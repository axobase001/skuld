"""Skuld V4 owns the Terminal state, decisions, approvals and Pi evidence."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from skuld.app.local import initialize_workspace
from skuld.app.runtime import Runtime

from .pi_bridge import PiBridge, canonical_hash, signed


class V4Terminal:
    def __init__(self, root: Path, workspace: Path, key: str | None = None,
                 profile_id: str = "default"):
        self.root = root.resolve()
        self.workspace = workspace.resolve(strict=True)
        if not (self.root / "config.json").is_file():
            initialize_workspace(self.root, "Local " + profile_id)
        config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        access = json.loads((self.root / "access.json").read_text(encoding="utf-8"))
        self.user_id = access["user_id"]
        self.profile_id = profile_id
        self.key = key
        if self.key:
            config["routes"]["pi-deepseek"] = {"provider": "deepseek", "model": "deepseek-v4-flash",
                "base_url": "https://api.deepseek.com", "key": self.key,
                "transport": "chat_json_object"}
            config["max_model_calls"] = 100
        self.runtime = Runtime(self.root, config, provider_transport=self._transport)
        try:
            sessions = self.runtime.nodes(self.user_id, "TerminalSession")
            self.session_id = sessions[-1].content["session_id"] if sessions else "terminal_" + uuid.uuid4().hex
            if not sessions:
                self._record("TerminalSession", {"session_id": self.session_id})
            if self.key and self.runtime.meta(self.user_id)["route"] != "pi-deepseek":
                self.runtime.switch(self.user_id, "pi-deepseek")
            denied = (self.root, self.workspace / ".git", self.workspace / ".skuld-terminal-secrets",
                      self.workspace / ".terminal-accounts")
            self.pi = PiBridge(self.workspace, self.session_id, self.key, denied)
        except BaseException:
            self.runtime.close()
            raise
        modes = self.runtime.nodes(self.user_id, "TerminalMode")
        self.mode = modes[-1].content["mode"] if modes else "PAUSED"
        self.trace: list[dict] = []
        self._skuld_state_cache: dict | None = None

    def _record(self, kind: str, content: dict) -> str:
        identifier = uuid.uuid4().hex
        with self.runtime.lock, self.runtime.store.transaction():
            source = self.runtime.source(self.user_id, kind, identifier, content)
            node = self.runtime.node(self.user_id, kind, identifier, content, source)
        return node.node_id

    def change_mode(self, target: str) -> str:
        target = target.upper()
        if target not in {"PAUSED", "OBSERVE", "ACT"}:
            raise ValueError("Invalid Terminal mode")
        self._record("TerminalMode", {"mode": target, "previous": self.mode,
            "session_id": self.session_id, "origin": "LOCAL_UI"})
        self.mode = target
        return target

    def _transport(self, *args):
        return self.pi.transport(*args)

    def conversation(self, text: str) -> dict:
        if self.mode == "PAUSED":
            raise PermissionError("PAUSED mode: conversation is stopped")
        if not self.key:
            raise RuntimeError("DeepSeek credential unavailable")
        key = "turn_" + uuid.uuid4().hex
        result = self.runtime.chat(self.user_id, text, key, self.session_id)
        self._skuld_state_cache = None
        self.trace.append({"kind": "conversation", "key": key, "result": result})
        return result

    def _target(self, value: str, create: bool) -> Path:
        path = (self.workspace / value).resolve(strict=not create)
        if not path.is_relative_to(self.workspace):
            raise PermissionError("Target outside workspace")
        forbidden = (self.root, self.workspace / ".git", self.workspace / ".skuld-terminal-secrets",
                     self.workspace / ".terminal-accounts")
        if any(path == item or item in path.parents for item in forbidden):
            raise PermissionError("Protected target")
        if create and not path.parent.is_dir():
            raise ValueError("Parent directory must exist")
        return path

    def propose_write(self, path: str, content: str) -> str:
        if self.mode != "ACT":
            raise PermissionError("Write requires ACT mode")
        target = self._target(path, True)
        if target.exists():
            raise FileExistsError("Write creates a new file only")
        action_id = "action_" + uuid.uuid4().hex
        self._record("PiActionProposal", {"action_id": action_id, "operation": "write",
            "path": str(target), "content": content, "content_sha256": canonical_hash(content),
            "session_id": self.session_id, "status": "PENDING_APPROVAL"})
        return action_id

    def approve_write(self, action_id: str) -> dict:
        if self.mode != "ACT":
            raise PermissionError("Write approval requires ACT mode")
        proposals = [n for n in self.runtime.nodes(self.user_id, "PiActionProposal")
                     if n.content.get("action_id") == action_id]
        if len(proposals) != 1:
            raise ValueError("Unknown or ambiguous action")
        if any(n.content.get("action_id") == action_id for n in self.runtime.nodes(self.user_id, "PiActionApproval")):
            raise PermissionError("Action already approved")
        proposal = proposals[0].content
        target = self._target(proposal["path"], True)
        if target.exists() or proposal["session_id"] != self.session_id:
            raise PermissionError("Proposal no longer matches target or session")
        self._record("PiActionApproval", {"action_id": action_id,
            "proposal_node_id": proposals[0].node_id, "target": str(target), "origin": "LOCAL_UI_EXACT"})
        return self._execute(action_id, "write", {"path": str(target), "content": proposal["content"]})

    def read(self, path: str) -> dict:
        action_id = "action_" + uuid.uuid4().hex
        try:
            if self.mode not in {"OBSERVE", "ACT"}:
                raise PermissionError("Read requires OBSERVE or ACT mode")
            target = self._target(path, False)
            return self._execute(action_id, "read", {"path": str(target)})
        except Exception as exc:
            evidence = {"action_id": action_id, "operation": "read", "target": path,
                        "status": "DENIED_OR_FAILED", "error": type(exc).__name__,
                        "detail": str(exc)[:300], "receipt_ids": []}
            self._record("PiToolOutcome", evidence)
            self.trace.append(evidence)
            raise

    def _execute(self, action_id: str, operation: str, parameters: dict) -> dict:
        target = str(self._target(parameters["path"], operation == "write"))
        effect = "READ_ONLY" if operation == "read" else "LOCAL_REVERSIBLE"
        plan = {"action_id": action_id, "effect_class": effect,
                "tool": operation, "parameters": parameters}
        plan_hash = canonical_hash(plan)
        stamp = int(time.time() * 1000)
        capability = signed({"session_id": self.session_id, "action_id": action_id,
            "plan_hash": plan_hash, "resolved_effect_class": effect, "data_class": "LOCAL_WORKSPACE",
            "read_scope": None, "remote_model_visible": False,
            "authorized_operations": [operation], "declared_targets": [target],
            "resolved_allowed_targets": [target], "session_allowed_roots": [str(self.workspace)],
            "issued_at": stamp, "expiry": stamp + 60_000})
        before = len(self.pi.receipts)
        try:
            result = self.pi.request({"type": "execute_direct", "actionPlan": {
                **plan, "plan_hash": plan_hash, "capability": capability}})
        except Exception as exc:
            result = {"status": "FAILED", "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                      "receiptIds": [], "content": ""}
        receipts = self.pi.receipts[before:]
        evidence = {"action_id": action_id, "operation": operation, "target": target,
            "status": result["status"], "receipt_ids": result.get("receiptIds", []),
            "receipts": receipts, "error": result.get("error"),
            "result_excerpt": result.get("content", "")[:4000]}
        self._record("PiToolOutcome", evidence)
        self.trace.append(evidence)
        return {"result": result, "evidence": evidence}

    def state(self) -> dict:
        if self._skuld_state_cache is None:
            self._skuld_state_cache = self.runtime.state(self.user_id)
        history = self.runtime.store._conn.execute(
            "SELECT COUNT(*),COALESCE(SUM(episodes),0),COALESCE(SUM(digested),0) FROM runtime_history WHERE user_id=?",
            (self.user_id,)).fetchone()
        return {"mode": self.mode, "session_id": self.session_id, "profile_id": self.profile_id,
            "skuld": self._skuld_state_cache,
            "historical_world_evidence": {"batches": history[0], "messages": history[1],
                                           "digested_messages": history[2], "retrieval_limit_per_turn": 3},
            "recent_outcomes": [n.content for n in self.runtime.nodes(self.user_id, "PiToolOutcome")][-20:]}

    def close(self):
        self.pi.close()
        self.runtime.close()
