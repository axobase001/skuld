"""A single JSONL Pi process. Skuld owns every plan and records every receipt."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path


def canonical_hash(value: object) -> str:
    wire = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


def signed(payload: dict) -> dict:
    return {**payload, "capability_hash": canonical_hash(payload)}


class PiBridge:
    def __init__(self, workspace: Path, session_id: str, key: str | None, denied: tuple[Path, ...]):
        self.workspace = workspace.resolve(strict=True)
        self.session_id = session_id
        self.key = key
        self.denied = tuple(str(p.resolve()) for p in denied)
        self.receipts: list[dict] = []
        self._lock = threading.RLock()
        entry = Path(__file__).parent / "harness_pi" / "dist" / "index.js"
        if not entry.is_file():
            raise RuntimeError("Pi sidecar is not built; run npm ci and npm run build in terminal_app/harness_pi")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(
            ["node", str(entry)], cwd=self.workspace, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding="utf-8", bufsize=1, creationflags=flags,
        )
        stamp = int(time.time() * 1000)
        capability = signed({
            "session_id": session_id, "requested_cwd": str(self.workspace),
            "resolved_cwd": str(self.workspace), "allowed_roots": [str(self.workspace)],
            "denied_roots": list(self.denied), "issued_at": stamp,
            "expiry": stamp + 12 * 60 * 60 * 1000,
        })
        command = {"type": "start", "sessionCapability": capability,
                   "modelProvider": "deepseek" if key else "unconfigured",
                   "model": "deepseek-v4-flash" if key else "unconfigured",
                   "maxModelTurns": 1000}
        if key:
            command.update(runtimeApiKey=key, modelRoute={"provider": "deepseek",
                "model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com"})
        self.request(command)

    def request(self, command: dict) -> dict:
        with self._lock:
            request_id = uuid.uuid4().hex
            command = {**command, "id": request_id}
            assert self.process.stdin is not None and self.process.stdout is not None
            self.process.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            while True:
                line = self.process.stdout.readline()
                if not line:
                    raise RuntimeError("Pi sidecar exited before response")
                item = json.loads(line)
                if item.get("type") == "response" and item.get("id") == request_id:
                    if not item.get("success"):
                        raise RuntimeError(str(item.get("error", "Pi request failed")))
                    return item
                if item.get("type") in {"tool_receipt", "model_receipt", "network_egress_receipt", "harness_event"}:
                    self.receipts.append(item)

    def transport(self, endpoint, key, payload, stop, owned_registry, registry_lock, nonce):
        if endpoint not in {"https://api.deepseek.com/chat/completions", "https://api.deepseek.com/v1/chat/completions"} or key != self.key:
            return {"error_type": "ROUTE_MISMATCH", "error_detail": "Pi route differs from Skuld route"}
        try:
            reply = self.request({"type": "model_turn", "systemPrompt": payload["messages"][0]["content"],
                "userContent": payload["messages"][1]["content"], "maxTokens": payload["max_tokens"]})
            body = {"id": reply.get("requestId"), "model": reply.get("servedModel"),
                    "choices": [{"finish_reason": "stop", "message": {"content": reply["text"]}}],
                    "usage": reply.get("usage", {})}
            return {"http_status": 200, "body": json.dumps(body, ensure_ascii=False)}
        except Exception as exc:
            return {"error_type": type(exc).__name__, "error_detail": str(exc)[:500]}

    def close(self):
        if self.process.poll() is None:
            try:
                self.request({"type": "stop"})
            except Exception:
                pass
            if self.process.stdin:
                self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
