"""Repeatable end-to-end check of two private local accounts and default isolation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .accounts import account_paths
from .v4_wiring import V4Terminal


def main() -> int:
    repository = Path(__file__).parents[1]
    base = repository / ".terminal-e2e-accounts"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir()
    alice = account_paths(base, "alice")
    bob = account_paths(base, "bob")
    default = account_paths(base, "default")
    alice.workspace.mkdir(parents=True)
    bob.workspace.mkdir(parents=True)
    checks = {}
    try:
        account_paths(base, "../alice")
        checks["profile_path_traversal_denied"] = False
    except ValueError:
        checks["profile_path_traversal_denied"] = True
    try:
        account_paths(base, "default", alice.workspace)
        checks["default_workspace_cannot_select_other_profile"] = False
    except ValueError:
        checks["default_workspace_cannot_select_other_profile"] = True
    a = V4Terminal(alice.runtime, alice.workspace, None, "alice")
    b = V4Terminal(bob.runtime, bob.workspace, None, "bob")
    d = V4Terminal(default.runtime, default.workspace, None, "default")
    try:
        checks["separate_graphs_and_sessions"] = (
            len({a.user_id, b.user_id, d.user_id}) == 3
            and len({a.session_id, b.session_id, d.session_id}) == 3)
        checks["separate_runtime_files"] = len({a.root, b.root, d.root}) == 3
        checks["no_inherited_key"] = all(t.key is None for t in (a, b, d))
        for t in (a, b, d):
            t.change_mode("ACT")
        action = a.propose_write("note.txt", "alice private evidence")
        try:
            b.approve_write(action)
            checks["approval_cannot_cross_account"] = False
        except ValueError:
            checks["approval_cannot_cross_account"] = True
        result = a.approve_write(action)
        checks["pi_write_in_owner_workspace"] = (
            result["result"]["status"] == "COMPLETED"
            and (alice.workspace / "note.txt").read_text(encoding="utf-8") == "alice private evidence")
        for name, terminal in (("other_profile", b), ("default_profile", d)):
            try:
                terminal.read(str(alice.workspace / "note.txt"))
                checks[name + "_cannot_read_alice"] = False
            except PermissionError:
                checks[name + "_cannot_read_alice"] = True
        checks["pi_receipt_only_in_owner_graph"] = (
            len(a.runtime.nodes(a.user_id, "PiToolOutcome")) == 1
            and all(not any(n.content.get("status") == "COMPLETED"
                            for n in t.runtime.nodes(t.user_id, "PiToolOutcome")) for t in (b, d)))
        checks["sqlite_integrity"] = all(
            t.runtime.store._conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            for t in (a, b, d))
        identity = (a.user_id, a.session_id)
    finally:
        a.close()
        b.close()
        d.close()
    recovered = V4Terminal(alice.runtime, alice.workspace, None, "alice")
    try:
        checks["owner_state_survives_restart"] = (
            (recovered.user_id, recovered.session_id) == identity
            and recovered.mode == "ACT"
            and len(recovered.state()["recent_outcomes"]) == 1)
    finally:
        recovered.close()
    result = {"passed": all(checks.values()), "checks": checks,
              "artifact": str(base / "result.json")}
    (base / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
