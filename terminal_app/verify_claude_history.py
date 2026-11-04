"""Product E2E evidence for the staged historical import; no model call."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .import_claude_history import DEFAULT_EXPORT, file_hash
from .secret_loader import load_deepseek_key
from .v4_wiring import V4Terminal


def main() -> int:
    repository = Path(__file__).parents[1]
    terminal = V4Terminal(repository / ".terminal-runtime", repository,
        load_deepseek_key(repository))
    try:
        rt, user_id = terminal.runtime, terminal.user_id
        report = json.loads((terminal.root / "claude_import_report.json").read_text(encoding="utf-8"))
        conn = rt.store._conn
        history = conn.execute("SELECT COUNT(*),SUM(episodes),SUM(consented),SUM(revoked) FROM runtime_history WHERE user_id=?", (user_id,)).fetchone()
        indexed = conn.execute("SELECT COUNT(*) FROM runtime_history_index WHERE user_id=?", (user_id,)).fetchone()[0]
        episodes = conn.execute("SELECT COUNT(*) FROM runtime_history_episodes").fetchone()[0]
        historical = sum(1 for n in rt.nodes(user_id, "ConversationEpisode")
                         if n.content.get("historical") and n.content.get("not_current_fact")
                         and n.content.get("not_action_approval") and not n.content.get("provider_eligible"))
        start = time.monotonic()
        retrieved = rt.search_history(user_id, "Skuld", 3)
        retrieval_ms = round((time.monotonic() - start) * 1000)
        quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        attempts = rt.model_call_usage()
        receipts = [json.loads(row[0])["receipt"] for row in conn.execute(
            "SELECT attempt_json FROM runtime_provider_receipts")]
        digests = rt.nodes(user_id, "DigestBatch")
        receipt_ids = {row[0] for row in conn.execute(
            "SELECT receipt_id FROM runtime_provider_receipts")}
        failed_without_request = sum(batch.content["receipt_id"] is None for batch in digests)
        inferred = sum(len(batch.content["accepted"]) for batch in digests)
        progress = conn.execute("SELECT COALESCE(SUM(digested),0),COALESCE(SUM(batches_used),0) "
                                "FROM runtime_history WHERE user_id=?", (user_id,)).fetchone()
        checks = {
            "source_hash": file_hash(DEFAULT_EXPORT) == report["source_sha256"],
            "all_conversations": report["imported_conversations"] == 219 and history[0] == len(report["batches"]),
            "all_messages": report["imported_messages"] == 22776 and history[1] == episodes == historical,
            "all_consented_and_indexed": history[2] == history[0] and history[3] == 0 and indexed == episodes,
            "bounded_retrieval": 0 < len(retrieved) <= 3,
            "sqlite_quick_check": quick_check == "ok" and not foreign_keys,
            "model_calls_only_for_digest": attempts == len(receipts) and all(
                row["stage"] == "IMPORT_DIGEST" and row["status"] == "VALID" and row["http_status"] == 200
                for row in receipts),
            "digest_receipt_lineage": all(batch.content["receipt_id"] in receipt_ids
                or batch.content["receipt_id"] is None and not batch.content["accepted"]
                for batch in digests),
            "digest_progress_bounded": progress[0] <= episodes and progress[1] == len(digests),
        }
        result = {"checks": checks, "passed": all(checks.values()),
                  "batches": history[0], "conversations": report["imported_conversations"],
                  "messages": episodes, "indexed": indexed, "retrieval_results": len(retrieved),
                  "retrieval_ms": retrieval_ms, "model_attempts_total": attempts,
                  "digested_messages": progress[0], "digest_batches": progress[1],
                  "inferences": inferred, "failed_batches_without_request": failed_without_request,
                  "db_bytes": (terminal.root / "world.db").stat().st_size,
                  "source_sha256": report["source_sha256"]}
        destination = terminal.root / "claude_import_verification.json"
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["passed"] else 1
    finally:
        terminal.close()


if __name__ == "__main__":
    raise SystemExit(main())
