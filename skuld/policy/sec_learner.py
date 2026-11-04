"""D12 SEC post-hoc learner. Imported only when config `sec_learner` is true, so with it off the
runtime behaves byte for byte as if it did not exist.

Reads, after each loop round has committed: the round's receipt (its decisions are the actions),
the closures of the responsibilities those actions served (CONSEQUENCE), and the owner's explicit
feedback (AGENCY_CALIBRATION). WORLD_CORRECTION inputs are only counted in S3. Writes only two
things, each as a SecUpdate node with absolute before/after values on a hash chain:

* the calibration offset of a (kind, risk_class, origin) bucket: delta = clip(rate * (label -
  frozen probability), +-step_max), offset within +-abs_max;
* the tau suggestion: +-tau_step per explicit helpful / unhelpful answer, within +-tau_span of the
  user's tau. It becomes tau only when the user accepts it.

CONSEQUENCE labels (Claude's GATE_S3 review ①) hang on the responsibility an action of level 2-4
served: completed inside the window -> 1; cancelled, or the window over without completion
evidence -> 0; otherwise no label yet. The window ends at the responsibility's due_at when that is
after the action, else WINDOW_SECONDS after it. One closure gives at most one label per bucket, on
that bucket's latest action. Execution status is not a learning signal.

A rollback writes a SecRollback node and restores the exact state at an earlier seq; nothing is
deleted. SEC never chooses actions and never reaches the ladder."""
import hashlib
import json
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from skuld.kernel.primitives import instant
from skuld.kernel.schemas import AuthorityClass, SubjectNode

RULE_VERSION = "sec-posthoc/1 (GATE_S3)"
OFFSET_RATE, OFFSET_STEP_MAX, OFFSET_ABS_MAX = 0.05, 0.05, 0.25
TAU_STEP, TAU_SPAN = 0.02, 0.10
WINDOW_SECONDS = 7 * 86400
LABEL_LEVELS = (2, 3, 4)
RESPONSIBILITY_TYPES = ("Goal", "Task", "Commitment")
DDL = """CREATE TABLE IF NOT EXISTS runtime_sec_pending(
    user_id TEXT NOT NULL, decision_id TEXT NOT NULL, responsibility_id TEXT NOT NULL, bucket TEXT NOT NULL,
    probability REAL NOT NULL, action_at TEXT NOT NULL, window_end TEXT NOT NULL,
    PRIMARY KEY(user_id, decision_id, responsibility_id));
CREATE TABLE IF NOT EXISTS runtime_sec_seen(user_id TEXT NOT NULL, node_id TEXT NOT NULL, PRIMARY KEY(user_id, node_id));
CREATE TABLE IF NOT EXISTS runtime_sec_state(user_id TEXT PRIMARY KEY, last_seq INTEGER NOT NULL, state_json TEXT NOT NULL, feedback_after TEXT);
CREATE TABLE IF NOT EXISTS runtime_sec_log(user_id TEXT NOT NULL, seq INTEGER NOT NULL, receipt_id TEXT NOT NULL, node_id TEXT NOT NULL, PRIMARY KEY(user_id, seq))"""


def q(x):
    return float(Decimal(str(x)).quantize(Decimal("1e-9"), rounding=ROUND_HALF_EVEN))


def clamp(x, lo, hi):
    return min(hi, max(lo, x))


def state_hash(state):
    body = {"offsets": dict(sorted(state["offsets"].items())), "tau_suggestion": state["tau_suggestion"]}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def initial():
    s = {"offsets": {}, "tau_suggestion": None, "seq": 0}
    s["state_hash"] = state_hash(s)
    return s


class SecLearner:
    def __init__(self, runtime):
        self.j = runtime
        with runtime.store.transaction():
            for statement in DDL.split(";"):
                runtime.store._conn.execute(statement)

    # state
    def _events(self, u):
        rows = self.j.nodes(u, "SecUpdate") + self.j.nodes(u, "SecRollback")
        return sorted(rows, key=lambda n: n.content["seq"])

    def _fold(self, u):
        """-> (current state, last global seq, {seq: state})."""
        state, at, last = initial(), {0: initial()}, 0
        for n in self._events(u):
            c = n.content
            if n.node_type == "SecUpdate":
                state = {"offsets": dict(state["offsets"]), "tau_suggestion": state["tau_suggestion"]}
                if c["field"] == "calibration_offset":
                    state["offsets"][c["scope"]] = c["after"]
                else:
                    state["tau_suggestion"] = c["after"]
                state["seq"], state["state_hash"] = c["seq"], c["state_hash"]
            else:
                state = json.loads(json.dumps(at[c["to_seq"]]))
            at[c["seq"]], last = state, c["seq"]
        return state, last, at

    def _cached(self, u):
        """-> (current state, last global seq): S5 keeps the folded state, so a round does not replay every update."""
        row = self.j.store._conn.execute("SELECT last_seq,state_json FROM runtime_sec_state WHERE user_id=?", (u,)).fetchone()
        return (json.loads(row["state_json"]), row["last_seq"]) if row else (initial(), 0)

    def _keep(self, u, last, state):
        self.j.store._conn.execute("INSERT INTO runtime_sec_state(user_id,last_seq,state_json) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                                   "last_seq=excluded.last_seq,state_json=excluded.state_json", (u, last, json.dumps(state, sort_keys=True)))

    def state(self, u):
        return self._cached(u)[0]

    def update_ids(self, u, receipt_id):
        """SecUpdate node ids written while learning from one receipt, in seq order."""
        return [r[0] for r in self.j.store._conn.execute("SELECT node_id FROM runtime_sec_log WHERE user_id=? AND receipt_id=? ORDER BY seq", (u, receipt_id))]

    def offset(self, u, bucket):
        return self.state(u)["offsets"].get(bucket, 0.0)

    # writes
    def _write(self, u, last, state, field, scope, before, after, bound, receipt_id, input_ids):
        seq = last + 1
        new = {"offsets": dict(state["offsets"]), "tau_suggestion": state["tau_suggestion"]}
        if field == "calibration_offset":
            new["offsets"][scope] = after
        else:
            new["tau_suggestion"] = after
        content = {"seq": seq, "field": field, "scope": scope, "before": before, "after": after, "delta": q(after - before),
                   "bound": bound, "rule_version": RULE_VERSION, "receipt_id": receipt_id, "input_ids": list(input_ids),
                   "prev_state_hash": state["state_hash"], "state_hash": state_hash(new)}
        src = self.j.source(u, "SecEvaluation", f"{receipt_id}:{seq}", {"receipt_id": receipt_id, "field": field, "scope": scope})
        node = self.j.node(u, "SecUpdate", f"sec:{seq}", content, src)
        new["seq"], new["state_hash"] = seq, content["state_hash"]
        self._keep(u, seq, new)
        self.j.store._conn.execute("INSERT INTO runtime_sec_log VALUES(?,?,?,?)", (u, seq, receipt_id, node.node_id))
        return seq, new

    def rollback(self, u, to_seq, source):
        state, last, at = self._fold(u)
        if isinstance(to_seq, bool) or not isinstance(to_seq, int) or to_seq not in at:
            raise ValueError("rollback target must be 0 or an existing SEC seq")
        content = {"seq": last + 1, "to_seq": to_seq, "restored_state_hash": at[to_seq]["state_hash"], "requested_by": source.source_event_id}
        with self.j.store.transaction():
            node = self.j.node(u, "SecRollback", f"sec:{last + 1}", content, source)
            self._keep(u, last + 1, json.loads(json.dumps(at[to_seq])))
        return node

    # learning
    def learn(self, u, receipt):
        """Process one committed LoopReceipt. -> node ids of the SecUpdates written."""
        conn, now = self.j.store._conn, instant(receipt.content["finished_at"])
        written = []
        with self.j.store.transaction():
            state, last = self._cached(u)
            for d in receipt.content["decisions"]:
                if d.get("level") not in LABEL_LEVELS or d.get("probability") is None or not d.get("decision_id"):
                    continue
                for rid in d.get("goal_ids") or []:
                    r = self.j.graph.get_node(u, rid)
                    if not r or r.node_type not in RESPONSIBILITY_TYPES:
                        continue
                    due = r.content.get("due_at")
                    end = instant(due) if due and instant(due) > now else now + timedelta(seconds=WINDOW_SECONDS)
                    conn.execute("INSERT OR IGNORE INTO runtime_sec_pending VALUES(?,?,?,?,?,?,?)",
                                 (u, d["decision_id"], rid, d["bucket"], float(d["probability"]), now.isoformat(), end.isoformat()))
            groups = {}
            for row in map(dict, conn.execute("SELECT * FROM runtime_sec_pending WHERE user_id=?", (u,)).fetchall()):
                end = instant(row["window_end"])
                closure = self._closure(u, self.j.graph.get_node(u, row["responsibility_id"]), instant(row["action_at"]))
                if closure is not None and instant(closure.created_at) <= min(now, end):
                    label, key = (1 if closure.content.get("status") == "completed" else 0), closure.node_id
                elif now >= end:
                    label, key, closure = 0, "window-end", None
                else:
                    continue
                groups.setdefault((row["responsibility_id"], key, row["bucket"]), (label, closure, []))[2].append(row)
            ordered = sorted(groups.items(), key=lambda kv: max((r["action_at"], r["decision_id"]) for r in kv[1][2]))
            for (rid, _, bucket), (label, closure, rows) in ordered:
                for r in rows:
                    conn.execute("DELETE FROM runtime_sec_pending WHERE user_id=? AND decision_id=? AND responsibility_id=?",
                                 (u, r["decision_id"], r["responsibility_id"]))
                latest = max(rows, key=lambda r: (r["action_at"], r["decision_id"]))
                before = state["offsets"].get(bucket, 0.0)
                delta = clamp(OFFSET_RATE * (label - latest["probability"]), -OFFSET_STEP_MAX, OFFSET_STEP_MAX)
                after = q(clamp(before + delta, -OFFSET_ABS_MAX, OFFSET_ABS_MAX))
                if after == before:
                    continue
                inputs = [latest["decision_id"], rid] + ([closure.node_id] if closure is not None else [])
                last, state = self._write(u, last, state, "calibration_offset", bucket, before, after,
                                          {"rate": OFFSET_RATE, "step_max": OFFSET_STEP_MAX, "abs_max": OFFSET_ABS_MAX},
                                          receipt.node_id, inputs)
                written.append(f"sec:{last}")
            seen = {r[0] for r in conn.execute("SELECT node_id FROM runtime_sec_seen WHERE user_id=?", (u,))}
            tau = self.j.tau(u)
            seen_after = (conn.execute("SELECT feedback_after FROM runtime_sec_state WHERE user_id=?", (u,)).fetchone() or [None])[0]
            fresh = [SubjectNode.from_dict(json.loads(r[0])) for r in conn.execute(
                "SELECT data_json FROM subject_nodes_v200 WHERE user_id=? AND node_type='ProactiveFeedback' AND (? IS NULL OR "
                "datetime(json_extract(data_json,'$.created_at'))>=datetime(?))", (u, seen_after, seen_after)).fetchall()]
            for fb in sorted(fresh, key=lambda n: (instant(n.created_at), n.node_id)):  # S5: only feedback since the last one seen
                if fb.node_id in seen or instant(fb.created_at) > now:
                    continue
                seen_after = fb.created_at if seen_after is None or instant(fb.created_at) > instant(seen_after) else seen_after
                conn.execute("INSERT OR IGNORE INTO runtime_sec_seen VALUES(?,?)", (u, fb.node_id))
                helpful = fb.content.get("helpful")
                if not isinstance(helpful, bool):
                    continue
                before = state["tau_suggestion"] if state["tau_suggestion"] is not None else tau
                after = q(clamp(before + (TAU_STEP if helpful else -TAU_STEP), max(0.0, tau - TAU_SPAN), min(1.0, tau + TAU_SPAN)))
                if after == before:
                    continue
                last, state = self._write(u, last, state, "tau_suggestion", "tau", before, after,
                                          {"step": TAU_STEP, "span": TAU_SPAN, "tau": tau}, receipt.node_id, [fb.node_id])
                written.append(f"sec:{last}")
            if seen_after is not None:
                self._keep(u, last, state)
                conn.execute("UPDATE runtime_sec_state SET feedback_after=? WHERE user_id=?", (seen_after, u))
        return self.update_ids(u, receipt.node_id)

    def _closure(self, u, r, after):
        """The first USER record that completed or cancelled this responsibility after `after`."""
        if r is None:
            return None
        found = [n for n in self.j.graph.nodes_by_identity(u, [r.semantic_identity_key])
                 if n.node_type == r.node_type and n.authority_class == AuthorityClass.USER_FIRST_PERSON
                 and n.content.get("status") in ("completed", "cancelled") and instant(n.created_at) >= after]
        return min(found, key=lambda n: (instant(n.created_at), n.node_id), default=None)


__all__ = ["SecLearner", "RULE_VERSION", "initial", "state_hash"]
