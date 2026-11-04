"""Repeatable product E2E: UI backing state, Pi tool, approval and V4 restart."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from .secret_loader import load_deepseek_key
from .v4_wiring import V4Terminal


def main() -> int:
    repository = Path(__file__).parents[1]
    base = repository / ".terminal-e2e"
    if base.exists():
        shutil.rmtree(base)
    workspace = base / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "sample.txt").write_text("Skuld Pi read evidence\n", encoding="utf-8")
    credential = load_deepseek_key(repository)
    facts: dict = {"started_at": datetime.now(timezone.utc).isoformat(),
                   "deepseek_credential_present": bool(credential), "checks": {}}
    terminal = V4Terminal(base / "state", workspace, credential)
    try:
        initial_subject = terminal.state()["skuld"]["subject_id"]
        terminal.change_mode("OBSERVE")
        read = terminal.read("sample.txt")
        facts["checks"]["pi_real_read"] = (read["result"]["status"] == "COMPLETED"
            and "Skuld Pi read evidence" in read["result"]["content"]
            and len(read["evidence"]["receipt_ids"]) == 1)
        try:
            terminal.read("missing.txt")
            facts["checks"]["read_failure_feedback"] = False
        except FileNotFoundError:
            outcomes = terminal.runtime.nodes(terminal.user_id, "PiToolOutcome")
            facts["checks"]["read_failure_feedback"] = outcomes[-1].content["status"] == "DENIED_OR_FAILED"
        try:
            terminal.propose_write("new.txt", "new evidence")
            facts["checks"]["observe_blocks_write"] = False
        except PermissionError:
            facts["checks"]["observe_blocks_write"] = True
        terminal.change_mode("ACT")
        action_id = terminal.propose_write("new.txt", "new evidence")
        facts["checks"]["write_requires_exact_approval"] = not (workspace / "new.txt").exists()
        written = terminal.approve_write(action_id)
        facts["checks"]["pi_real_write"] = (written["result"]["status"] == "COMPLETED"
            and (workspace / "new.txt").read_text(encoding="utf-8") == "new evidence")
        facts["checks"]["v4_feedback_persisted"] = len(terminal.runtime.nodes(terminal.user_id, "PiToolOutcome")) == 3
        facts["checks"]["graph_invariants"] = not terminal.runtime.export(terminal.user_id)["invariant_violations"]
        if credential:
            try:
                answer = terminal.conversation("请用一句中文回答：你是谁？")
                facts["checks"]["pi_deepseek_conversation"] = bool(answer.get("reply"))
                facts["conversation_reply_excerpt"] = answer.get("reply", "")[:200]
            except Exception as exc:
                facts["checks"]["pi_deepseek_conversation"] = False
                facts["conversation_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        else:
            facts["checks"]["pi_deepseek_conversation"] = False
    finally:
        terminal.close()
    recovered = V4Terminal(base / "state", workspace, credential)
    try:
        facts["checks"]["restart_subject_session_mode"] = (
            recovered.state()["skuld"]["subject_id"] == initial_subject
            and recovered.mode == "ACT" and recovered.session_id == terminal.session_id)
        facts["checks"]["restart_outcomes"] = len(recovered.state()["recent_outcomes"]) == 3
    finally:
        recovered.close()
    facts["finished_at"] = datetime.now(timezone.utc).isoformat()
    facts["passed"] = all(facts["checks"].values())
    (base / "result.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": facts["passed"], "checks": facts["checks"],
                      "artifact": str(base / "result.json")}, ensure_ascii=False))
    return 0 if facts["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
