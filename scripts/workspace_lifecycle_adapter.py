#!/usr/bin/env python3
"""Adapt Codex lifecycle hook payloads to the workspace ownership contract.

The adapter is deliberately side-effect free. It observes a worktree and
returns a decision that a future hook can surface or enforce. It does not
claim a task, create a commit, modify Git state, or remove a worktree. The
default ``shadow`` mode reports a would-block decision as a warning so the
contract can be exercised against real payloads before enabling enforcement.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

try:
    from workspace_lifecycle import LifecycleError, changed_paths, discover_worktree, load_binding
except ModuleNotFoundError:  # pragma: no cover - direct path execution fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from workspace_lifecycle import LifecycleError, changed_paths, discover_worktree, load_binding


SCHEMA_VERSION = 1
ADAPTER_SCHEMA_VERSION = 1
SHADOW = "shadow"
ENFORCE = "enforce"
VALID_MODES = {SHADOW, ENFORCE}
WRITE_TOOL_NAMES = {
    "apply_patch",
    "edit_file",
    "write_file",
    "write_stdin",
}
WRITE_COMMAND_RE = re.compile(
    r"(?is)(?:^|[\s;&|])(?:>>?|tee(?:\s|$)|sed\s+-i|perl\s+-i|"
    r"(?:rm|mv|cp|mkdir|touch|chmod|chown)\b|git\s+(?:add|commit|merge|rebase|"
    r"cherry-pick|pull|reset|checkout)\b|apply_patch\b)"
)
EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"}


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _environment_task_id(environ: Mapping[str, str] | None) -> str:
    if environ is None:
        environ = os.environ
    for key in ("CODEX_THREAD_ID", "CODEX_SESSION_ID"):
        value = _as_text(environ.get(key))
        if value:
            return value
    return ""


def task_id_from_payload(payload: dict[str, Any], *, environ: Mapping[str, str] | None = None) -> str:
    """Extract a stable task id without guessing from arbitrary fields."""

    for key in ("thread_id", "session_id", "task_id"):
        value = _as_text(payload.get(key))
        if value:
            return value
    session_meta = payload.get("session_meta")
    if isinstance(session_meta, dict):
        nested = session_meta.get("payload")
        if isinstance(nested, dict):
            value = _as_text(nested.get("id"))
            if value:
                return value
    return _environment_task_id(environ)


def identity_source_from_payload(payload: dict[str, Any], *, environ: Mapping[str, str] | None = None) -> str:
    for key in ("thread_id", "session_id", "task_id"):
        if _as_text(payload.get(key)):
            return "payload"
    session_meta = payload.get("session_meta")
    if isinstance(session_meta, dict):
        nested = session_meta.get("payload")
        if isinstance(nested, dict) and _as_text(nested.get("id")):
            return "payload"
    return "environment" if _environment_task_id(environ) else ""


def thread_id_from_payload(payload: dict[str, Any]) -> str:
    for key in ("thread_id", "session_id"):
        value = _as_text(payload.get(key))
        if value:
            return value
    return ""


def event_from_payload(payload: dict[str, Any]) -> str:
    return _as_text(payload.get("hook_event_name") or payload.get("event"))


def cwd_from_payload(payload: dict[str, Any]) -> str:
    for key in ("cwd", "workdir", "workspace", "workspace_path"):
        value = _as_text(payload.get(key))
        if value:
            return value
    return ""


def tool_name_from_payload(payload: dict[str, Any]) -> str:
    value = payload.get("tool_name") or payload.get("tool")
    if not isinstance(value, str):
        return ""
    return value.rsplit(".", 1)[-1].rsplit("__", 1)[-1].strip()


def _payload_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_payload_text(item) for item in value.values())
    if isinstance(value, list):
        return "\n".join(_payload_text(item) for item in value)
    return "" if value is None else str(value)


def command_from_payload(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input") or payload.get("input")
    if isinstance(tool_input, dict):
        for key in ("cmd", "command", "script"):
            value = _as_text(tool_input.get(key))
            if value:
                return value
    return _payload_text(tool_input)


def is_write_candidate(payload: dict[str, Any]) -> bool:
    tool = tool_name_from_payload(payload)
    if tool in WRITE_TOOL_NAMES:
        return True
    return bool(WRITE_COMMAND_RE.search(command_from_payload(payload)))


def _status(paths: list[str], binding: dict[str, Any] | None) -> str:
    if paths and not binding:
        return "unowned_dirty"
    if not binding:
        return "clean"
    state = _as_text(binding.get("state")) or "unknown"
    return f"{state}_{'dirty' if paths else 'clean'}"


def _base_result(payload: dict[str, Any], *, mode: str, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    event = event_from_payload(payload)
    task_id = task_id_from_payload(payload, environ=environ)
    cwd = cwd_from_payload(payload)
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "adapter_schema_version": ADAPTER_SCHEMA_VERSION,
        "event": event,
        "mode": mode,
        "task_id": task_id,
        "thread_id": thread_id_from_payload(payload) or _environment_task_id(environ),
        "identity_source": identity_source_from_payload(payload, environ=environ),
        "worktree": cwd,
        "decision": "allow",
        "status": "unknown",
        "changed_paths": [],
        "mutated": False,
    }
    if event not in EVENTS:
        result.update({"decision": "shadow_warn", "status": "unknown_event", "message": "unsupported lifecycle event"})
    if not task_id:
        result.update({"status": "unknown_task", "message": "no stable task id in hook payload; do not attribute changes by guess"})
    if not cwd:
        result.update({"status": "missing_cwd", "message": "hook payload has no worktree path"})
    return result


def handle_payload(
    payload: dict[str, Any],
    *,
    state_dir: str | Path | None = None,
    mode: str = SHADOW,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return a lifecycle decision for one hook payload.

    ``mode=enforce`` is intended only for an isolated canary at this stage.
    This function never changes the filesystem in either mode.
    """

    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")
    if not isinstance(payload, dict):
        raise TypeError("payload must be a JSON object")
    result = _base_result(payload, mode=mode, environ=environ)
    event = result["event"]
    cwd = result["worktree"]
    if result["status"] in {"unknown_task", "missing_cwd"}:
        result["decision"] = "shadow_warn"
        return result

    try:
        identity = discover_worktree(cwd)
        paths = changed_paths(identity["worktree"])
        binding = load_binding(identity["worktree"], state_dir)
    except (LifecycleError, OSError) as exc:
        result.update({"decision": "shadow_warn", "status": "out_of_scope", "message": str(exc)})
        return result

    status = _status(paths, binding)
    binding_task_id = _as_text(binding.get("task_id")) if binding else ""
    result.update({
        "worktree": identity["worktree"],
        "repo_root": identity["repo_root"],
        "branch": identity["branch"],
        "head": identity["head"],
        "status": status,
        "changed_paths": paths,
        "binding_task_id": binding_task_id,
    })

    task_id = result["task_id"]
    same_task = bool(binding_task_id and binding_task_id == task_id)
    active_binding = bool(binding and binding.get("state") in {"active", "handoff"})
    same_active_task = same_task and bool(binding and binding.get("state") == "active")

    if event in {"SessionStart", "UserPromptSubmit"}:
        if status == "unowned_dirty":
            result.update({"decision": "shadow_warn", "message": "dirty changes have no task owner; attribute them before writing"})
        elif active_binding and not same_task:
            result.update({"decision": "shadow_warn", "message": "another active task owns this worktree; use a dedicated worktree"})
        elif binding and binding.get("state") == "handoff" and same_task:
            result.update({"decision": "shadow_warn", "message": "handoff binding requires an explicit claim before resuming writes"})
        else:
            result["message"] = "workspace observed; no lifecycle state changed"
        return result

    if event == "PreToolUse":
        candidate = is_write_candidate(payload)
        result["write_candidate"] = candidate
        if not candidate:
            result["message"] = "tool is not classified as a write candidate"
            return result
        blocked = status == "unowned_dirty" or (active_binding and not same_active_task)
        needs_claim = not same_active_task
        if blocked or needs_claim:
            result.update({
                "decision": "deny" if mode == ENFORCE and blocked else "shadow_warn",
                "would_block": blocked,
                "requires_claim": needs_claim,
                "message": "claim the clean worktree or resolve its existing owner before writing",
            })
        else:
            result["message"] = "write is attributed to the active task"
        return result

    if event == "Stop":
        if status == "unowned_dirty":
            result.update({"decision": "shadow_warn", "message": "task stopped with unowned changes; preserve attribution before leaving"})
        elif active_binding and paths:
            result.update({"decision": "shadow_warn", "message": "task stopped dirty; transition to handoff or commit before ready"})
        elif active_binding and not paths:
            result.update({"decision": "shadow_warn", "message": "task stopped clean; record ready or archived explicitly"})
        else:
            result["message"] = "no owned dirty changes require a transition"
        return result

    return result


def _read_payload() -> dict[str, Any]:
    text = sys.stdin.read()
    if not text.strip():
        return {}
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("stdin must contain a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir")
    parser.add_argument("--mode", choices=sorted(VALID_MODES), default=SHADOW)
    args = parser.parse_args(argv)
    try:
        result = handle_payload(_read_payload(), state_dir=args.state_dir, mode=args.mode)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "status": "invalid_payload", "message": str(exc), "mutated": False}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
