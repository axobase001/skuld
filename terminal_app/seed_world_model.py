"""Bounded initial V4 digestion of imported history, with real provider receipts."""

from __future__ import annotations

import json
import os
from datetime import timedelta
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .secret_loader import load_deepseek_key
from .v4_wiring import V4Terminal
from skuld.kernel.primitives import instant


def _run():
    repository = Path(__file__).parents[1]
    terminal = V4Terminal(repository / ".terminal-runtime", repository,
        load_deepseek_key(repository))
    try:
        rt, user_id = terminal.runtime, terminal.user_id
        report = json.loads((terminal.root / "claude_import_report.json").read_text(encoding="utf-8"))
        if report.get("status") != "IMPORTED_ALL_HISTORY":
            raise RuntimeError("Historical import must finish before digestion")
        if not terminal.key:
            raise RuntimeError("DeepSeek key unavailable")
        def record(result):
            result["recorded_at_utc"] = datetime.now(timezone.utc).isoformat()
            wire = json.dumps(result, ensure_ascii=False)
            (terminal.root / "claude_world_seed_report.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            with (terminal.root / "claude_world_digest_runs.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(wire + "\n")
            print(wire)
            return 0
        progress_before = rt.store._conn.execute(
            "SELECT COALESCE(SUM(digested),0) FROM runtime_history WHERE user_id=?",
            (user_id,)).fetchone()[0]
        inferred_before = sum(len(n.content["accepted"]) for n in rt.nodes(user_id, "DigestBatch"))
        if progress_before >= report["imported_messages"]:
            return record({"status": "COMPLETE", "digested_messages_total": progress_before,
                           "historical_messages": report["imported_messages"],
                           "inferences_total": inferred_before})
        if inferred_before >= 250:
            return record({"status": "PAUSED_GRAPH_CAP", "digested_messages_total": progress_before,
                           "historical_messages": report["imported_messages"],
                           "inferences_total": inferred_before})
        def balance():
            response = httpx.get("https://api.deepseek.com/user/balance",
                headers={"Authorization": "Bearer " + terminal.key}, timeout=15)
            response.raise_for_status()
            body = response.json()
            items = body.get("balance_infos", [])
            cny = next((float(item["total_balance"]) for item in items
                        if item.get("currency") == "CNY"), None)
            if not body.get("is_available") or cny is None:
                raise RuntimeError("DeepSeek official balance unavailable")
            return cny
        balance_before = balance()
        if balance_before < 10.0:
            return record({"status": "PAUSED_BALANCE_RESERVE", "official_balance_cny": balance_before,
                           "minimum_cny": 10.0, "digested_messages_total": progress_before,
                           "historical_messages": report["imported_messages"],
                           "inferences_total": inferred_before})
        rt.add_channel(user_id, "conversation", {})
        # A small, explicit first slice. The remaining episodes stay searchable as
        # historical evidence; the product's own hourly and import budgets remain.
        before = rt.model_call_usage()
        recent = rt.store._conn.execute(
            "SELECT COUNT(*) FROM runtime_digest_log WHERE user_id=? AND at>?",
            (user_id, (instant(rt.clock()) - timedelta(hours=1)).isoformat()),
        ).fetchone()[0]
        # Both guards must stop at the same boundary. A lower model-call guard
        # alone produces an extra failed DigestBatch with no provider request.
        if before >= 100:
            return record({"status": "PAUSED_CALL_CAP", "provider_attempts_total": before,
                           "digested_messages_total": progress_before,
                           "historical_messages": report["imported_messages"],
                           "inferences_total": inferred_before})
        allowance = min(3, 12 - recent, 100 - before)
        if allowance <= 0:
            return record({"status": "HOURLY_GUARD", "provider_attempts_total": before,
                           "digested_messages_total": progress_before,
                           "historical_messages": report["imported_messages"],
                           "inferences_total": inferred_before})
        rt.config["digest_batches_per_hour"] = recent + allowance
        rt.config["max_model_calls"] = before + allowance
        batch_ids = rt._digest(user_id)
        after = rt.model_call_usage()
        batches = [n for n in rt.nodes(user_id, "DigestBatch") if n.node_id in batch_ids]
        progress = rt.store._conn.execute(
            "SELECT COALESCE(SUM(digested),0),COALESCE(SUM(batches_used),0) FROM runtime_history WHERE user_id=?",
            (user_id,)).fetchone()
        inferred_total = sum(len(n.content["accepted"]) for n in rt.nodes(user_id, "DigestBatch"))
        result = {"status": "SEEDED_BOUNDED" if batch_ids else "NO_PROGRESS",
            "provider_attempts_before": before,
            "provider_attempts_after": after, "new_batches": len(batch_ids),
            "official_balance_cny_before": balance_before,
            "official_balance_cny_after": balance(),
            "accepted_inferences": sum(len(n.content["accepted"]) for n in batches),
            "proposed_inferences": sum(n.content["proposed"] for n in batches),
            "historical_messages": report["imported_messages"],
            "digested_messages_total": progress[0], "digest_batches_total": progress[1],
            "inferences_total": inferred_total,
            "full_history_digestion_claimed": False,
            "database_quick_check": rt.store._conn.execute("PRAGMA quick_check").fetchone()[0]}
        if result["database_quick_check"] != "ok":
            raise RuntimeError("SQLite quick_check failed after digest")
        return record(result)
    except Exception as exc:
        result = {"status": "BLOCKED_BEFORE_OR_DURING_DIGEST", "error_type": type(exc).__name__,
                  "error_detail": str(exc)[:250],
                  "provider_attempts_total": terminal.runtime.model_call_usage(),
                  "full_history_digestion_claimed": False}
        (terminal.root / "claude_world_seed_report.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 1
    finally:
        terminal.close()


def main():
    root = Path(__file__).parents[1] / ".terminal-runtime"
    lock_path = root / "claude_world_digest.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(json.dumps({"status": "BUSY", "reason": "another digest invocation holds the lock"}))
            return 0
        try:
            return _run()
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == "nt":
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
