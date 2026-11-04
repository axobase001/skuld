"""S5 episode runner: one (arm, world) episode on the real runtime, or a world-level qualification arm.

Each step is one loop round at the runtime's own cadence (wake_interval at TAU). Before a round, the world
advances to that hour, the user says what the world scripts (while present), and responsibilities that
fell due are closed by the user: completed when every item of theirs was met in time, cancelled when
not, which is the CONSEQUENCE label the SEC learner sees. After the round, the trace is read from the
receipt (decisions, prepared artifacts, executions) and the captured deliveries. While present, the
user gives the D7 feedback on each delivery (useful or not, by the hidden list).
The runner returns one ledger line with exactly the pre-registered keys. Nothing of the hidden list is
written under the episode's root."""
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from skuld.app.loop_runtime import wake_interval
from skuld.app.runtime import FORMAT_RETRY_REF, Runtime
from skuld.kernel.schemas import AuthorityClass, ModalState
from skuld.sim import arms, qual, score
from skuld.sim.env import Environment
from skuld.sim.providers import offline_config, real_config
from skuld.sim.user import ScriptedUser
from skuld.sim.world import EPISODE_HOURS, build

EPOCH = datetime(2026, 11, 2, tzinfo=timezone.utc)
TAU = 0.5
SPLIT_H = EPISODE_HOURS / 2
EXTRA_ARMS = {"C'": "C", "C_FROZEN": "C"}   # outside arms.ARMS: C' = C rerun (H5 noise baseline); C_FROZEN = C without feedback after SPLIT_H (H4)
PRICE_CNY_PER_M = (1.2, 3.0)        # effective prompt / completion price measured on the S4 runs (estimate, 未核实)
CNY_PER_USD = 7.1


class SimClock:
    def __init__(self):
        self.t = EPOCH

    def __call__(self):
        return self.t.isoformat()

    def at(self, h):
        self.t = EPOCH + timedelta(hours=h)


class CaptureNotifier:
    """The D8 notifier of the simulated user: keeps every delivery instead of sending mail."""
    config = {"timezone": "UTC"}

    def __init__(self):
        self.sent = []

    def deliver(self, message, urgency):
        self.sent.append({"subject": message["subject"], "body": message["body"], "urgency": urgency})
        return {"ok": True}


def ledger_line(arm, spec, hours, trace, labels, world, **counts):
    items = [i for i in world.items if i["deadline"] <= hours]
    line = {"arm": arm, "family": spec.family, "absence": spec.absence, "seed": spec.seed, "hours": hours,
            "regret": score.regret(items, trace), "interruption": score.interruption(items, trace),
            "brier_first": None, "brier_second": None, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0,
            "transport_ok": 1.0, "format_ok": 1.0, "wall_s": 0.0, "code_ref": os.environ.get("SKULD_CODE_REF", "unknown"),
            "actions": [list(x) for x in labels], "deliveries": sum(e["kind"] == "DELIVER" for e in trace),
            "proactive_actions": 0, "inferences": 0, "predictions": 0, "sec_updates": 0}
    line.update(counts)
    return line


def _bind(rt, u, r, cid, key, clock):
    src = rt.source(u, "UserConversation", f"resp-{r['key']}", {"text": r["text"]})
    content = {"key": r["key"], "kind": r["kind"], "text": r["text"], "value": None, "quote": r["text"], "modality": "intended",
               "status": "active", "actor": "USER", "replaces_id": None, "valid_from": None,
               "due_at": (EPOCH + timedelta(hours=r["due_at"])).isoformat()}
    node = rt.node(u, r["kind"], r["key"], content, src, modal=ModalState.INTENDED, authority=AuthorityClass.USER_FIRST_PERSON)
    beliefs = [n for n in rt.graph.nodes_by_identity(u, [cid + ":" + key]) if n.node_type == "Belief"]
    if beliefs:
        rt.hyper(u, "RESPONSIBILITY_OBSERVABLE_CONTEXT", [("responsibility", node), ("belief", max(beliefs, key=lambda n: (n.created_at, n.node_id)))], src)
    return node


def _close(rt, u, node, status):
    src = rt.source(u, "UserConversation", f"close-{node.semantic_identity_key}-{status}", {"text": f"{node.content['text']}（{status}）"})
    successor = rt.node(u, node.node_type, node.semantic_identity_key, {**node.content, "status": status, "replaces_id": node.node_id},
                        src, modal=node.modal_state, authority=AuthorityClass.USER_FIRST_PERSON)
    rt.edge(u, "SUPERSEDES", successor, node, src)


def _receipts(root):
    """Agent model calls from receipts.jsonl -> (calls, prompt, completion, transport_ok, format_ok)."""
    path = Path(root) / "receipts.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []
    rows = [r for r in rows if r.get("kind") == "provider_call" and r.get("status")]
    prompt = sum(int((r.get("usage") or {}).get("prompt_tokens") or 0) for r in rows)
    completion = sum(int((r.get("usage") or {}).get("completion_tokens") or 0) for r in rows)
    transport_bad = [r for r in rows if r.get("http_status") is None or int(r.get("http_status") or 0) >= 500 or r.get("http_status") == 429]
    answered = [r for r in rows if r not in transport_bad]
    # CHANGE_NOTE_12 (c): format validity per logical call - a call the product retried counts once, by its retry
    retried = {(r.get("stage"), str(r.get("logical_ref"))[:-len(FORMAT_RETRY_REF)]) for r in rows if str(r.get("logical_ref")).endswith(FORMAT_RETRY_REF)}
    final = [r for r in answered if (r.get("stage"), str(r.get("logical_ref"))) not in retried]
    valid = [r for r in final if r.get("status") == "VALID"]
    return (len(rows), prompt, completion, (len(rows) - len(transport_bad)) / len(rows) if rows else 1.0,
            len(valid) / len(final) if final else 1.0)


def run_episode(arm, spec, *, provider=None, hours=EPISODE_HOURS, root=None, paraphrase=None, max_calls=4000):
    """One episode -> one ledger line (LEDGER_KEYS). provider: a transport for the runtime (None: the real
    default route from the environment); paraphrase: the user's words (default: the intent itself)."""
    begin, world = time.monotonic(), build(spec)
    if arm in qual.WORLD_ARMS:
        trace, labels = qual.run(arm, world, hours)
        return ledger_line(arm, spec, hours, trace, labels, world, proactive_actions=len(labels), wall_s=round(time.monotonic() - begin, 2))
    holder = None
    if root is None:
        holder = tempfile.TemporaryDirectory(prefix="skuld-sim-")
        root = holder.name
    root = Path(root)
    clock, notifier = SimClock(), CaptureNotifier()
    base = EXTRA_ARMS.get(arm, arm)                  # C' (the noise rerun) and C_FROZEN run the C configuration
    config = {**(offline_config() if provider is not None else real_config(max_calls)), **arms.runtime_config(base)}
    rt = Runtime(root / "runtime", config, clock=clock, provider_transport=provider)
    env = Environment(world, root / "world", EPOCH)
    trace, labels, feedback_queue, closed, rounds_failed = [], [], [], set(), 0
    try:
        u = rt.provision("Sim user", True)["user_id"]
        rt.set_tau(u, TAU, "仿真用户设定的主动性温度")
        rt.notifier = notifier
        rt.configure_delivery(u, "user@sim.invalid", "以后有准备好的东西发到 user@sim.invalid")
        env.start()
        cids = env.add_channels(rt, u)
        rt.add_channel(u, "clock", {"stale_after_seconds": 86400})
        rt.add_channel(u, "conversation", {})
        for cid in cids.values():
            rt.observe_channel(u, cid)
        rt._last_loop_sample.clear()        # the baseline is not a change: rounds start from the observed world
        nodes = {r["key"]: _bind(rt, u, r, cids[r["object_id"]], world.setup[r["object_id"]]["key"], clock) for r in world.responsibilities}
        user = ScriptedUser(world, paraphrase)
        step, t = wake_interval(TAU) / 3600, 0.0
        while t + step <= hours + 1e-9:
            nt = round(t + step, 6)
            clock.at(nt)
            env.apply(nt)
            # CHANGE_NOTE_12 (a): the surface reports whether it sees the user; asleep is not there, like away
            rt.set_presence(u, "present" if world.state_at(nt) == "present" else "absent")
            for at, text in user.utterances(t, nt):     # intents exist only while the user is present
                try:
                    rt.chat(u, text, f"sim-utterance-{at}", "sim")
                except Exception:                # noqa: BLE001 - a failed turn is recorded in its receipt and does not stop the episode
                    pass
            for r in world.responsibilities:
                if r["key"] not in closed and r["due_at"] <= nt:
                    mine = [i for i in world.items if i["object_id"] == r["object_id"] and i["deadline"] <= r["due_at"]]
                    on_time = all(any(score.matches(i, e) and e["at_h"] <= i["deadline"] for e in trace) for i in mine)
                    _close(rt, u, nodes[r["key"]], "completed" if on_time else "cancelled")
                    closed.add(r["key"])
            sent_before = len(notifier.sent)
            try:
                receipt = rt.loop_round(u, "SCHEDULED").content
            except Exception:                    # noqa: BLE001 - the receipt of a failed round is committed before the error
                rounds_failed += 1
                receipt = next((n.content for n in rt.graph.nodes_by_identity(u, [f"loop:{(rt._loop_row(u) or {'last_round': 0})['last_round']}"])
                                if n.node_type == "LoopReceipt"), {"decisions": [], "actions": []})
            for d in receipt.get("decisions", []):
                labels.append([d["kind"], int(d["level"])])
            actions = receipt.get("actions", [])
            artifacts = rt.graph.nodes_by_ids(u, [a for a in actions if a.startswith("preparedartifact")])
            executed = any(a.startswith("action_outcome") for a in actions)
            for d in receipt.get("decisions", []):
                if d["level"] < 2:
                    continue
                for a in artifacts:                    # every artifact a decision prepared (an L3 proposal included)
                    text = a.content.get("content") or a.content.get("draft") or ""
                    trace.append({"at_h": nt, "kind": "PREPARE", "level": 2, "text": text})
                    if d["kind"] == "EXECUTE" and d["level"] == 4 and executed:
                        trace.append({"at_h": nt, "kind": "EXECUTE", "level": 4, "text": text})
            sent, refs, works = notifier.sent[sent_before:], [], {}
            if sent:     # CHANGE_NOTE_12 (a): a released delivery belongs to the decision that prepared it, not to this round's
                rows = {d["delivery_id"]: d for d in rt.deliveries(u)}
                refs = [rows[i]["item_ref"] for i in receipt.get("deliveries", []) if i in rows and rows[i]["status"] == "SENT"]
                works = {n.content.get("artifact_node_id"): n.content.get("decision_id") for n in rt.nodes(u, "ProductWork")}
            for i, m in enumerate(sent):
                entry = {"at_h": nt, "kind": "DELIVER", "level": 0, "text": m["body"]}
                trace.append(entry)
                decision = works.get(refs[i]) if i < len(refs) else None
                if decision:
                    feedback_queue.append((decision, entry))
            if arm == "C_FROZEN" and nt >= SPLIT_H:   # H4: the frozen policy learns nothing in the second half
                feedback_queue = []
            if world.state_at(nt) == "present":
                for decision, entry in feedback_queue:
                    try:
                        rt.product.world.feedback(u, decision, user.feedback(entry) == "useful", "仿真用户的反馈", f"sim-feedback-{decision}")
                    except Exception:            # noqa: BLE001 - feedback the calibrator refuses is not retried
                        pass
                feedback_queue = []
            t = nt
        settlements = rt.nodes(u, "ForecastSettlement")
        forecasts = []
        for s in settlements:
            p = rt.graph.get_node(u, s.content["prediction_id"])
            if s.content.get("status") == "RESOLVED" and p is not None and p.content.get("probability") is not None:
                at = (datetime.fromisoformat(p.created_at) - EPOCH).total_seconds() / 3600
                forecasts.append((at, float(p.content["probability"]), 1.0 if s.content.get("outcome") else 0.0))
        b1, b2 = score.brier(forecasts, SPLIT_H)
        all_nodes = rt.nodes(u)
        inferences = sum(n.authority_class == AuthorityClass.SYSTEM_INFERRED for n in all_nodes)
        predictions = sum(n.node_type == "Prediction" and (n.content.get("kind") == "inference_contract"
                          or str((n.content.get("observable") or {}).get("modeled_subject_id", "")).startswith("channel_")) for n in all_nodes)
        calls, prompt, completion, transport_ok, format_ok = _receipts(root / "runtime")
        if provider is not None and hasattr(provider, "requests"):
            calls = len(provider.requests)
        extra_p = getattr(paraphrase, "prompt_tokens", 0) if paraphrase is not None and provider is None else 0
        extra_c = getattr(paraphrase, "completion_tokens", 0) if paraphrase is not None and provider is None else 0
        cost = ((prompt + extra_p) * PRICE_CNY_PER_M[0] + (completion + extra_c) * PRICE_CNY_PER_M[1]) / 1e6 / CNY_PER_USD
        return ledger_line(arm, spec, hours, trace, labels, world, brier_first=b1, brier_second=b2, calls=calls, prompt_tokens=prompt,
                           completion_tokens=completion, cost_usd=round(cost, 5), transport_ok=round(transport_ok, 4), format_ok=round(format_ok, 4),
                           wall_s=round(time.monotonic() - begin, 2), proactive_actions=sum(1 for x in labels if x[1] >= 2),
                           inferences=inferences, predictions=predictions, sec_updates=sum(n.node_type == "SecUpdate" for n in all_nodes))
    finally:
        rt.close()
        env.close()
        if holder is not None:
            holder.cleanup()


__all__ = ["run_episode", "EPOCH", "TAU"]
