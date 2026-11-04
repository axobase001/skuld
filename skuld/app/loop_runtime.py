"""S3 runtime (GATE_S3): the ten-stage loop with its receipt and scheduler (D5), the ladder and
mandates (D6), tau (D7), delivery (D8), the policy hook and the optional SEC learner (D12).

Mixed into Runtime. Every stage works through the runtime's graph helpers; the only state kept
outside the graph is operational (round counter and wake time, last channel sample, delivery log),
in runtime_* tables, like the job queue."""
from __future__ import annotations

import json
import threading
from skuld.channels.base import ReadBudgetExhausted
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from skuld.delivery.notifier import SmtpNotifier
from skuld.kernel.primitives import dumps, instant, nid
from skuld.kernel.schemas import AuthorityClass, ModalState, canonical_hash
from skuld.loop import ladder
from skuld.policy.policies import POLICIES as POLICY_TYPES

LOOP_STAGES = ("WAKE", "PREDICT", "SELECT", "OBSERVE", "PE", "UPDATE", "REASON", "GOALS", "ACT", "REFLECT")
WAKES = ("SCHEDULED", "CHANNEL_EVENT", "MANUAL")
TAU_DEFAULT = 0.5
WAKE_MAX_SECONDS = 3600
SELECT_MAX_CHANNELS = 4
SESSION_SECONDS = 8 * 3600
APPROVAL_TTL_SECONDS = 900
MANDATE_LEVELS = ("SESSION_ONLY", "STANDING")
MANDATE_SOURCES = {"USER_INSTRUCTION": "UserMandateInstruction", "ONBOARDING": "OnboardingBoundary"}
NON_URGENT_DAILY_MAX, URGENT_DAILY_MAX, URGENT_HOURS = 3, 2, 24
DAILY_DELIVERY_MAX = 3  # CHANGE_NOTE_11: all urgency buckets share the daily total
PRESENCE_STATES = ("present", "absent")   # CHANGE_NOTE_12 (a); unknown (None) keeps the presence-blind S3 behaviour
STALENESS_WEIGHT, STALENESS_TERM_MAX = 0.05, 0.15
POLLED_KINDS = ("http_json", "ics_calendar", "local_directory", "web_page")
EVENT_KINDS = ("conversation", "clock")
RESPONSIBILITY_TYPES = ("Goal", "Task", "Commitment")
LOOP_DDL = (
    "CREATE TABLE IF NOT EXISTS runtime_channel_attempts(user_id TEXT NOT NULL,channel_id TEXT NOT NULL,last_attempt TEXT NOT NULL,consecutive_failures INTEGER NOT NULL DEFAULT 0,next_attempt TEXT,PRIMARY KEY(user_id,channel_id))",
    "CREATE TABLE IF NOT EXISTS runtime_loop(user_id TEXT PRIMARY KEY,last_round INTEGER NOT NULL,last_finished TEXT,wake_due TEXT)",
    "CREATE TABLE IF NOT EXISTS runtime_channel_samples(channel_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,sample_json TEXT NOT NULL,sampled_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS runtime_deliveries(delivery_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,item_ref TEXT NOT NULL,kind TEXT NOT NULL,"
    "urgency TEXT NOT NULL,status TEXT NOT NULL,subject TEXT NOT NULL,at TEXT NOT NULL,day TEXT NOT NULL,round INTEGER NOT NULL,error TEXT)",
    "CREATE INDEX IF NOT EXISTS runtime_deliveries_day ON runtime_deliveries(user_id,day,urgency,status)",
    # S5: what is live, so a round does not read the whole history (GATE_S5 precondition)
    "CREATE TABLE IF NOT EXISTS runtime_live_responsibilities(user_id TEXT NOT NULL,node_id TEXT NOT NULL,node_type TEXT NOT NULL,identity_key TEXT NOT NULL,PRIMARY KEY(user_id,node_id))",
    "CREATE INDEX IF NOT EXISTS runtime_live_responsibilities_identity ON runtime_live_responsibilities(user_id,identity_key,node_type)",
    "CREATE TABLE IF NOT EXISTS runtime_bindings(user_id TEXT NOT NULL,channel_id TEXT NOT NULL,key TEXT NOT NULL,responsibility_id TEXT NOT NULL,hyperedge_id TEXT NOT NULL,PRIMARY KEY(user_id,hyperedge_id,responsibility_id))",
    "CREATE INDEX IF NOT EXISTS runtime_bindings_key ON runtime_bindings(user_id,channel_id,key)",
    "CREATE TABLE IF NOT EXISTS runtime_inference_watch(user_id TEXT NOT NULL,channel_id TEXT,key TEXT,PRIMARY KEY(user_id,channel_id,key))",
    "CREATE TABLE IF NOT EXISTS runtime_reminders(user_id TEXT NOT NULL,responsibility_id TEXT NOT NULL,hours REAL NOT NULL,PRIMARY KEY(user_id,responsibility_id,hours))",
    "CREATE TABLE IF NOT EXISTS runtime_pending_forecasts(user_id TEXT NOT NULL,prediction_id TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(user_id,prediction_id))",
    # CHANGE_NOTE_12 (a): a delivery decided while the user is present waits here for their first absent round
    "CREATE TABLE IF NOT EXISTS runtime_held_deliveries(delivery_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,item_json TEXT NOT NULL,goal_ids_json TEXT NOT NULL,held_at TEXT NOT NULL)",
)


def wake_interval(tau):
    return round(WAKE_MAX_SECONDS / (1 + 3 * tau))


def severity(hours):
    """PE severity of a change on an observable bound to an active responsibility (same table as
    the relevance in attention). Overdue counts as within 24 hours."""
    if hours is not None and hours <= 24:
        return 1.0
    if hours is not None and hours <= 168:
        return 0.6
    return 0.4


class LoopMixin:
    def _loop_setup(self):
        self._read_rounds={}
        self._read_context=threading.local()
        with self.store.transaction():
            for ddl in LOOP_DDL:
                self.store._conn.execute(ddl)
        self._product_calls = 0
        self._last_loop_sample = {}
        self._presence = {}
        policy = self.config.get("policy", "rule")
        if policy not in POLICY_TYPES:
            raise ValueError("policy must be one of " + ", ".join(POLICY_TYPES))
        self.policy = POLICY_TYPES[policy]()
        self.notifier = SmtpNotifier(self.config["delivery"]) if self.config.get("delivery") else None
        self.sec = None
        if self.config.get("sec_learner"):
            from skuld.policy.sec_learner import SecLearner  # D12: SEC off never imports it
            self.sec = SecLearner(self)

    POLICIES = tuple(POLICY_TYPES)

    # D7 tau
    def _tau_rows(self, u):
        return [n for n in self.graph.nodes_by_identity(u, ["tau-setting"]) if n.node_type == "TauSetting"]

    def tau(self, u):
        rows = self._tau_rows(u)
        return max(rows, key=lambda n: n.content["seq"]).content["tau"] if rows else TAU_DEFAULT

    def set_tau(self, u, tau, instruction):
        if isinstance(tau, bool) or not isinstance(tau, (int, float)) or not 0 <= tau <= 1:
            raise ValueError("tau must be between 0 and 1")
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("tau is set by the user's explicit instruction")
        with self.lock:
            self.meta(u)
            seq = len(self._tau_rows(u)) + 1
            with self.store.transaction():
                src = self.source(u, "UserTauSetting", f"tau:{seq}", {"tau": float(tau), "instruction": instruction})
                self.node(u, "TauSetting", "tau-setting", {"tau": float(tau), "seq": seq, "instruction": instruction}, src,
                          authority=AuthorityClass.USER_FIRST_PERSON)
        return float(tau)

    def accept_tau_suggestion(self, u, instruction):
        if self.sec is None:
            raise ValueError("the SEC learner is off; there is no tau suggestion")
        suggestion = self.sec.state(u)["tau_suggestion"]
        if suggestion is None:
            raise ValueError("no tau suggestion yet")
        return self.set_tau(u, suggestion, instruction)

    def sec_state(self, u):
        if self.sec is None:
            raise ValueError("the SEC learner is off")
        return self.sec.state(u)

    def sec_rollback(self, u, to_seq):
        if self.sec is None:
            raise ValueError("the SEC learner is off")
        with self.lock:
            seq = len(self.nodes(u, "SecRollback")) + 1
            with self.store.transaction():
                src = self.source(u, "UserSecRollback", f"rollback:{seq}:{to_seq}", {"to_seq": to_seq})
                return self.sec.rollback(u, to_seq, src).node_id

    # D6 mandates
    def _operation_risk(self, u, channel_id, operation):
        if channel_id == "delivery":
            if operation != "SEND":
                raise ValueError("the delivery channel's only operation is SEND")
            return 0
        ops = self.channel(u, channel_id).operations()
        if operation == "READ" or operation not in ops:
            raise ValueError("the channel declares no write operation " + str(operation))
        return ops[operation]

    def grant_mandate(self, u, channel_id, operation, level="SESSION_ONLY", *, origin, instruction, expires_at=None, _content=None):
        if level not in MANDATE_LEVELS:
            raise ValueError("mandate level must be SESSION_ONLY or STANDING")
        if origin not in MANDATE_SOURCES:
            raise ValueError("a mandate comes only from the user's explicit instruction or an onboarding answer")
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("a mandate needs the user's own words")
        with self.lock:
            risk = self._operation_risk(u, channel_id, operation)
            now = instant(self.clock())
            end = instant(expires_at) if expires_at else None
            if level == "SESSION_ONLY":
                end = min(end, now + timedelta(seconds=SESSION_SECONDS)) if end else now + timedelta(seconds=SESSION_SECONDS)
            if end is not None and end <= now:
                raise ValueError("a mandate must expire in the future")
            seq = len(self.nodes(u, "Mandate")) + 1
            scope = {"channel_id": channel_id, "operation": operation, "risk_class": risk}
            with self.store.transaction():
                src = self.source(u, MANDATE_SOURCES[origin], f"mandate:{seq}", {"scope": scope, "level": level, "instruction": instruction})
                node = self.node(u, "Mandate", f"mandate:{seq}", {"scope": scope, "level": level, "origin": origin, "instruction": instruction,
                                 "expires_at": end.isoformat() if end else None, "source": src.source_event_id, **(_content or {})},
                                 src, authority=AuthorityClass.USER_FIRST_PERSON)
            return node.node_id

    def _live_mandates(self, u):
        now = instant(self.clock())
        out = []
        for n in self.nodes(u, "Mandate"):
            if n.content["expires_at"] and instant(n.content["expires_at"]) <= now:
                continue
            if any(e.relation_type == "WITHDRAWS" for e in self.graph.list_edges(u, target_node_id=n.node_id, status="ACTIVE", limit=10)):
                continue
            out.append(n)
        return out

    def mandates(self, u):
        with self.lock:
            return [{"mandate_id": n.node_id, "scope": dict(n.content["scope"]), "level": n.content["level"],
                     "expires_at": n.content["expires_at"], "source": n.content["source"]} for n in self._live_mandates(u)]

    def mandate_for(self, u, channel_id, operation):
        """The live mandate covering this operation now (checked at dispatch time), or None."""
        return next((n for n in self._live_mandates(u) if n.content["scope"]["channel_id"] == channel_id
                     and n.content["scope"]["operation"] == operation), None)

    def revoke_mandate(self, u, mandate_id):
        with self.lock:
            n = self.graph.get_node(u, mandate_id)
            if not n or n.node_type != "Mandate":
                raise KeyError("unknown mandate")
            with self.store.transaction():
                src = self.source(u, "UserMandateRevocation", mandate_id, {"mandate_id": mandate_id})
                mark = self.node(u, "Revocation", "revoke:" + mandate_id, {"mandate_id": mandate_id}, src, authority=AuthorityClass.USER_FIRST_PERSON)
                self.edge(u, "WITHDRAWS", mark, n, src)
            return mark.node_id

    def configure_delivery(self, u, address, instruction):
        """Onboarding's delivery address: itself the delivery channel's STANDING mandate (D8)."""
        if self.notifier is None:
            raise ValueError("no delivery transport is configured")
        if not isinstance(address, str) or address.count("@") != 1 or any(ch.isspace() for ch in address):
            raise ValueError("a single e-mail address is required")
        with self.lock, self.store.transaction():
            for old in self._live_mandates(u):
                scope = old.content['scope']
                if scope['channel_id'] == 'delivery' and scope['operation'] == 'SEND':
                    self.revoke_mandate(u, old.node_id)
            return self.grant_mandate(u, 'delivery', 'SEND', 'STANDING', origin='ONBOARDING', instruction=instruction,
                                      _content={'address': address})

    # channel samples (S3: an unchanged sample adds no node)
    def _loop_sample(self, u, cid, ch, observations):
        conn = self.store._conn
        row = conn.execute("SELECT sample_json FROM runtime_channel_samples WHERE channel_id=?", (cid,)).fetchone()
        prior = json.loads(row["sample_json"]) if row else None
        at = self.clock()
        current = {o["key"]: o["value"] for o in observations}
        facts = [{"key": o["key"], "value": o["value"], "text": o["provenance"].get("text") or o["key"] + ": " + dumps(o["value"]),
                  "quote": o["provenance"].get("quote"), "valid_from": o["effective_at"]} for o in observations]
        report=current.get('report')
        try:report=json.loads(report) if isinstance(report,str) else {}
        except ValueError:report={}
        object_id=current.get('object_id') or (report.get('object') if isinstance(report,dict) else None)
        prior_report=prior.get('report') if prior else None
        try:prior_report=json.loads(prior_report) if isinstance(prior_report,str) else {}
        except ValueError:prior_report={}
        prior_obj=(prior.get('object_id') or (prior_report.get('object') if isinstance(prior_report,dict) else None)) if prior else None
        changed = [o for o in observations if object_id and object_id!=prior_obj or prior is None or o["key"] not in prior or prior[o["key"]] != o["value"]]
        source_row = self._source_row(u, cid)
        changes = []
        with self.store.transaction():
            content = {"url": source_row["url"], "source_id": cid, "name": source_row["name"], "data": {"facts": facts}, "body_hash": canonical_hash(facts),
                       "simulation": self.owner(u).content["simulation"], "fresh": True, "observation_source": ch.kind + "_channel",
                       "observed_at": at, "changed_keys": [o["key"] for o in changed], "object_id":object_id,"object_version":report.get("version") if isinstance(report,dict) else None}

            sample = self.source(u, "ExternalObservation", f"{cid}:{at}:{canonical_hash([prior, current])[:16]}", content, at=at)
            for o in changed:
                key = cid + ":" + o["key"]
                field = next(f for f in facts if f["key"] == o["key"])
                payload = {**field, "object_id":object_id,"object_version":content["object_version"], "source_id": cid, "status": "active", "epistemic_status": "SOURCE_REPORT", "observation_source": content["observation_source"],
                           "observed_at": at, "historical_correction": False, "fresh": True}
                fact = self.node(u, "Fact", "fact:" + key, payload, sample, valid=field["valid_from"], authority=AuthorityClass.TOOL_OBSERVED, observed=True)
                previous = next((n for n in self.graph.nodes_by_identity(u, [key]) if n.node_type == "Belief" and self.active(u, n)), None)
                belief = self.node(u, "Belief", key, {**payload, "fact_id": fact.node_id}, sample, valid=field["valid_from"])
                self.edge(u, "SUPPORTED_BY", belief, fact, sample)
                if previous is not None:
                    revision = self.node(u, "Revision", "revision:" + belief.node_id, {"predecessor_node_ids": [previous.node_id], "successor_node_ids": [belief.node_id],
                                         "reason": "fresh source sample", "effective_at": field["valid_from"], "recorded_at": at}, sample, valid=field["valid_from"])
                    self.edge(u, "SUPERSEDES", revision, previous, sample)
                    self.edge(u, "CREATES", revision, belief, sample)
                if prior is not None:
                    changes.append({"channel_id": cid, "key": o["key"], "old": prior.get(o["key"]), "new": o["value"], "belief_id": belief.node_id})
            conn.execute("INSERT INTO runtime_channel_samples VALUES(?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET sample_json=excluded.sample_json,sampled_at=excluded.sampled_at",
                         (cid, u, dumps(current), at))
        self._last_loop_sample[(u, cid)] = {"source_id": sample.source_event_id, "changes": changes}

    # attention (S2 §5.2 with the S3 staleness cap)
    def _bound(self, u, source_id, key):
        """[(active responsibility, hours to due or None)] bound to the report source_id:key. S5: through the
        binding and live-responsibility indexes, so closed responsibilities and old belief versions are not read."""
        now, out = instant(self.clock()), []
        rows = self.store._conn.execute("SELECT DISTINCT b.responsibility_id FROM runtime_bindings b JOIN runtime_live_responsibilities l "
                                        "ON l.user_id=b.user_id AND l.node_id=b.responsibility_id WHERE b.user_id=? AND b.channel_id=? AND b.key=? "
                                        "ORDER BY b.responsibility_id", (u, source_id, key)).fetchall()
        for r in sorted(self.graph.nodes_by_ids(u, [row[0] for row in rows]), key=lambda n: n.node_id):
            if not self._responsibility_live(u, r) or not self.responsibility_available(u,r.node_id):
                continue
            due = r.content.get("due_at")
            out.append((r, (instant(due) - now).total_seconds() / 3600 if due else None))
        return out

    def _watched_keys(self, u, cid):
        """Keys of one channel that can carry attention: bound to a live responsibility, or watched by an inference."""
        return {r[0] for r in self.store._conn.execute(
            "SELECT b.key FROM runtime_bindings b JOIN runtime_live_responsibilities l ON l.user_id=b.user_id AND l.node_id=b.responsibility_id "
            "WHERE b.user_id=? AND b.channel_id=? UNION SELECT key FROM runtime_inference_watch WHERE user_id=? AND channel_id=?", (u, cid, u, cid))}

    def _reminders(self, u):
        """S5 arm B: one REMINDER prediction error per live responsibility and reminder_hours entry, once."""
        hours = self.config.get("reminder_hours") or []
        if not hours:
            return []
        now, out = instant(self.clock()), []
        rows = self.store._conn.execute("SELECT node_id FROM runtime_live_responsibilities WHERE user_id=? ORDER BY node_id", (u,)).fetchall()
        for r in self.graph.nodes_by_ids(u, [row[0] for row in rows]):
            due = r.content.get("due_at")
            if not due or instant(due) <= now or not self._responsibility_live(u, r):
                continue
            for h in hours:
                if instant(due) - timedelta(hours=h) <= now and not self.store._conn.execute(
                        "SELECT 1 FROM runtime_reminders WHERE user_id=? AND responsibility_id=? AND hours=?", (u, r.node_id, h)).fetchone():
                    with self.store.transaction():
                        self.store._conn.execute("INSERT INTO runtime_reminders VALUES(?,?,?)", (u, r.node_id, h))
                    out.append({"channel_id": None, "key": f"reminder:{r.node_id}:{h}", "kind": "REMINDER", "belief_id": None,
                                "severity": 1.0, "responsibility_ids": [r.node_id]})
        return out

    def _responsibility_live(self, u, r):
        if self.responsibility_state(u,r.node_id)["status"]=="COMPLETED":return False
        if not self.active(u, r):
            return False
        later = [n for n in self.graph.nodes_by_identity(u, [r.semantic_identity_key]) if n.node_type == r.node_type
                 and n.authority_class == AuthorityClass.USER_FIRST_PERSON and instant(n.created_at) >= instant(r.created_at) and n.node_id != r.node_id]
        return not any(n.content.get("status") in ("completed", "cancelled") for n in later)

    def attention(self, u, source_id, key):
        """Deterministic attention for one observable: relevance of bound responsibilities (by
        urgency) plus the uncertainty of inferences that watch it and their staleness, the staleness
        term capped at STALENESS_TERM_MAX. Reads no prompt and calls no model."""
        relevance = max((severity(h) for _, h in self._bound(u, source_id, key)), default=0.0)
        watch = 0.0
        for p in self.graph.nodes_by_identity_prefix(u, "inference_contract:" + source_id + ":" + key + ":"):
            spec = p.content.get("contract", {})
            if spec.get("channel_id") != source_id or spec.get("observable_key") != key:
                continue
            for n in self.graph.nodes_by_identity(u, [p.content["inference_key"]]):
                if n.authority_class != AuthorityClass.SYSTEM_INFERRED or not self.active(u, n):
                    continue
                _, state = self._inference_outcomes(u, n)
                stale = min(STALENESS_TERM_MAX, STALENESS_WEIGHT * state["staleness"])
                watch = max(watch, 0.3 * (1 - abs(2 * state["confidence"] - 1)) + stale)
        return round(min(1.0, 0.8 * relevance + watch), 4)

    # scheduler (D7(a))
    def _loop_row(self, u):
        return self.store._conn.execute("SELECT * FROM runtime_loop WHERE user_id=?", (u,)).fetchone()

    def next_wake(self, u):
        with self.lock:
            row = self._loop_row(u)
            if row and row["wake_due"]:
                return row["wake_due"]
            base = instant(row["last_finished"]) if row and row["last_finished"] else instant(self.owner(u).created_at)
            return (base + timedelta(seconds=wake_interval(self.tau(u)))).isoformat()

    def wake_now(self, u):
        with self.store.transaction():
            self.store._conn.execute("INSERT INTO runtime_loop VALUES(?,0,NULL,?) ON CONFLICT(user_id) DO UPDATE SET wake_due=excluded.wake_due", (u, self.clock()))

    def _due_rounds(self):
        """Users whose scheduled round is due and who have no queued loop job."""
        due = []
        with self.lock:
            busy = {r[0] for r in self.store._conn.execute("SELECT user_id FROM runtime_jobs WHERE state!='DONE' AND state!='FAILED' AND state!='BLOCKED' AND json_extract(data_json,'$.kind')='product_cycle'")}
            for row in self.store._conn.execute("SELECT user_id FROM runtime_users").fetchall():
                u = row["user_id"]
                if u not in busy and instant(self.next_wake(u)) <= instant(self.clock()):
                    due.append(u)
        return due

    def _read_round(self,u,*,advance=False,manual=False):
        owner=getattr(self._read_context,'owner',None)
        if owner and owner['user_id']==u:return owner
        with self.lock:
            old=self._read_rounds.get(u)
            busy=self.store._conn.execute("SELECT 1 FROM runtime_jobs WHERE user_id=? AND state='RUNNING' LIMIT 1",(u,)).fetchone()
            due=old and instant(self.clock())>=instant(old['until'])
            if old is None or (not busy and not old['inflight'] and (advance and due or manual and self.clock()!=old['at'])):
                number=old['number']+1 if old else 1
                old={'user_id':u,'number':number,'round_id':f'{self.runtime_id}:{u}:{number}',
                     'at':self.clock(),'until':(instant(self.clock())+timedelta(seconds=wake_interval(self.tau(u)))).isoformat(),
                     'used':0,'inflight':0,'covered':False,'observations':{}}
                self._read_rounds[u]=old
            return old

    def _read_owner(self,u):
        owner=getattr(self._read_context,'owner',None)
        return owner if owner and owner['user_id']==u else self._read_rounds.get(u)

    def _claim_source_read(self,u,cid,coverage=False):
        with self.lock:
            owner=self._read_owner(u)
            if owner is None:return None  # Explicit fixture/bootstrap observations before scheduling.
            limit=4 if coverage or owner['covered'] else 3
            if owner['used']>=limit:
                self._receipt_line({'kind':'source_read','status':'DEFERRED','user_id':u,'source_id':cid,'round_id':owner['round_id'],'actual_reads':0,'at':self.clock()})
                raise ReadBudgetExhausted('four-read round exhausted or coverage slot reserved')
            owner['used']+=1;owner['inflight']+=1
            if coverage:owner['covered']=True
            token=(owner,owner['used'],cid)
            self._receipt_line({'kind':'source_read','status':'STARTED','user_id':u,'source_id':cid,'round_id':owner['round_id'],'ordinal':token[1],'actual_reads':1,'at':self.clock()})
            return token

    def _finish_source_read(self,token,*,observation=None,error=None):
        if token is None:return
        owner,ordinal,cid=token
        with self.lock:
            owner['inflight']-=1
            if observation is not None:owner['observations'][cid]=observation.source_event_id
            self._receipt_line({'kind':'source_read','status':'FAILED' if error else 'SUCCEEDED','user_id':owner['user_id'],
                'source_id':cid,'round_id':owner['round_id'],'ordinal':ordinal,'actual_reads':0,
                'observation_id':observation.source_event_id if observation else None,'error_type':type(error).__name__ if error else None,'at':self.clock()})

    def _reuse_source_read(self,u,observation):
        owner=self._read_owner(u)
        self._receipt_line({'kind':'source_read','status':'REUSED','user_id':u,'source_id':observation.content['source_id'],
            'round_id':owner['round_id'] if owner else None,'observation_id':observation.source_event_id,
            'observed_at':observation.received_at,'fresh':observation.content.get('fresh',False),'actual_reads':0,'at':self.clock()})

    # the loop (D5)
    def loop_round(self, u, wake="MANUAL", *, job=None, job_source=None):
        if wake not in WAKES:
            raise ValueError("wake must be one of " + ", ".join(WAKES))
        owner=self._read_round(u,advance=wake=='SCHEDULED',manual=wake=='MANUAL')
        previous=getattr(self._read_context,'owner',None)
        self._read_context.owner=owner
        token = self._begin_provider_operation("LOOP")
        try:
            return self._round(u, wake, job, job_source)
        finally:
            self._end_provider_operation(token)
            self._read_context.owner=previous

    def _channels_of(self, u):
        return [dict(r) for r in self.store._conn.execute("SELECT channel_id,kind FROM runtime_channels WHERE user_id=? ORDER BY channel_id", (u,)).fetchall()]

    def _select(self, u, polled):
        sampled = {r["channel_id"]: r["sampled_at"] for r in self.store._conn.execute("SELECT channel_id,sampled_at FROM runtime_channel_samples WHERE user_id=?", (u,))}
        attempts = {r['channel_id']:dict(r) for r in self.store._conn.execute('SELECT * FROM runtime_channel_attempts WHERE user_id=?',(u,))}
        polled = [c for c in polled if not attempts.get(c['channel_id'],{}).get('next_attempt') or instant(attempts[c['channel_id']]['next_attempt'])<=instant(self.clock())]
        def rank(c):
            cid = c["channel_id"]
            if cid not in sampled:
                return (0, 0.0, "", cid)
            if not self.config.get("predictive_modeling", True):  # S5 arm B: fixed rotation, oldest sample first
                return (1, 0.0, sampled[cid], cid)
            keys = json.loads(self.store._conn.execute("SELECT sample_json FROM runtime_channel_samples WHERE channel_id=?", (cid,)).fetchone()[0])
            # a key with no live binding and no watching inference has attention 0 (S5: not computed)
            score = max((self.attention(u, cid, k) for k in sorted(self._watched_keys(u, cid) & set(keys))), default=0.0)
            return (1, -score, sampled[cid], cid)
        ranked=[c['channel_id'] for c in sorted(polled,key=rank)]
        # Pending authorized investigations share the remaining three read slots.
        pending=self._pending_channel_investigations(u)
        wanted=list(dict.fromkeys(n.content['source_id'] for _,_,n in pending if n.content['source_id'] in ranked))
        ranked=wanted+[cid for cid in ranked if cid not in wanted]
        relevant=[c['channel_id'] for c in polled if self._watched_keys(u,c['channel_id'])]
        # Reserve one of the existing four reads, using attempts for fairness and
        # successful samples only for freshness. A failing source cannot monopolize it.
        if relevant:
            oldest=min(relevant,key=lambda cid:(attempts.get(cid,{}).get('last_attempt',sampled.get(cid,'')),cid))
            return [oldest]+[cid for cid in ranked if cid!=oldest][:SELECT_MAX_CHANNELS-1]
        return ranked[:SELECT_MAX_CHANNELS]

    def _pending_channel_investigations(self,u):
        out=[]
        for row in self.store._conn.execute("SELECT * FROM runtime_jobs WHERE user_id=? AND state='PENDING' AND json_extract(data_json,'$.kind')='product_investigation' ORDER BY job_id",(u,)):
            data=json.loads(row['data_json'])
            node=self.graph.get_node(u,data['investigation_id'])
            if node and instant(self.clock()).timestamp()>=data.get('not_before',0):out.append((dict(row),data,node))
        return out

    def _settle_due(self, u):
        """PREDICT: settle pending product forecasts on channels from samples taken before this round."""
        out = []
        pending = [r[0] for r in self.store._conn.execute("SELECT prediction_id FROM runtime_pending_forecasts WHERE user_id=? "
                                                          "ORDER BY created_at,prediction_id", (u,)).fetchall()]
        for p in sorted(self.graph.nodes_by_ids(u, pending), key=lambda n: (instant(n.created_at), n.node_id)):  # S5: unsettled channel forecasts only
            obs = p.content.get("observable") or {}
            cid = obs.get("modeled_subject_id")
            samples = [e for e in self.graph.source_events_by_ref_prefix(u, "ExternalObservation", cid + ":", p.created_at)
                       if instant(e.received_at) > instant(p.created_at) and any(f["key"] == obs.get("observable_key") for f in e.content.get("data", {}).get("facts", []))]
            if not samples:
                continue
            kind=obs.get("temporal_kind")
            if kind=="AT_TIME":samples=[e for e in samples if instant(e.received_at)==instant(obs.get("target_at",obs["window_end"]))]
            if not samples:continue
            attempted = [n for n in self.graph.nodes_by_identity_prefix(u, 'settlement:' + p.node_id + ':')
                         if n.node_type == 'ForecastSettlement' and n.content.get('status') != 'RESOLVED']
            attempted_sources = {n.content.get('evidence_source_event_id') for n in attempted}
            attempted_facts = {n.content.get('observation_id') for n in attempted}
            ordered = reversed(samples) if kind == 'WINDOW' else samples
            for sample in ordered:
                if sample.source_event_id in attempted_sources:
                    continue
                fact = self._fact_at(u, 'fact:' + cid + ':' + obs['observable_key'], sample.received_at)
                if fact and fact.node_id not in attempted_facts:
                    out.append(self.product.settle(u, p, fact, sample).node_id)
                    break
        return out

    def _round(self, u, wake, job, job_source):
        with self.lock:
            self.meta(u)
            started, tau = self.clock(), self.tau(u)
            presence = self._presence.get(u)
            row = self._loop_row(u)
            number = (row["last_round"] if row else 0) + 1
            calls0 = self._product_calls
            settled = self._settle_due(u)
            channels = self._channels_of(u)
            polled = [c for c in channels if c["kind"] in POLLED_KINDS or self.config.get("subject_resource_loop") and c["kind"]=="resource_simulation"]
            selected = self._select(u, polled)
            events = [c["channel_id"] for c in channels if c["kind"] in EVENT_KINDS]
        observed, changes, deadlines, documents, due_soon = [], [], [], [], []
        read_results={};read_errors={}
        for cid in selected + events:
            try:
                obs = self.observe_channel(u, cid, _coverage=bool(selected and cid==selected[0]))
            except ReadBudgetExhausted:
                continue  # No acquisition and no failure/freshness update.
            except Exception as exc:  # one unreachable channel does not stop the round
                read_errors[cid]=exc
                with self.lock:
                    self.source(u, "ChannelObservationFailure", f"{cid}:{number}", {"channel_id": cid, "error_type": type(exc).__name__})
                self._last_loop_sample.pop((u, cid), None)
                continue
            got = self._last_loop_sample.pop((u, cid), None)
            if got:
                read_results[cid]=self.graph.get_source_event(u,got['source_id'])
                observed.append(got["source_id"])
                changes += got["changes"]
            for o in obs:
                if o["key"] == "document" and o["provenance"].get("document_source_id"):
                    read_results[cid]=self.graph.get_source_event(u,o['provenance']['document_source_id'])
                    documents.append(o["provenance"]["document_source_id"])
                    observed.append(o["provenance"]["document_source_id"])
                elif o["key"].startswith("deadline:"):
                    deadlines.append(o["key"].split(":", 1)[1])
                elif o["key"].startswith("due_soon:"):     # CHANGE_NOTE_13 (2)
                    due_soon.append(o["key"].split(":", 1)[1])
        # Reuse the already budgeted read, never add a fifth source acquisition.
        for pending,data,investigation in self._pending_channel_investigations(u):
            cid=investigation.content['source_id']
            if cid in read_results or cid in read_errors:
                with self.lock,self.store.transaction():
                    claimed=self.store._conn.execute("UPDATE runtime_jobs SET state='RUNNING' WHERE job_id=? AND state='PENDING'",(pending['job_id'],)).rowcount
                if not claimed:continue
                status='FAILED'
                try:status=self.product.dispatch_investigation(u,data,reused_observation=read_results.get(cid),read_error=read_errors.get(cid))
                finally:
                    with self.lock,self.store.transaction():self.store._conn.execute('UPDATE runtime_jobs SET state=? WHERE job_id=?',(status,pending['job_id']))
        queued_cycle=None
        if job is None:
            queued_cycle=self.store._conn.execute("SELECT * FROM runtime_jobs WHERE user_id=? AND state='PENDING' AND json_extract(data_json,'$.kind')='product_cycle' AND json_extract(data_json,'$.trigger')='INVESTIGATION_RESULT' ORDER BY job_id LIMIT 1",(u,)).fetchone()
            if queued_cycle:
                with self.lock,self.store.transaction():
                    claimed=self.store._conn.execute("UPDATE runtime_jobs SET state='RUNNING' WHERE job_id=? AND state='PENDING'",(queued_cycle['job_id'],)).rowcount
                if claimed:
                    job=json.loads(queued_cycle['data_json'])
                    job_source=self.graph.get_source_event(u,job['source_id'])
                else:queued_cycle=None
        with self.lock:
            pe = []
            for c in changes:
                bound = self._bound(u, c["channel_id"], c["key"])
                pe.append({"channel_id": c["channel_id"], "key": c["key"], "kind": "VALUE_CHANGE", "belief_id": c["belief_id"],
                           "severity": max((severity(h) for _, h in bound), default=0.0), "responsibility_ids": [r.node_id for r, _ in bound]})
            for rid in deadlines:
                pe.append({"channel_id": None, "key": "deadline:" + rid, "kind": "DEADLINE", "belief_id": None, "severity": 1.0, "responsibility_ids": [rid]})
            for rid in due_soon:
                pe.append({"channel_id": None, "key": "due_soon:" + rid, "kind": "DUE_SOON", "belief_id": None, "severity": 1.0, "responsibility_ids": [rid]})
            pe += self._reminders(u)
            updates = [c["belief_id"] for c in changes]
        for doc in documents:  # UPDATE: web extraction, at most one call per fresh document, its own budget
            jrow = self.store._conn.execute("SELECT * FROM runtime_jobs WHERE job_id=? AND state='PENDING'", ("web:" + doc,)).fetchone()
            if jrow:
                self._execute_job(dict(jrow))
        updates += self._digest(u)  # S4 (D9): consented history, its own budget, never in reason_calls
        cycle, trigger, goal_ids = None, None, []
        if job is not None:
            trigger, goal_ids = job.get("trigger"), list(job.get("goal_ids") or [])
        else:
            ranked = sorted((p for p in pe if p["severity"] > 0), key=lambda p: -p["severity"])
            goal_ids = list(dict.fromkeys(r for p in ranked for r in p["responsibility_ids"]))
            trigger = ("DEADLINE" if any(p["kind"] == "DEADLINE" for p in ranked) else "SOURCE_OBSERVATION") if goal_ids else None
        closed_now=self.advance_responsibilities(u)
        goal_ids=[g for g in goal_ids if self.needs_preparation(u,g)]
        initial=[n.content['goal_id'] for n in self.nodes(u,'ProductDeliveryWork') if n.content.get('initial_pending') and self.product._work(u,n.content['goal_id']).node_id==n.node_id and self.needs_preparation(u,n.content['goal_id'])]
        if initial:goal_ids=list(dict.fromkeys(initial+goal_ids));trigger=trigger or 'NEW_RESPONSIBILITY'
        if job is None:
            repairs=[n for n in self.nodes(u,'ProductDeliveryWork') if n.content.get('status') in ('PENDING','READY_FOR_PROPOSAL','NEEDS_REPAIR') and n.content.get('recovery_rounds',0)<2 and not n.content.get('initial_pending')]
            if repairs:
                recovered=[n.content['goal_id'] for n in repairs if self.product._work(u,n.content['goal_id']).node_id==n.node_id and self.needs_preparation(u,n.content['goal_id'])]
                if recovered:
                    goal_ids=list(dict.fromkeys(goal_ids+recovered));trigger=trigger or 'WORK_RESUME'
                    chosen=self.product._select_goal(u,goal_ids)
                    if chosen and chosen[0] in recovered:
                        work=self.product._work(u,chosen[0]);src=self.source(u,'WorkResume',f'{chosen[0]}:{number}',{'reason':work.content.get('resume_reason')},at=started);self.product._work_update(u,chosen[0],src,recovery_rounds=work.content.get('recovery_rounds',0)+1)
        if self.config.get('subject_resource_loop'):
            from skuld.loop.subject_resource import prepare
            domain=prepare(self,u,read_results,number)
            if domain is not None:
                job,job_source=domain;goal_ids=job['goal_ids'];trigger='RESPONSIBILITY_EXPERIENCE'
        if self.config.get('tool_subject'):
            from skuld.loop.tool_subject import prepare
            job,job_source=prepare(self,u,number);goal_ids=[];trigger='PUBLIC_TOOL_RESPONSIBILITY'
        failure = None
        if job is not None or goal_ids:
            if job is None:
                with self.lock:
                    job_source = self.source(u, "LoopWake", f"round:{number}", {"round": number, "trigger": trigger, "goal_ids": goal_ids}, at=started)
                    job = {"kind": "product_cycle", "cycle_id": nid("cycle", u, "loop", number), "source_id": job_source.source_event_id,
                           "trigger": trigger, "goal_ids": goal_ids}
            job = {**job, "loop": {"tau": tau, "policy": self.policy.name, "round": number, "presence": presence}}
            try:
                cycle = self._drive(self.product.steps(u, job, job_source))
            except Exception as exc:
                failure = exc
                for gid in goal_ids:
                    work=self.product._work(u,gid)
                    if work:self.product._work_update(u,gid,job_source,status=work.content.get("status","PENDING"),resume_reason=type(exc).__name__+": "+str(exc)[:200])
        reason_calls = self._product_calls - calls0
        with self.lock:
            loop = (cycle or {}).get("loop") or {}
            decisions = [loop["decision"]] if loop.get("decision") else ([{"kind": "WAIT", "level": 0, "candidate_id": None, "decision_id": None,
                                                                         "mandate_id": None, "goal_ids": goal_ids}] if cycle is not None else [])
            actions = list(loop.get("actions", []))
            updates += list(loop.get("inferred", []))
            deliveries = self._release_held(u) if presence != "present" and not self.quiet(u) and tau > 0 else []
            if loop.get("deliverable") and decisions and decisions[0]["level"] >= 2:
                sev = max((p["severity"] for p in pe if set(p["responsibility_ids"]) & set(decisions[0].get("goal_ids") or [])), default=0.0)
                if tau > 0 and sev > 1 - tau:
                    deliveries += [d for d in [self._deliver(u, loop["deliverable"], decisions[0].get("goal_ids") or [], number,
                                                             presence=presence, kind=decisions[0]["kind"])] if d]
            pruned = self.maintain(u)
            prev = self.graph.nodes_by_identity(u, [f"loop:{number - 1}"]) if number > 1 else []
            sec = None
            if self.sec is not None:
                prev_id = next((n.node_id for n in prev if n.node_type == "LoopReceipt"), None)
                sec = {"update_ids": self.sec.update_ids(u, prev_id) if prev_id else [],
                       "state_hash": self.sec.state(u)["state_hash"]}
            finished = self.clock()
            content = {"round": number, "wake": wake, "trigger": trigger, "tau": tau, "policy": self.policy.name, "stages": list(LOOP_STAGES),
                       "settled": settled, "predicted": list(loop.get("predicted", [])), "selected": selected, "observed": observed, "pe": pe,
                       "updates": updates, "reason_calls": reason_calls, "goals": {"seen": goal_ids, "created": [], "closed": closed_now},
                       "decisions": decisions, "actions": actions, "deliveries": deliveries, "pruned": pruned, "sec": sec,
                       "started_at": started, "finished_at": finished}
            with self.store.transaction():
                src = self.source(u, "LoopRound", str(number), {"round": number, "wake": wake}, at=finished)
                receipt = self.node(u, "LoopReceipt", f"loop:{number}", content, src)
                self.store._conn.execute("INSERT INTO runtime_loop VALUES(?,?,?,NULL) ON CONFLICT(user_id) DO UPDATE SET last_round=excluded.last_round,"
                                         "last_finished=excluded.last_finished,wake_due=NULL", (u, number, finished))
        if self.sec is not None:
            self.sec.learn(u, receipt)
        if queued_cycle is not None:
            with self.store.transaction():self.store._conn.execute('UPDATE runtime_jobs SET state=? WHERE job_id=?',('FAILED' if failure else 'DONE',queued_cycle['job_id']))
        if failure is not None:
            raise failure
        return receipt

    # CHANGE_NOTE_12 (a): presence as the surface reports it (S5: the simulator; S6: the UI). Operational and in memory:
    # unknown after a restart, and unknown keeps the presence-blind behaviour of S3.
    def set_presence(self, u, state):
        if state is not None and state not in PRESENCE_STATES:
            raise ValueError("presence must be one of " + ", ".join(PRESENCE_STATES) + " or None")
        with self.lock:
            self._presence[u] = state

    def presence(self, u):
        return self._presence.get(u)

    # D8 delivery
    def _urgency(self, u, goal_ids):
        now = instant(self.clock())
        for gid in goal_ids:
            r = self.graph.get_node(u, gid)
            if not r or r.authority_class != AuthorityClass.USER_FIRST_PERSON:
                continue
            if r.content.get("urgent") is True:
                return "URGENT"
            due = r.content.get("due_at")
            if due and (instant(due) - now).total_seconds() <= URGENT_HOURS * 3600 and self._responsibility_live(u, r):
                return "URGENT"
        return "NORMAL"

    def _delivery_day(self, at):
        tz = ZoneInfo((self.notifier.config["timezone"] if self.notifier else "UTC"))
        return instant(at).astimezone(tz).date().isoformat()

    def _send(self, u, mandate, item, urgency, day):
        if self.quiet(u):return "HELD", "quiet/DND"
        cap = URGENT_DAILY_MAX if urgency == "URGENT" else NON_URGENT_DAILY_MAX
        sent = self.store._conn.execute("SELECT COUNT(*) FROM runtime_deliveries WHERE user_id=? AND day=? AND urgency=? AND status='SENT'", (u, day, urgency)).fetchone()[0]
        total = self.store._conn.execute("SELECT COUNT(*) FROM runtime_deliveries WHERE user_id=? AND day=? AND status='SENT'", (u, day)).fetchone()[0]
        if total >= DAILY_DELIVERY_MAX:
            return 'SUPPRESSED_CAP', f'daily total cap {DAILY_DELIVERY_MAX} reached'
        if sent >= cap:
            return "SUPPRESSED_CAP", f"daily {urgency.lower()} cap {cap} reached"
        result = self.notifier.deliver({"to": mandate.content["address"], "subject": item["title"], "body": item["body"]}, urgency)
        return ("SENT", None) if result["ok"] else ("FAILED", result["error"])

    def _deliver(self, u, item, goal_ids, number, presence=None, kind=None):
        if not self.delivery_applicable(u,item,goal_ids):return None
        mandate = self.mandate_for(u, "delivery", "SEND")
        urgency, at = self._urgency(u, goal_ids), self.clock()
        day = self._delivery_day(at)
        delivery_id = self.delivery_key(u,item,goal_ids)
        previous=self.store._conn.execute("SELECT status FROM runtime_deliveries WHERE delivery_id=?",(delivery_id,)).fetchone()
        if previous and previous[0]=="SENT":return delivery_id
        if self.notifier is None or mandate is None:
            return None
        if self.quiet(u):status,error="HELD","quiet/DND"
        elif presence == "present":   # CHANGE_NOTE_12 (a): nothing is pushed to a present user; a question is for the session
            status, error = ("IN_SESSION" if kind == "ASK" else "HELD"), None
        else:
            status, error = self._send(u, mandate, item, urgency, day)
        with self.store.transaction():
            self.store._conn.execute("INSERT OR REPLACE INTO runtime_deliveries VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                     (delivery_id, u, item["ref"], item["kind"], urgency, status, item["title"], at, day, number, error))
            if status == "HELD":
                self.store._conn.execute("INSERT OR REPLACE INTO runtime_held_deliveries VALUES(?,?,?,?,?)", (delivery_id, u, dumps(item), dumps(list(goal_ids)), at))
        return delivery_id

    def _release_held(self, u):
        """CHANGE_NOTE_12 (a): at an absent round, what was held while the user was present goes out, oldest first, through
        the same D8 caps (urgency and day as of sending); an item whose responsibilities are all closed is dropped."""
        if self.quiet(u):return []
        mandate, out = self.mandate_for(u, "delivery", "SEND"), []
        for r in self.store._conn.execute("SELECT * FROM runtime_held_deliveries WHERE user_id=? ORDER BY held_at,rowid", (u,)).fetchall():
            item, goal_ids = json.loads(r["item_json"]), json.loads(r["goal_ids_json"])
            nodes = [n for n in (self.graph.get_node(u, g) for g in goal_ids) if n is not None]
            urgency, at = self._urgency(u, goal_ids), self.clock()
            day = self._delivery_day(at)
            if not self.delivery_applicable(u,item,goal_ids) or (nodes and not any(self._responsibility_live(u, n) for n in nodes) and not any(self.responsibility_contract(u,g).get("notify_required") for g in goal_ids)):
                status, error = "DROPPED_CLOSED", "every responsibility of the held item is closed"
            elif self.notifier is None or mandate is None:
                status, error = "SUPPRESSED_NO_MANDATE", "no live delivery mandate"
            else:
                status, error = self._send(u, mandate, item, urgency, day)
            with self.store.transaction():
                self.store._conn.execute("UPDATE runtime_deliveries SET status=?,urgency=?,at=?,day=?,error=? WHERE delivery_id=?",
                                         (status, urgency, at, day, error, r["delivery_id"]))
                self.store._conn.execute("DELETE FROM runtime_held_deliveries WHERE delivery_id=?", (r["delivery_id"],))
            out.append(r["delivery_id"])
        return out

    def deliveries(self, u):
        return [{"delivery_id": r["delivery_id"], "item_ref": r["item_ref"], "kind": r["kind"], "urgency": r["urgency"], "status": r["status"],
                 "subject": r["subject"], "at": r["at"]}
                for r in self.store._conn.execute("SELECT * FROM runtime_deliveries WHERE user_id=? ORDER BY round,rowid", (u,)).fetchall()]

    # D6 L3 / L4 through the existing agency bridge
    def _ground(self, u, decision_id, goal, artifact, channel_id, operation, parameters, probability, source):
        at = source.received_at
        intent = self.agency.propose_intent(user_id=u, intent={"decision_id": decision_id, "channel_id": channel_id, "operation": operation},
                                            source_event_id=source.source_event_id, created_at=at)
        justification = self.node(u, "Prediction", "action:" + decision_id, {"kind": "action_justification", "decision_id": decision_id, "actionability": True,
                                  "probability": probability, "goal_id": goal.node_id, "status": "pending"}, source, modal=ModalState.PREDICTED)
        return self.agency.ground_action(user_id=u, intent_node_id=intent.node_id, prediction_node_id=justification.node_id, goal_node_id=goal.node_id,
                                         connector="channel:" + channel_id, operation=operation, parameters=parameters,
                                         parameter_sources={k: {"node_id": artifact.node_id, "field": k} for k in parameters},
                                         source_event_id=source.source_event_id, created_at=at)

    def _proposal_rows(self, u):
        return [n for n in self.nodes(u, "ProductWork") if n.content.get("kind") == "EXECUTE" and n.content.get("action_id")]

    def _action_status(self, u, work):
        aid = work.content["action_id"]
        idem = "action:" + aid
        ident = canonical_hash({"user_id": u, "idempotency_key": idem})
        outcome = self.graph.get_node(u, "action_outcome_v200_" + ident[:40])
        if outcome is not None:
            return "EXECUTED" if outcome.content.get("status") == "SUCCEEDED" else "FAILED"
        if self.graph.get_node(u, "action_attempt_v200_" + ident[:40]) is not None:
            return "IN_DOUBT"
        return "APPROVED" if any(n.content.get("action_node_id") == aid for n in self.nodes(u, "Approval")) else "PROPOSED"

    def proposals(self, u):
        with self.lock:
            out = []
            for w in self._proposal_rows(u):
                if w.content.get("level") != 3:
                    continue
                action = self.graph.get_node(u, w.content["action_id"])
                out.append({"action_id": action.node_id, "action_hash": action.content["action_hash"], "channel_id": w.content["channel_id"],
                            "operation": w.content["operation"], "parameters": dict(w.content["parameters"]), "risk_class": w.content["risk_class"],
                            "artifact_id": w.content["artifact_node_id"], "status": self._action_status(u, w)})
            return out

    def in_doubt(self, u, channel_id, operation, parameters):
        """True while an attempt with these exact (channel, operation, parameters) is IN_DOUBT."""
        return any(w.content["channel_id"] == channel_id and w.content["operation"] == operation and w.content["parameters"] == parameters
                   and self._action_status(u, w) == "IN_DOUBT" for w in self._proposal_rows(u))

    def approve(self, u, aid, action_hash):
        with self.lock:
            action = self.graph.get_node(u, aid)
            if not action or action.node_type != "GroundedAction":
                raise KeyError("unknown action")
            if action_hash != action.content.get("action_hash"):
                raise ValueError("approval action hash mismatch")
            expires = (instant(self.clock()) + timedelta(seconds=APPROVAL_TTL_SECONDS)).isoformat()
            seq = len(self.nodes(u, "Approval")) + 1
            with self.store.transaction():
                src = self.source(u, "ExactActionApproval", f"{aid}:{seq}", {"action_id": aid, "action_hash": action_hash, "principal": u,
                                  "expires_at": expires, "basis": "EXACT_USER_APPROVAL"})
                return self.agency.approve(user_id=u, action_node_id=aid, approved_action_hash=action_hash, source_event_id=src.source_event_id,
                                           created_at=src.received_at).node_id

    def revoke(self, u, aid, approval_id):
        with self.lock:
            approval = self.graph.get_node(u, approval_id)
            if not approval or approval.node_type != "Approval" or approval.content.get("action_node_id") != aid:
                raise KeyError("unknown approval for this action")
            with self.store.transaction():
                src = self.source(u, "UserApprovalRevocation", approval_id, {"approval_id": approval_id})
                mark = self.node(u, "Revocation", "revoke:" + approval_id, {"approval_id": approval_id}, src, authority=AuthorityClass.USER_FIRST_PERSON)
                self.edge(u, "WITHDRAWS", mark, approval, src)
            return mark.node_id

    def execute(self, u, aid, approval_id):
        with self.lock:
            approval = self.graph.get_node(u, approval_id)
            if not approval or approval.node_type != "Approval" or approval.content.get("action_node_id") != aid:
                raise ValueError("an exact approval of this action is required")
            source = self.graph.get_source_event(u, approval.source_event_id)
            if instant(source.content["expires_at"]) < instant(self.clock()):
                ident = canonical_hash({"user_id": u, "idempotency_key": "action:" + aid})
                if self.graph.get_node(u, "action_outcome_v200_" + ident[:40]) is None:
                    raise ValueError("approval expired")
            return self._dispatch(u, aid, approval_id)

    def _dispatch(self, u, aid, approval_id):
        work = next(w for w in self._proposal_rows(u) if w.content["action_id"] == aid)
        ch = self.channel(u, work.content["channel_id"])
        artifact = self.graph.get_node(u, work.content["artifact_node_id"])

        def connector(params):
            return ch.perform(work.content["operation"], {k: v for k, v in params.items() if k != "idempotency_key"}, artifact.content["content"])
        try:
            r = self.agency.execute(user_id=u, action_node_id=aid, approval_node_id=approval_id, idempotency_key="action:" + aid,
                                    connector_call=connector, created_at=self.clock())
        except ValueError:
            raise
        except Exception as exc:
            return {"status": "IN_DOUBT", "outcome_id": None, "error_type": type(exc).__name__}
        outcome = self.graph.get_node(u, r.action_outcome_node_id)
        return {"status": outcome.content["status"], "outcome_id": outcome.node_id}

    def run_under_mandate(self, u, aid, mandate, source):
        """L4: the live mandate authorizes a one-shot approval, executed at once, then reported."""
        action = self.graph.get_node(u, aid)
        with self.store.transaction():
            src = self.source(u, "ExactActionApproval", f"{aid}:mandate", {"action_id": aid, "action_hash": action.content["action_hash"], "principal": "MANDATE",
                              "mandate_id": mandate.node_id, "expires_at": self.clock(), "basis": "STANDING_OR_SESSION_MANDATE"})
            approval = self.agency.approve(user_id=u, action_node_id=aid, approved_action_hash=action.content["action_hash"],
                                           source_event_id=src.source_event_id, created_at=src.received_at)
        return approval.node_id, self._dispatch(u, aid, approval.node_id)


__all__ = ["LoopMixin", "LOOP_STAGES", "wake_interval", "severity"]
