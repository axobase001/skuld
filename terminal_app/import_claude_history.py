"""Stream a Claude export into V4's historical graph in atomic, resumable batches."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from skuld.app.import_runtime import iter_conversations

from .secret_loader import load_deepseek_key
from .v4_wiring import V4Terminal


MAX_MESSAGES = 500
MAX_RAW_BYTES = 8 * 1024 * 1024


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def save_report(path: Path, report: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parents[1] / ".terminal-runtime")
    parser.add_argument("--workspace", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--max-batches", type=int, default=0, help="0 means all; positive value supports staged runs")
    args = parser.parse_args(argv)
    source = args.export.resolve(strict=True)
    if args.max_batches < 0:
        raise ValueError("max-batches must be nonnegative")
    source_hash = file_hash(source)
    terminal = V4Terminal(args.data_dir, args.workspace, load_deepseek_key(Path(__file__).parents[1]))
    rt, user_id = terminal.runtime, terminal.user_id
    report_path = terminal.root / "claude_import_report.json"
    report = {"source_sha256": source_hash, "source_bytes": source.stat().st_size,
              "format": "claude_export", "batch_max_messages": MAX_MESSAGES,
              "batch_max_raw_bytes": MAX_RAW_BYTES, "batches": [], "status": "RUNNING"}
    if report_path.exists():
        old = json.loads(report_path.read_text(encoding="utf-8"))
        if old.get("source_sha256") != source_hash:
            raise RuntimeError("A different export was already associated with this workspace")
    save_report(report_path, report)
    batch: list[dict] = []
    messages = 0
    raw_bytes = 0
    batch_index = 0

    def flush():
        nonlocal batch, messages, raw_bytes, batch_index
        if not batch:
            return
        batch_index += 1
        handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json",
            prefix="claude-batch-", dir=terminal.root, delete=False)
        temporary = Path(handle.name)
        try:
            json.dump(batch, handle, ensure_ascii=False)
            handle.close()
            preview = rt.preview_import(user_id, temporary)
            ids = [item["id"] for item in preview["conversations"]]
            if not ids:
                raise ValueError("batch has no importable conversations")
            with rt.lock, rt.store.transaction():
                result = rt.import_file(user_id, temporary, preview["digest"], ids,
                    local_storage_consent=True)
                rt.consent_history(user_id, result["import_id"],
                    "用户要求将与 Claude 的历史对话分批导入 Skuld，作为可检索的初始世界模型证据；历史内容不是当前授权。")
            status = rt.import_status(user_id, result["import_id"])
            check = rt.store._conn.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok" or status["episodes"] != result["imported_messages"]:
                raise RuntimeError("batch database or import count check failed")
            entry = {"batch": batch_index, "import_id": result["import_id"],
                     "conversations": result["imported_conversations"], "messages": result["imported_messages"],
                     "replayed": result["replayed"], "status": status["status"],
                     "db_bytes": (terminal.root / "world.db").stat().st_size,
                     "quick_check": check}
            report["batches"].append(entry)
            report["imported_conversations"] = sum(x["conversations"] for x in report["batches"])
            report["imported_messages"] = sum(x["messages"] for x in report["batches"])
            save_report(report_path, report)
            print(json.dumps({"batch": batch_index, "conversations": entry["conversations"],
                "messages": entry["messages"], "replayed": entry["replayed"],
                "db_mb": round(entry["db_bytes"] / 1048576, 1)}), flush=True)
        finally:
            handle.close()
            temporary.unlink(missing_ok=True)
        batch, messages, raw_bytes = [], 0, 0

    try:
        for conversation in iter_conversations(source):
            if not isinstance(conversation, dict) or "chat_messages" not in conversation:
                raise ValueError("source format changed mid-stream")
            count = len(conversation.get("chat_messages") or [])
            size = len(json.dumps(conversation, ensure_ascii=False))
            if batch and (messages + count > MAX_MESSAGES or raw_bytes + size > MAX_RAW_BYTES):
                flush()
                if args.max_batches and batch_index >= args.max_batches:
                    report["status"] = "PAUSED_AT_BATCH_BOUNDARY"
                    save_report(report_path, report)
                    return 0
            batch.append(conversation)
            messages += count
            raw_bytes += size
        flush()
        report["status"] = "IMPORTED_ALL_HISTORY"
        report["finished_at"] = time.time()
        save_report(report_path, report)
        return 0
    except Exception as exc:
        report["status"] = "FAILED_AT_BATCH_BOUNDARY"
        report["error_type"] = type(exc).__name__
        report["error_detail"] = str(exc)[:300]
        save_report(report_path, report)
        raise
    finally:
        terminal.close()


if __name__ == "__main__":
    raise SystemExit(main())
