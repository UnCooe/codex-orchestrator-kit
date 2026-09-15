#!/usr/bin/env python3
"""Shadow-only Codex hook for task-to-worktree lifecycle bookkeeping.

This entrypoint is intentionally narrow: it records ownership at session or
prompt start when the worktree is clean, records a clean task as ready at
Stop, and queues a known dirty task for owner closure. Dirty or conflicting
worktrees only produce additional context. It never denies a tool, changes
Git content, commits, switches branches, or removes a worktree.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LIBRARY_ROOT = ROOT / "workspace-lifecycle"
if not LIBRARY_ROOT.is_dir():
    LIBRARY_ROOT = ROOT / "scripts"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

import workspace_lifecycle as lifecycle
import workspace_lifecycle_adapter as adapter


def _read_payload() -> dict[str, Any]:
    if sys.stdin.isatty():
        return {}
    text = sys.stdin.read()
    if not text.strip():
        return {}
    value = json.loads(text)
    return value if isinstance(value, dict) else {}


def _default_state_dir() -> Path:
    configured = os.environ.get("CODEX_WORKSPACE_STATE_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".codex" / "workspace-ledger-v1"


def _context(event: str, message: str) -> None:
    if not message or event not in {"SessionStart", "UserPromptSubmit"}:
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": f"Workspace lifecycle (shadow): {message}",
        }
    }, ensure_ascii=False, sort_keys=True))


def handle(payload: dict[str, Any], *, state_dir: str | Path | None = None) -> dict[str, Any]:
    state = state_dir or _default_state_dir()
    observed = adapter.handle_payload(payload, state_dir=state, mode=adapter.SHADOW)
    event = observed.get("event")
    task_id = str(observed.get("task_id") or "")
    worktree = str(observed.get("worktree") or "")
    action = "observe"

    if (
        task_id
        and worktree
        and observed.get("status") in {"clean", "ready_clean", "archived_clean"}
        and event in {"SessionStart", "UserPromptSubmit"}
    ):
        claimed = lifecycle.claim(
            worktree,
            task_id=task_id,
            thread_id=str(observed.get("thread_id") or task_id),
            state_dir=state,
        )
        observed["claim"] = {key: claimed.get(key) for key in ("ok", "status", "message", "state_file") if key in claimed}
        action = "claim" if claimed.get("ok") else "claim_rejected"
    elif task_id and worktree and observed.get("status") == "active_clean" and event == "Stop":
        transitioned = lifecycle.transition(worktree, task_id=task_id, state="ready", state_dir=state)
        observed["transition"] = {key: transitioned.get(key) for key in ("ok", "status", "message", "state_file") if key in transitioned}
        action = "ready" if transitioned.get("ok") else "ready_rejected"
    elif task_id and worktree and observed.get("status") == "active_dirty" and event == "Stop":
        pending = lifecycle.record_pending_closure(
            worktree,
            task_id=task_id,
            thread_id=str(observed.get("thread_id") or task_id),
            state_dir=state,
        )
        observed["pending_closure"] = {
            key: pending.get(key)
            for key in ("ok", "status", "message", "state_file", "queue_file", "queue_id")
            if key in pending
        }
        action = "pending_closure" if pending.get("ok") else "warn"
    elif observed.get("status") in {
        "unowned_dirty",
        "active_dirty",
        "handoff_clean",
        "handoff_dirty",
        "ready_dirty",
        "archived_dirty",
        "pending_closure_dirty",
        "quarantined_dirty",
    }:
        action = "warn"

    observed["action"] = action
    observed["mutated"] = action in {"claim", "ready", "pending_closure"}
    if action == "warn":
        _context(str(event or ""), str(observed.get("message") or ""))
    return observed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir")
    args = parser.parse_args(argv)
    try:
        result = handle(_read_payload(), state_dir=args.state_dir)
    except (lifecycle.LifecycleError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        result = {"ok": False, "action": "error", "message": str(exc), "mutated": False}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    # Successful clean observations stay silent; Codex does not need a prompt
    # injection for routine lifecycle bookkeeping.
    if result.get("action") not in {"warn"}:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
