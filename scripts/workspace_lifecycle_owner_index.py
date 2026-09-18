#!/usr/bin/env python3
"""Index exact worktree-creation events without reading conversation content.

The index is deliberately conservative.  It considers only direct session
events that executed ``git worktree add``.  Compacted history, user messages,
assistant text, search commands, and later ``git worktree list`` output are not
ownership evidence.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable


_CREATE_RE = re.compile(r"git\s+worktree\s+add\b", re.IGNORECASE)


def _session_meta(path: Path) -> tuple[str, str]:
    """Return session id and cwd from the direct session_meta event."""

    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict) or event.get("type") != "session_meta":
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    return "", ""
                return str(payload.get("session_id") or payload.get("id") or ""), str(payload.get("cwd") or "")
    except OSError:
        return "", ""
    return "", ""


def _direct_commands(event: dict[str, Any]) -> list[tuple[str, str]]:
    """Extract a command from a direct event, never from nested history."""

    event_type = event.get("type")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None

    if event_type == "response_item" and payload.get("type") == "custom_tool_call":
        if payload.get("name") != "exec" or not isinstance(payload.get("input"), str):
            return []
        source = payload["input"]
        commands: list[tuple[str, str]] = []
        # Tool calls use a JS object.  Decode each quoted cmd value so a
        # surrounding search string cannot be mistaken for the executed shell
        # command.  Unquoted ``cmd:`` is retained as a conservative fallback.
        for match in re.finditer(r"(?:['\"]?cmd['\"]?\s*:\s*)", source):
            start = match.end()
            if start < len(source) and source[start] == '"':
                try:
                    value, _ = json.JSONDecoder().raw_decode(source[start:])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, str):
                    commands.append((value, "custom_tool_call"))
            else:
                end = source.find(",", start)
                if end == -1:
                    end = source.find("}", start)
                if end > start:
                    commands.append((source[start:end].strip().strip("'\""), "custom_tool_call"))
        return commands

    if event_type == "event_msg":
        item = payload.get("item")
        if not isinstance(item, dict) or item.get("type") != "CommandExecution":
            return []
        command = item.get("command")
        if not isinstance(command, list):
            return []
        return [(" ".join(str(part) for part in command), "command_execution")]

    return []


def _matches_target(command: str, target: str) -> bool:
    """Match a target only when it follows the actual worktree-add verb."""

    # This rejects a command such as ``rg 'git worktree add' <target>`` while
    # allowing JS-wrapped exec calls and shell quoting around the target.
    escaped = re.escape(target)
    if not re.search(r"(?:^|[;&]\s*|(?:-lc|-c)\s+)git\s+worktree\s+add\b", command, re.IGNORECASE):
        return False
    return bool(re.search(r"git\s+worktree\s+add\b(?:(?!git\s+worktree\s+add).){0,800}?" + escaped + r"(?:['\"\s]|$)", command, re.IGNORECASE | re.DOTALL))


def index_sessions(
    sessions_root: str | Path,
    worktree_paths: Iterable[str | Path],
) -> dict[str, list[dict[str, Any]]]:
    """Return direct creation owners grouped by normalized worktree path."""

    targets = {str(Path(raw).expanduser().resolve()) for raw in worktree_paths}
    aliases: dict[str, str] = {}
    for raw in worktree_paths:
        path = Path(raw).expanduser()
        normalized = str(path.resolve())
        variants = {str(path.absolute()), normalized}
        # macOS commonly exposes /var and /tmp through /private symlinks; a
        # shell command may preserve either spelling of the same worktree.
        for value in list(variants):
            if value.startswith("/private/var/"):
                variants.add(value.removeprefix("/private"))
            if value.startswith("/private/tmp/"):
                variants.add(value.removeprefix("/private"))
        for variant in variants:
            aliases[variant] = normalized
    result: dict[str, list[dict[str, Any]]] = {target: [] for target in sorted(targets)}
    root = Path(sessions_root).expanduser()
    if not root.is_dir() or not targets:
        return result

    # Let ripgrep prefilter by the exact path.  This keeps routine inventory
    # fast even when the local session archive contains large transcripts.
    candidate_files: set[Path] = set()
    try:
        command = ["rg", "-l", "--glob", "*.jsonl", "-F"]
        for alias in aliases:
            command.extend(["-e", alias])
        command.append(str(root))
        scan = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError:
        scan = None
    if scan and scan.returncode in (0, 1):
        candidate_files.update(Path(line) for line in scan.stdout.splitlines() if line.strip())

    for session_file in sorted(candidate_files):
        session_id, cwd = _session_meta(session_file)
        if not session_id:
            continue
        try:
            with session_file.open(encoding="utf-8", errors="replace") as handle:
                for line_number, raw in enumerate(handle, 1):
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    for command, evidence_kind in _direct_commands(event):
                        for alias, target in aliases.items():
                            if not _matches_target(command, alias):
                                continue
                            evidence = {
                                "session_id": session_id,
                                "thread_id": session_id,
                                "cwd": cwd,
                                "session_file": str(session_file),
                                "line": line_number,
                                "timestamp": str(event.get("timestamp") or ""),
                                "evidence_kind": evidence_kind,
                            }
                            # A single command can be represented by both the
                            # tool-call and command-completion event; retain each
                            # direct observation but expose one owner below.
                            result[target].append(evidence)
                            break
        except OSError:
            continue

    return result


def unique_owner(evidence: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Return one owner only when all direct evidence names one session."""

    items = list(evidence)
    session_ids = sorted({str(item.get("session_id") or "") for item in items if item.get("session_id")})
    if len(session_ids) != 1:
        return None
    selected = next(item for item in items if item.get("session_id") == session_ids[0])
    return {
        "session_id": session_ids[0],
        "thread_id": str(selected.get("thread_id") or session_ids[0]),
        "cwd": str(selected.get("cwd") or ""),
        "evidence_count": len(items),
        "evidence_kinds": sorted({str(item.get("evidence_kind") or "") for item in items}),
        "first_timestamp": min(str(item.get("timestamp") or "") for item in items),
        "last_timestamp": max(str(item.get("timestamp") or "") for item in items),
    }
