#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import fnmatch
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATE_RELATIVE_PATH = Path(".codex-local") / "merge-safety" / "current.json"
IGNORED_LOCAL_PREFIXES = (".codex-local/",)
CODE_EDIT_TOOL_NAMES = {"apply_patch", "edit", "write"}
SHELL_TOOL_NAMES = {"bash", "shell", "exec", "exec_command", "functions.exec_command"}
READ_ONLY_GIT_RE = re.compile(
    r"(?is)^\s*(?:"
    r"git\s+(?:status|fetch|merge-base|log|diff|range-diff|show|rev-parse|branch|remote|ls-remote|for-each-ref|name-rev)\b"
    r"|gh\s+pr\s+(?:view|diff|checks|status)\b"
    r")"
)
CONFLICT_MARKER_RE = re.compile(r"^(<<<<<<<|=======|>>>>>>>)(?: .*)?$", re.MULTILINE)
POSITIVE_REVIEW_RE = re.compile(r"(?i)\b(pass|passed|approved|reviewed|no p1-now|no p1 now|无\s*p1-now|无阻塞)\b")
POSITIVE_TEST_RE = re.compile(r"(?i)\b(test|tests|pytest|go test|npm test|validated|verification|py_compile|not run|could not run|无法运行|未运行|验证)\b")
P1_NOW_RE = re.compile(r"(?i)\bp1[-_ ]?now\b|P1-now|\bblocked\b|\bblockers?\b|(?<!无)阻塞")
NO_P1_RE = re.compile(r"(?i)no\s+p1[-_ ]?now|no\s+blockers?|无\s*p1|无阻塞|无\s*阻塞")
ACTIVE_WINDOW_HOURS = 12
PROTECTED_INTEGRATION_REFS = {"pre", "test", "master", "main", "origin/pre", "origin/test", "origin/master", "origin/main"}
HOTFIX_MARKER_RE = re.compile(r"(?i)\bhotfix\b|\bhot\s+fix\b|热补丁|直接在\s*(?:pre|test|master|main)\s*上修")
MERGE_GUARD_SETUP_RE = re.compile(r"(?i)(?:^|\s)(?:python(?:\d+(?:\.\d+)?)?\s+)?\S*merge_guard\.py\s+(?:init|check-pre)\b")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _fail(message: str, **extra: Any) -> int:
    payload = {"ok": False, "message": message}
    payload.update(extra)
    print(_json(payload), end="")
    return 1


def _ok(message: str, **extra: Any) -> int:
    payload = {"ok": True, "message": message}
    payload.update(extra)
    print(_json(payload), end="")
    return 0


def _with_next_action(payload: dict[str, Any], next_action: str | None) -> dict[str, Any]:
    if next_action:
        payload["next_action"] = next_action
    return payload


def _git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _git_value(root: Path, args: list[str]) -> str:
    result = _git(root, args)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _git_lines(root: Path, args: list[str]) -> list[str]:
    result = _git(root, args)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _repo_root(cwd: str | None = None) -> Path:
    start = Path(cwd or os.getcwd()).expanduser()
    result = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "not inside a git repository")
    return Path(result.stdout.strip()).resolve()


def _resolve_cwd(path: str | None, base: str | None = None) -> str | None:
    if not path or not str(path).strip():
        return None
    raw = Path(str(path).strip()).expanduser()
    if not raw.is_absolute() and base:
        raw = Path(base).expanduser() / raw
    return str(raw.resolve())


def _state_path(root: Path) -> Path:
    return root / STATE_RELATIVE_PATH


def _load_state(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    if not path.exists():
        raise RuntimeError(f"merge safety intent is missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise RuntimeError("merge safety intent must be a JSON object")
    return _normalize_state(data, refresh_updated_at=False)


def _write_state(root: Path, data: dict[str, Any]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json(data), encoding="utf-8")


def _clear_state(root: Path) -> None:
    path = _state_path(root)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _list_arg(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in re.split(r"[,\n]+", value) if item.strip()]


def _status_porcelain(root: Path) -> list[str]:
    result = _git(root, ["status", "--porcelain", "--untracked-files=all"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git status failed")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _porcelain_path(line: str) -> str:
    if len(line) <= 3:
        return line.strip()
    path = line[3:].strip()
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[-1].strip()
    return path.strip('"')


def _dirty_files(root: Path) -> list[str]:
    return [
        line
        for line in _status_porcelain(root)
        if not _is_ignored_local_path(_porcelain_path(line))
    ]


def _dirty_report(root: Path) -> dict[str, Any]:
    raw = _status_porcelain(root)
    blocking: list[str] = []
    ignored_tool_state: list[str] = []
    for line in raw:
        path = _porcelain_path(line)
        if _is_ignored_local_path(path):
            ignored_tool_state.append(line)
        else:
            blocking.append(line)
    if blocking:
        classification = "code_dirty"
    elif ignored_tool_state:
        classification = "tool_state_only"
    else:
        classification = "clean"
    return {
        "classification": classification,
        "blocking": blocking,
        "ignored_tool_state": ignored_tool_state,
        "raw": raw,
    }


def _changed_files(root: Path, target_ref: str, source_ref: str) -> list[str]:
    diff_args = ["diff", "--name-only", f"{target_ref}...{source_ref}"]
    result = _git(root, diff_args)
    if result.returncode != 0:
        diff_args = ["diff", "--name-only", f"{target_ref}..{source_ref}"]
        result = _git(root, diff_args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _commit_changed_files(root: Path, source_ref: str) -> list[str]:
    result = _git(root, ["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", source_ref])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff-tree failed")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _planned_changed_files(root: Path, state: dict[str, Any]) -> list[str]:
    target_ref = str(state.get("target_ref", "")).strip()
    source_ref = str(state.get("source_ref", "")).strip()
    merge_mode = str(state.get("merge_mode", "")).lower()
    if not target_ref or not source_ref:
        return []
    if "cherry-pick" in merge_mode:
        return _commit_changed_files(root, source_ref)
    return _changed_files(root, target_ref, source_ref)


def _resolve_ref(root: Path, ref: str) -> str:
    ref = str(ref or "").strip()
    if not ref:
        return ""
    return _git_value(root, ["rev-parse", "--verify", ref])


def _left_only_commits(root: Path, left_ref: str, right_ref: str) -> list[dict[str, str]]:
    left = str(left_ref or "").strip()
    right = str(right_ref or "").strip()
    if not left or not right:
        return []
    result = _git(root, ["log", "--left-right", "--cherry-pick", "--oneline", f"{left}...{right}"])
    if result.returncode != 0:
        return [{"commit": "", "summary": result.stderr.strip() or "git log --left-right failed"}]
    commits: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("<"):
            continue
        body = line[1:].strip()
        if not body:
            continue
        parts = body.split(" ", 1)
        commits.append({"commit": parts[0], "summary": parts[1] if len(parts) > 1 else ""})
    return commits


def _commit_patch_id(root: Path, ref: str) -> str:
    ref = str(ref or "").strip()
    if not ref:
        return ""
    show = _git(root, ["show", "--format=", "--no-ext-diff", "--binary", ref])
    if show.returncode != 0:
        return ""
    patch_id = subprocess.run(
        ["git", "patch-id", "--stable"],
        input=show.stdout,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if patch_id.returncode != 0:
        return ""
    first = patch_id.stdout.strip().split()
    return first[0] if first else ""


def _single_patch_equivalent(root: Path, required_ref: str, result_ref: str) -> bool:
    required_oid = _resolve_ref(root, required_ref)
    result_oid = _resolve_ref(root, result_ref)
    if not required_oid or not result_oid:
        return False
    return bool(_commit_patch_id(root, required_oid) and _commit_patch_id(root, required_oid) == _commit_patch_id(root, result_oid))


def _ref_integrated(root: Path, required_ref: str, result_ref: str) -> dict[str, Any]:
    required = str(required_ref or "").strip()
    result = str(result_ref or "").strip()
    details: dict[str, Any] = {
        "required_ref": required,
        "result_ref": result,
        "required_oid": _resolve_ref(root, required),
        "result_oid": _resolve_ref(root, result),
        "ancestor": False,
        "patch_equivalent": False,
        "single_patch_equivalent": False,
        "left_only_commits": [],
        "ok": False,
    }
    if not required or not result:
        details["error"] = "required_ref and result_ref are required"
        return details
    ancestor = _git(root, ["merge-base", "--is-ancestor", required, result])
    details["ancestor"] = ancestor.returncode == 0
    left_only = _left_only_commits(root, required, result)
    details["left_only_commits"] = left_only
    details["patch_equivalent"] = not left_only
    details["single_patch_equivalent"] = _single_patch_equivalent(root, required, result)
    details["ok"] = details["ancestor"] or details["patch_equivalent"]
    return details


def _has_unmerged_paths(root: Path) -> bool:
    result = _git(root, ["diff", "--name-only", "--diff-filter=U"])
    if result.returncode != 0:
        return False
    return any(line.strip() for line in result.stdout.splitlines())


def _ref_name(ref: str) -> str:
    raw = str(ref or "").strip()
    if raw.startswith("refs/heads/"):
        raw = raw.removeprefix("refs/heads/")
    if raw.startswith("refs/remotes/"):
        raw = raw.removeprefix("refs/remotes/")
    return raw


def _is_protected_ref(ref: str) -> bool:
    name = _ref_name(ref)
    return name in PROTECTED_INTEGRATION_REFS or name.split("/", 1)[0] in {"pre", "test", "master", "main"}


def _hotfix_markers(values: list[Any]) -> list[str]:
    markers: list[str] = []
    for value in values:
        text = " ".join(str(item) for item in value) if isinstance(value, list) else str(value or "")
        if HOTFIX_MARKER_RE.search(text):
            markers.append(text.strip())
    return markers


def _worktree_changed_files(root: Path) -> list[str]:
    result = _git(root, ["diff", "--name-only", "--cached"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff --cached failed")
    staged = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    result = _git(root, ["diff", "--name-only"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    unstaged = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    result = _git(root, ["ls-files", "--others", "--exclude-standard"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-files failed")
    untracked = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return sorted(path for path in set(staged + unstaged + untracked) if not _is_ignored_local_path(path))


def _is_ignored_local_path(path: str) -> bool:
    normalized = path.strip().lstrip("/")
    return any(normalized.startswith(prefix) for prefix in IGNORED_LOCAL_PREFIXES)


def _match_any(path: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    normalized = path.strip("/")
    for pattern in patterns:
        clean = pattern.strip().strip("/")
        if not clean:
            continue
        if fnmatch.fnmatch(normalized, clean) or fnmatch.fnmatch(normalized, clean.rstrip("/") + "/*"):
            return True
        if normalized == clean or normalized.startswith(clean.rstrip("/") + "/"):
            return True
    return False


def _blocked_by_allowlist(files: list[str], allowlist: list[str]) -> list[str]:
    if not allowlist:
        return []
    return [path for path in files if not _is_ignored_local_path(path) and not _match_any(path, allowlist)]


def _blocked_by_denylist(files: list[str], denylist: list[str]) -> list[str]:
    return [path for path in files if not _is_ignored_local_path(path) and _match_any(path, denylist)]


def _conflict_candidate_files(root: Path) -> list[str]:
    result = _git(root, ["grep", "-l", "-I", r"^<<<<<<< "])
    if result.returncode not in {0, 1}:
        return []
    files = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    untracked = _git(root, ["ls-files", "--others", "--exclude-standard"])
    if untracked.returncode == 0:
        for path in untracked.stdout.splitlines():
            path = path.strip()
            if not path or _is_ignored_local_path(path):
                continue
            try:
                text = (root / path).read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if re.search(r"^<<<<<<< ", text, re.MULTILINE):
                files.add(path)
    return sorted(files)


def _conflict_marker_hits(root: Path) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for path in _conflict_candidate_files(root):
        try:
            lines = (root / path).read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        index = 0
        while index < len(lines):
            line = lines[index]
            if not line.startswith("<<<<<<< "):
                index += 1
                continue
            mid = end = None
            scan_end = min(len(lines), index + 2000)
            for cursor in range(index + 1, scan_end):
                if mid is None and lines[cursor] == "=======":
                    mid = cursor
                    continue
                if mid is not None and lines[cursor].startswith(">>>>>>> "):
                    end = cursor
                    break
            if mid is not None and end is not None:
                hits.append({
                    "file": path,
                    "start_line": index + 1,
                    "middle_line": mid + 1,
                    "end_line": end + 1,
                })
                index = end + 1
            else:
                index += 1
    return hits


def _conflict_files(root: Path) -> list[str]:
    return sorted({hit["file"] for hit in _conflict_marker_hits(root)})


def _has_conflict_markers(root: Path) -> bool:
    return bool(_conflict_files(root))


def _normalize_state(data: dict[str, Any], *, refresh_updated_at: bool = True) -> dict[str, Any]:
    data.setdefault("schema_version", 1)
    # Keep the state contract aligned with critical_action_gate.py. Protected
    # branch integration is performed from a source/integration worktree and
    # culminates in one explicit push to the declared target ref.
    data.setdefault("kind", "merge_safety_intent")
    data.setdefault("operation", "push")
    data.setdefault("created_at", _now())
    if refresh_updated_at:
        data["updated_at"] = _now()
    else:
        data.setdefault("updated_at", data["created_at"])
    data.setdefault("goal", "")
    data.setdefault("target_ref", "")
    data.setdefault("source_ref", "")
    data.setdefault("merge_mode", "")
    data.setdefault("allowed_paths", [])
    data.setdefault("forbidden_paths", [])
    data.setdefault("forbidden_changes", [])
    data.setdefault("acceptance", [])
    data.setdefault("unknowns", [])
    data.setdefault("user_confirmed_unknowns_clear", False)
    data.setdefault("tests", [])
    data.setdefault("review", {"status": "", "summary": "", "p1_now": []})
    data.setdefault("subagents", {"status": "", "closed": False, "summary": ""})
    data.setdefault("planned_changed_files", [])
    data.setdefault("last_pre_check", {})
    data.setdefault("last_post_check", {})
    data.setdefault("source_head", "")
    data.setdefault("target_head", "")
    data.setdefault("validated_ref", "")
    data.setdefault("validated_head", "")
    data.setdefault("validated_merge_base", "")
    data.setdefault("validated_only_commits", [])
    data.setdefault("lineage_proof", {})
    data.setdefault("patch_equivalent_result_allowed", False)
    data.setdefault("intent_origin", "feature")
    data.setdefault("hotfix_requested_by_user", False)
    data.setdefault("validation_mode", "feature-only")
    data.setdefault("evidence", {})
    data.setdefault("active", True)
    data.setdefault("base_head", "")
    data.setdefault("merge_started", False)
    return data


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _state_is_active(state: dict[str, Any]) -> bool:
    if state.get("active") is False:
        return False
    updated = _parse_time(state.get("updated_at")) or _parse_time(state.get("created_at"))
    if not updated:
        return True
    age = datetime.now(timezone.utc) - updated
    return age.total_seconds() <= ACTIVE_WINDOW_HOURS * 3600


def _state_unknowns_open(state: dict[str, Any]) -> list[str]:
    unknowns = state.get("unknowns", [])
    if isinstance(unknowns, str):
        unknowns = [unknowns]
    if not isinstance(unknowns, list):
        return ["unknowns field is invalid"]
    return [str(item).strip() for item in unknowns if str(item).strip()]


def _review_p1_now(state: dict[str, Any]) -> list[str]:
    review = state.get("review", {})
    if not isinstance(review, dict):
        return ["review field is invalid"]
    p1 = review.get("p1_now", [])
    if isinstance(p1, str):
        p1 = [p1] if p1.strip() else []
    if not isinstance(p1, list):
        return ["review.p1_now is invalid"]
    summary = str(review.get("summary", ""))
    status = str(review.get("status", ""))
    found = [str(item).strip() for item in p1 if str(item).strip()]
    if P1_NOW_RE.search(summary) and not NO_P1_RE.search(summary):
        found.append("review summary mentions P1-now")
    if P1_NOW_RE.search(status) and not NO_P1_RE.search(status):
        found.append("review status mentions P1-now")
    return found


def _state_has_tests(state: dict[str, Any], assistant_message: str = "") -> bool:
    tests = state.get("tests", [])
    if isinstance(tests, str):
        tests = [tests]
    if isinstance(tests, list) and any(str(item).strip() for item in tests):
        return True
    return bool(POSITIVE_TEST_RE.search(assistant_message))


def _state_has_review(state: dict[str, Any], assistant_message: str = "") -> bool:
    review = state.get("review", {})
    if isinstance(review, dict):
        if POSITIVE_REVIEW_RE.search(str(review.get("status", ""))):
            return True
        if POSITIVE_REVIEW_RE.search(str(review.get("summary", ""))):
            return True
    return bool(POSITIVE_REVIEW_RE.search(assistant_message))


def _subagents_closed(state: dict[str, Any], assistant_message: str = "") -> bool:
    subagents = state.get("subagents", {})
    if isinstance(subagents, dict):
        if subagents.get("closed") is True:
            return True
        status = str(subagents.get("status", "")).lower()
        if "closed" in status or "not_used" in status or "not used" in status:
            return True
    lower = assistant_message.lower()
    return "subagent_status=closed" in lower or "subagent_status=not_used" in lower


def _read_payload(path: str | None) -> dict[str, Any]:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not sys.stdin.isatty():
        text = sys.stdin.read()
        if text.strip():
            return json.loads(text)
    return {}


def _load_jsonl(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    items: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def _current_turn_items(transcript_path: str | None) -> list[dict[str, Any]]:
    items = _load_jsonl(transcript_path)
    last_user_index = -1
    for index, item in enumerate(items):
        payload = item.get("payload", {}) if isinstance(item.get("payload"), dict) else {}
        if payload.get("type") == "message" and payload.get("role") == "user":
            content_text = str(payload.get("content", ""))
            if "<hook_prompt" not in content_text:
                last_user_index = index
    if last_user_index < 0:
        return []
    return items[last_user_index + 1 :]


def _payload_cwd(payload: dict[str, Any]) -> str | None:
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) and cwd.strip() else None


def _payload_tool_name(payload: dict[str, Any]) -> str:
    raw = payload.get("tool_name")
    if not isinstance(raw, str):
        return ""
    name = raw.rsplit("__", 1)[-1].strip().lower()
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    return name


def _transcript_tool_payload(item: dict[str, Any]) -> dict[str, Any] | None:
    payload = item.get("payload", {}) if isinstance(item.get("payload"), dict) else {}
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        raw = payload.get("arguments")
        if isinstance(raw, str):
            try:
                arguments = json.loads(raw)
            except Exception:
                arguments = {}
        else:
            arguments = {}
    return {
        "cwd": payload.get("cwd"),
        "tool_name": name,
        "tool_input": arguments,
    }


def _payload_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_payload_text(v) for v in value.values())
    if isinstance(value, list):
        return "\n".join(_payload_text(v) for v in value)
    return "" if value is None else str(value)


def _shell_statements(command: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    heredoc_pending = False
    heredoc_token = ""
    skip_until_heredoc_end = False

    for raw_line in command.splitlines():
        line = raw_line.rstrip("\n")
        if skip_until_heredoc_end:
            if line.strip() == heredoc_token:
                skip_until_heredoc_end = False
                heredoc_token = ""
            continue
        if heredoc_pending:
            skip_until_heredoc_end = True
            heredoc_pending = False
            if line.strip() == heredoc_token:
                skip_until_heredoc_end = False
                heredoc_token = ""
            continue
        index = 0
        while index < len(line):
            char = line[index]
            if escaped:
                current.append(char)
                escaped = False
            elif char == "\\":
                current.append(char)
                escaped = True
            elif char == "'" and not in_double:
                current.append(char)
                in_single = not in_single
            elif char == '"' and not in_single:
                current.append(char)
                in_double = not in_double
            elif not in_single and not in_double and char in ";&|()":
                stmt = "".join(current).strip()
                if stmt:
                    statements.append(stmt)
                    token = _heredoc_token(stmt)
                    if token:
                        heredoc_pending = True
                        heredoc_token = token
                current = []
            else:
                current.append(char)
            index += 1
        if not in_single and not in_double:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
                token = _heredoc_token(stmt)
                if token:
                    heredoc_pending = True
                    heredoc_token = token
            current = []
        else:
            current.append("\n")
    stmt = "".join(current).strip()
    if stmt:
        statements.append(stmt)
    return statements


def _heredoc_token(statement: str) -> str:
    match = re.search(r"<<-?\s*(['\"]?)([A-Za-z0-9_./-]+)\1", statement)
    return match.group(2) if match else ""


def _strip_env_and_wrappers(tokens: list[str]) -> list[str]:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "env":
            index += 1
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", token):
            index += 1
            continue
        break
    return tokens[index:]


def _git_invocation_cwd(tokens: list[str], base_cwd: str | None) -> str | None:
    tokens = _strip_env_and_wrappers(tokens)
    if not tokens or tokens[0] != "git":
        return None
    index = 1
    effective = base_cwd
    while index < len(tokens):
        token = tokens[index]
        if token == "-c" and index + 1 < len(tokens):
            index += 2
            continue
        if token == "-C" and index + 1 < len(tokens):
            effective = _resolve_cwd(tokens[index + 1], effective or base_cwd)
            index += 2
            continue
        if token == "--work-tree" and index + 1 < len(tokens):
            effective = _resolve_cwd(tokens[index + 1], base_cwd)
            index += 2
            continue
        if token == "--git-dir" and index + 1 < len(tokens):
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return effective


def _git_subcommand(tokens: list[str]) -> tuple[str, list[str]]:
    tokens = _strip_env_and_wrappers(tokens)
    if not tokens:
        return "", []
    if tokens[0] == "gh" and len(tokens) >= 3 and tokens[1] == "pr":
        return f"gh pr {tokens[2]}", tokens[3:]
    if tokens[0] != "git":
        return "", []
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-c" and index + 1 < len(tokens):
            index += 2
            continue
        if token in {"-C", "--git-dir", "--work-tree"} and index + 1 < len(tokens):
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token, tokens[index + 1 :]
    return "", []


def _iter_shell_git_commands(command: str) -> list[tuple[str, list[str]]]:
    parsed: list[tuple[str, list[str]]] = []
    for statement in _shell_statements(command):
        try:
            tokens = shlex.split(statement, posix=True)
        except ValueError:
            continue
        subcommand, args = _git_subcommand(tokens)
        if subcommand:
            parsed.append((subcommand, args))
    return parsed


def _payload_tool_workdir(payload: dict[str, Any]) -> str | None:
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return None
    for key in ("workdir", "cwd"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _leading_cd_cwd(command: str, base_cwd: str | None) -> str | None:
    match = re.match(r"(?is)^\s*cd\s+(.+?)\s*(?:&&|;)\s*", command)
    if not match:
        return None
    try:
        tokens = shlex.split("cd " + match.group(1), posix=True)
    except ValueError:
        return None
    if len(tokens) >= 2:
        return _resolve_cwd(tokens[1], base_cwd)
    return None


def _command_cwd_candidates(payload: dict[str, Any], command: str) -> list[dict[str, str]]:
    payload_cwd = _payload_cwd(payload)
    tool_workdir = _resolve_cwd(_payload_tool_workdir(payload), payload_cwd)
    base_cwd = tool_workdir or _resolve_cwd(payload_cwd)
    candidates: list[dict[str, str]] = []

    if tool_workdir:
        candidates.append({"source": "tool_input.workdir", "cwd": tool_workdir})

    cd_cwd = _leading_cd_cwd(command, base_cwd)
    if cd_cwd:
        candidates.append({"source": "shell.cd", "cwd": cd_cwd})

    for statement in _shell_statements(command):
        try:
            tokens = shlex.split(statement, posix=True)
        except ValueError:
            continue
        git_cwd = _git_invocation_cwd(tokens, cd_cwd or base_cwd)
        if git_cwd:
            candidates.append({"source": "git.invocation", "cwd": git_cwd})

    if payload_cwd:
        candidates.append({"source": "payload.cwd", "cwd": _resolve_cwd(payload_cwd) or payload_cwd})

    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        key = (item["source"], item["cwd"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _repo_root_for_hook(payload: dict[str, Any], command: str, *, prefer_command: bool) -> tuple[Path, dict[str, Any]]:
    candidates = _command_cwd_candidates(payload, command)
    if not candidates:
        return _repo_root(_payload_cwd(payload)), {
            "root_source": "payload.cwd",
            "cwd_candidates": [],
        }
    ordered = candidates if prefer_command else sorted(candidates, key=lambda item: item["source"] != "payload.cwd")
    errors: list[str] = []
    for item in ordered:
        try:
            root = _repo_root(item["cwd"])
            return root, {
                "root_source": item["source"],
                "selected_cwd": item["cwd"],
                "cwd_candidates": candidates,
            }
        except Exception as exc:
            errors.append(f"{item['source']}={item['cwd']}: {exc}")
    raise RuntimeError("; ".join(errors) or "not inside a git repository")


def _root_has_active_intent(root: Path) -> tuple[bool, dict[str, Any] | None, str]:
    try:
        state = _load_state(root)
    except Exception as exc:
        if _state_path(root).exists():
            return True, None, str(exc)
        return False, None, str(exc)
    if not state.get("merge_mode") or not _state_is_active(state):
        return False, state, ""
    return True, state, ""


def _hook_stop_root_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in _current_turn_items(payload.get("transcript_path") if isinstance(payload.get("transcript_path"), str) else None):
        tool_payload = _transcript_tool_payload(item)
        if not tool_payload:
            continue
        text = _payload_text(tool_payload.get("tool_input", {}))
        if not (command_is_merge_related(text) or command_is_patch_application(text)):
            continue
        try:
            root, details = _repo_root_for_hook(tool_payload, text, prefer_command=True)
        except Exception as exc:
            candidates.append({"source": "transcript.merge_command", "error": str(exc)})
            continue
        candidates.append({"source": "transcript.merge_command", "root": root, "details": details})
    try:
        root = _repo_root(payload.get("cwd") if isinstance(payload.get("cwd"), str) else None)
        candidates.append({"source": "payload.cwd", "root": root, "details": {"root_source": "payload.cwd"}})
    except Exception as exc:
        candidates.append({"source": "payload.cwd", "error": str(exc)})

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        root = candidate.get("root")
        key = str(root) if isinstance(root, Path) else f"error:{candidate.get('source')}:{candidate.get('error')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def command_is_mutating_merge(command: str) -> bool:
    for subcommand, args in _iter_shell_git_commands(command):
        if subcommand in {"merge", "rebase", "cherry-pick", "pull", "gh pr merge"}:
            return True
        if subcommand == "checkout" and any(arg in {"--ours", "--theirs"} for arg in args):
            return True
    return False


def command_mixes_guard_setup_and_merge(command: str) -> bool:
    return bool(MERGE_GUARD_SETUP_RE.search(command)) and command_is_mutating_merge(command)


def command_is_patch_application(command: str) -> bool:
    for subcommand, _args in _iter_shell_git_commands(command):
        if subcommand == "apply":
            return True
    for statement in _shell_statements(command):
        try:
            tokens = _strip_env_and_wrappers(shlex.split(statement, posix=True))
        except ValueError:
            continue
        if tokens and tokens[0] in {"patch"}:
            return True
    return False


def command_is_merge_related(command: str) -> bool:
    return command_is_mutating_merge(command)


def command_is_read_only_merge_map(command: str) -> bool:
    lines = [line.strip() for line in command.splitlines() if line.strip()]
    if not lines:
        return False
    return all(READ_ONLY_GIT_RE.search(line) for line in lines)


def command_uses_implicit_pull(command: str) -> bool:
    return any(subcommand == "pull" for subcommand, _args in _iter_shell_git_commands(command))


def command_uses_whole_file_resolution(command: str) -> bool:
    return any(subcommand == "checkout" and any(arg in {"--ours", "--theirs"} for arg in args) for subcommand, args in _iter_shell_git_commands(command))


def command_is_continuation(command: str) -> bool:
    for subcommand, args in _iter_shell_git_commands(command):
        if subcommand in {"rebase", "cherry-pick", "merge"} and any(arg == "--continue" for arg in args):
            return True
    return False


def cmd_init(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.cwd)
    except RuntimeError as exc:
        return _fail(str(exc))
    hotfix_markers = _hotfix_markers([args.goal, args.merge_mode, args.source_ref, args.target_ref, args.acceptance])
    hotfix_requested = bool(args.hotfix_requested_by_user)
    validation_mode = "hotfix" if hotfix_requested else "feature-only"
    intent_origin = "hotfix-requested-by-user" if hotfix_requested else "feature"
    state = _normalize_state({
        "goal": args.goal,
        "target_ref": args.target_ref,
        "source_ref": args.source_ref,
        "merge_mode": args.merge_mode,
        "allowed_paths": _list_arg(args.allowed_paths),
        "forbidden_paths": _list_arg(args.forbidden_paths),
        "forbidden_changes": _list_arg(args.forbidden_changes),
        "acceptance": _list_arg(args.acceptance),
        "unknowns": _list_arg(args.unknowns),
        "user_confirmed_unknowns_clear": args.confirm_unknowns_clear,
        "tests": _list_arg(args.tests),
        "review": {"status": args.review_status or "", "summary": "", "p1_now": []},
        "subagents": {"status": "not_used", "closed": True, "summary": ""},
        "intent_origin": intent_origin,
        "hotfix_requested_by_user": hotfix_requested,
        "validation_mode": validation_mode,
    })
    state["base_head"] = _git_value(root, ["rev-parse", "HEAD"])
    state["target_head"] = _resolve_ref(root, state["target_ref"])
    state["source_head"] = _resolve_ref(root, state["source_ref"])
    if hotfix_markers and not hotfix_requested:
        state["unknowns"] = sorted(set(list(state.get("unknowns") or []) + [
            "hotfix-like merge intent requires explicit current-turn user authorization"
        ]))
    try:
        state["planned_changed_files"] = _planned_changed_files(root, state)
    except Exception:
        state["planned_changed_files"] = []
    _write_state(root, state)
    return _ok("merge safety intent saved", path=str(_state_path(root)))


def _validate_intent_shape(state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("goal", "target_ref", "source_ref", "merge_mode"):
        if not str(state.get(key, "")).strip():
            errors.append(f"{key} is required")
    for key in ("allowed_paths", "forbidden_paths", "forbidden_changes", "acceptance", "unknowns"):
        if not isinstance(state.get(key, []), list):
            errors.append(f"{key} must be a list")
    if not isinstance(state.get("review", {}), dict):
        errors.append("review must be an object")
    if not isinstance(state.get("subagents", {}), dict):
        errors.append("subagents must be an object")
    if state.get("hotfix_requested_by_user") not in {True, False}:
        errors.append("hotfix_requested_by_user must be a boolean")
    if str(state.get("intent_origin", "feature")) == "hotfix-requested-by-user" and state.get("hotfix_requested_by_user") is not True:
        errors.append("hotfix intent requires hotfix_requested_by_user=true")
    if _hotfix_markers([state.get("goal"), state.get("merge_mode"), state.get("source_ref"), state.get("target_ref")]) and state.get("hotfix_requested_by_user") is not True:
        errors.append("hotfix-like intent requires explicit user authorization")
    return errors


def _lineage_details(root: Path, state: dict[str, Any], result_ref: str = "HEAD") -> dict[str, Any]:
    source_ref = str(state.get("source_ref", "")).strip()
    source_in_result = _ref_integrated(root, source_ref, result_ref) if source_ref else {
        "ok": False,
        "error": "source_ref is required",
    }
    validated_ref = str(state.get("validated_ref", "")).strip()
    validated_in_source: dict[str, Any] | None = None
    validated_in_result: dict[str, Any] | None = None
    if validated_ref:
        validated_in_source = _ref_integrated(root, validated_ref, source_ref)
        validated_in_result = _ref_integrated(root, validated_ref, result_ref)
    source_only_from_validated = list(state.get("validated_only_commits") or [])
    return {
        "result_ref": result_ref,
        "source_ref": source_ref,
        "validated_ref": validated_ref,
        "source_in_result": source_in_result,
        "validated_in_source": validated_in_source,
        "validated_in_result": validated_in_result,
        "validated_only_commits": source_only_from_validated,
        "patch_equivalent_result_allowed": state.get("patch_equivalent_result_allowed") is True,
        "hotfix_requested_by_user": state.get("hotfix_requested_by_user") is True,
        "validation_mode": state.get("validation_mode", "feature-only"),
    }


def _lineage_errors(details: dict[str, Any], *, require_validated: bool = False) -> list[str]:
    errors: list[str] = []
    source_in_result = details.get("source_in_result") or {}
    validated_ref = str(details.get("validated_ref") or "").strip()
    validated_in_result = details.get("validated_in_result")
    patch_equivalent_allowed = details.get("patch_equivalent_result_allowed") is True
    validated_result_ok = bool((validated_in_result or {}).get("ok")) or (
        patch_equivalent_allowed and bool((validated_in_result or {}).get("single_patch_equivalent"))
    )
    source_satisfied = bool(source_in_result.get("ok")) or (
        patch_equivalent_allowed and bool(validated_ref) and validated_result_ok
    )
    if not source_satisfied:
        errors.append("final merge result does not include declared source_ref")
    if require_validated and not validated_ref:
        errors.append("validated_ref is required for protected target readiness")
    validated_in_source = details.get("validated_in_source")
    hotfix = details.get("hotfix_requested_by_user") is True
    if validated_ref and not hotfix:
        if not (validated_in_source or {}).get("ok"):
            errors.append("validated_ref contains commits not present in source_ref; return fixes to the feature branch")
        if not validated_result_ok:
            errors.append("validated_ref is not included in final merge result")
    if validated_ref and hotfix and not validated_result_ok:
        errors.append("authorized hotfix validated_ref is not included in final merge result")
    return errors


def _check_pre(root: Path, state: dict[str, Any], *, allow_dirty: bool = False) -> tuple[bool, list[str], dict[str, Any]]:
    errors = _validate_intent_shape(state)
    unknowns = _state_unknowns_open(state)
    if unknowns and not state.get("user_confirmed_unknowns_clear"):
        errors.append("unknown business/merge questions are still open")
    dirty_info = _dirty_report(root)
    dirty = list(dirty_info["blocking"])
    if dirty and not allow_dirty:
        errors.append("worktree is dirty before merge execution")
    target = str(state.get("target_ref", "")).strip()
    source = str(state.get("source_ref", "")).strip()
    changed: list[str] = []
    if target and source:
        try:
            changed = _planned_changed_files(root, state)
            allow_blocked = _blocked_by_allowlist(changed, list(state.get("allowed_paths") or []))
            deny_blocked = _blocked_by_denylist(changed, list(state.get("forbidden_paths") or []))
            if allow_blocked:
                errors.append("source diff contains files outside allowed_paths")
            if deny_blocked:
                errors.append("source diff touches forbidden_paths")
        except Exception as exc:
            errors.append(str(exc))
    details = {
        "checked_root": str(root),
        "state_path": str(_state_path(root)),
        "dirty": dirty,
        "dirty_classification": dirty_info["classification"],
        "dirty_ignored_tool_state": dirty_info["ignored_tool_state"],
        "changed_files": changed,
        "unknowns": unknowns,
    }
    return not errors, errors, details


def cmd_check_pre(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.cwd)
        state = _load_state(root)
        ok, errors, details = _check_pre(root, state)
    except Exception as exc:
        return _fail(str(exc))
    if not ok:
        return _fail("merge safety pre-check failed", errors=errors, **details)
    state["planned_changed_files"] = details.get("changed_files", [])
    state["last_pre_check"] = {"ok": True, "checked_at": _now(), **details}
    _write_state(root, state)
    return _ok("merge safety pre-check passed", **details)


def _check_post(root: Path, state: dict[str, Any], assistant_message: str = "") -> tuple[bool, list[str], dict[str, Any]]:
    errors = _validate_intent_shape(state)
    unknowns = _state_unknowns_open(state)
    if unknowns and not state.get("user_confirmed_unknowns_clear"):
        errors.append("unknown business/merge questions are still open")
    worktree_changed = _worktree_changed_files(root)
    planned_changed: list[str] = []
    changed = list(worktree_changed)
    if not worktree_changed:
        planned = state.get("planned_changed_files", [])
        if isinstance(planned, list):
            planned_changed = [str(path) for path in planned if str(path).strip()]
            changed = planned_changed
    allow_blocked = _blocked_by_allowlist(changed, list(state.get("allowed_paths") or []))
    deny_blocked = _blocked_by_denylist(changed, list(state.get("forbidden_paths") or []))
    if allow_blocked:
        errors.append("merge result contains files outside allowed_paths")
    if deny_blocked:
        errors.append("merge result touches forbidden_paths")
    conflict_markers = _conflict_marker_hits(root)
    conflict_files = sorted({hit["file"] for hit in conflict_markers})
    if conflict_files:
        errors.append("merge result still contains conflict markers")
    if not _state_has_tests(state, assistant_message):
        errors.append("test or verification evidence is missing")
    if not _state_has_review(state, assistant_message):
        errors.append("independent review evidence is missing")
    p1_now = _review_p1_now(state)
    if p1_now:
        errors.append("review has P1-now blockers")
    if not _subagents_closed(state, assistant_message):
        errors.append("subagent closure evidence is missing")
    current_head = _git_value(root, ["rev-parse", "HEAD"])
    lineage = _lineage_details(root, state, "HEAD")
    errors.extend(_lineage_errors(lineage))
    integration_evidence = bool(_worktree_changed_files(root)) or (
        bool(current_head and state.get("base_head")) and current_head != str(state.get("base_head"))
    )
    if not integration_evidence:
        errors.append("merge integration evidence is missing")
    details = {
        "checked_root": str(root),
        "state_path": str(_state_path(root)),
        "changed_files": changed,
        "worktree_changed_files": worktree_changed,
        "planned_changed_files": planned_changed,
        "unknowns": unknowns,
        "conflict_files": conflict_files,
        "conflict_markers": conflict_markers,
        "p1_now": p1_now,
        "base_head": state.get("base_head", ""),
        "current_head": current_head,
        "integration_evidence": integration_evidence,
        "lineage": lineage,
    }
    return not errors, errors, details


def _precheck_next_action(errors: list[str]) -> str:
    text = "\n".join(errors)
    if "worktree is dirty before merge execution" in text:
        return "Stop and classify the dirty files in checked_root. Use a clean dedicated integration worktree before merging into pre/test/master/main."
    if "source diff contains files outside allowed_paths" in text:
        return "Re-check target_ref/source_ref and allowed_paths. If target is stale or the wrong worktree was used, create a fresh integration worktree from the fetched target ref and reinitialize intent there."
    if "unknown business/merge questions are still open" in text:
        return "Stop and ask the user to answer the open unknowns before running the merge."
    if "hotfix-like intent requires explicit user authorization" in text:
        return "Stop and ask for explicit current-turn hotfix authorization, or move the fix back to the declared feature/fix branch."
    return "Run merge_guard.py report --cwd <checked_root>, fix the listed pre-check issue, then rerun check-pre before any mutating merge command."


def _postcheck_next_action(errors: list[str]) -> str:
    missing: list[str] = []
    if "test or verification evidence is missing" in errors:
        missing.append("test")
    if "independent review evidence is missing" in errors:
        missing.append("review")
    if "subagent closure evidence is missing" in errors:
        missing.append("subagent closure")
    if "merge integration evidence is missing" in errors:
        missing.append("integration")
    if missing:
        return (
            "Record missing evidence with merge_guard.py record-evidence, or report the merge as incomplete. "
            f"Missing: {', '.join(missing)}."
        )
    if "merge result contains files outside allowed_paths" in errors:
        return "Re-check target/source refs and allowed_paths. If the base branch was stale, rebuild the result in a fresh integration worktree from the fetched target ref."
    if "final merge result does not include declared source_ref" in errors:
        return (
            "Stop: the final result does not include the declared source_ref. Rebuild from the correct source branch before claiming readiness. "
            "For an intentional narrow cherry-pick into a protected branch, declare the exact source commit as validated_ref and record lineage with --allow-patch-equivalent."
        )
    if "validated_ref contains commits not present in source_ref; return fixes to the feature branch" in errors:
        return "Return the pre-validated fixes to the declared source_ref and revalidate, unless the user explicitly authorizes a hotfix."
    return "Run merge_guard.py report --cwd <checked_root>, fix the listed post-check issues, then rerun check-post before claiming readiness."


def cmd_check_post(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.cwd)
        state = _load_state(root)
        assistant_message = args.assistant_message or ""
        ok, errors, details = _check_post(root, state, assistant_message)
    except Exception as exc:
        return _fail(str(exc))
    if not ok:
        return _fail("merge safety post-check failed", errors=errors, next_action=_postcheck_next_action(errors), **details)
    state["last_post_check"] = {"ok": True, "checked_at": _now(), **details}
    _write_state(root, state)
    return _ok("merge safety post-check passed", **details)


def cmd_record_lineage(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.cwd)
        state = _load_state(root)
        validated_ref = args.validated_ref or state.get("validated_ref") or state.get("source_ref")
        source_ref = args.source_ref or state.get("source_ref")
        target_ref = args.target_ref or state.get("target_ref")
        merge_ref = args.merge_ref or "HEAD"
        if not validated_ref:
            return _fail("validated_ref is required")
        state["validated_ref"] = validated_ref
        state["source_ref"] = source_ref
        state["target_ref"] = target_ref
        state["validated_head"] = _resolve_ref(root, validated_ref)
        state["source_head"] = _resolve_ref(root, source_ref)
        state["target_head"] = _resolve_ref(root, target_ref)
        if getattr(args, "allow_patch_equivalent", False):
            state["patch_equivalent_result_allowed"] = True
        state["validated_merge_base"] = _git_value(root, ["merge-base", target_ref, source_ref]) if target_ref and source_ref else ""
        state["validated_only_commits"] = _left_only_commits(root, validated_ref, source_ref) if validated_ref and source_ref else []
        lineage = _lineage_details(root, state, merge_ref)
        lineage["recorded_at"] = _now()
        state["lineage_proof"] = lineage
        state.setdefault("evidence", {})["lineage_check"] = lineage
        _write_state(root, state)
    except Exception as exc:
        return _fail(str(exc))
    return _ok("merge safety lineage recorded", checked_root=str(root), state_path=str(_state_path(root)), lineage=lineage)


def cmd_check_lineage(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.cwd)
        state = _load_state(root)
        if args.validated_ref:
            state["validated_ref"] = args.validated_ref
        if args.source_ref:
            state["source_ref"] = args.source_ref
        if getattr(args, "allow_patch_equivalent", False):
            state["patch_equivalent_result_allowed"] = True
        result_ref = args.result_ref or "HEAD"
        lineage = _lineage_details(root, state, result_ref)
        errors = _lineage_errors(lineage, require_validated=args.require_validated)
    except Exception as exc:
        return _fail(str(exc))
    if errors:
        return _fail("merge safety lineage check failed", errors=errors, checked_root=str(root), lineage=lineage)
    return _ok("merge safety lineage check passed", checked_root=str(root), lineage=lineage)


def cmd_record_evidence(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.cwd)
        state = _load_state(root)
        evidence = dict(state.get("evidence") or {})
        if args.test:
            tests = state.get("tests", [])
            if not isinstance(tests, list):
                tests = []
            tests.append(args.test)
            state["tests"] = tests
            evidence["test"] = {"recorded_at": _now(), "summary": args.test}
        if args.review:
            review = state.get("review", {})
            if not isinstance(review, dict):
                review = {}
            review["status"] = args.review_status or review.get("status") or "reviewed"
            review["summary"] = args.review
            if args.p1_now:
                review["p1_now"] = _list_arg(args.p1_now)
            state["review"] = review
            evidence["review"] = {"recorded_at": _now(), "summary": args.review, "p1_now": review.get("p1_now", [])}
        if args.subagents_closed:
            state["subagents"] = {"status": "closed", "closed": True, "summary": args.subagents_summary or ""}
            evidence["subagents"] = {"recorded_at": _now(), "closed": True, "summary": args.subagents_summary or ""}
        if args.push_auth:
            evidence["push_auth"] = {"recorded_at": _now(), "summary": args.push_auth}
        state["evidence"] = evidence
        _write_state(root, state)
    except Exception as exc:
        return _fail(str(exc))
    return _ok("merge safety evidence recorded", checked_root=str(root), state_path=str(_state_path(root)), evidence=state.get("evidence", {}))


def cmd_report(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.cwd)
        state = _load_state(root)
        has_merge_result = bool(_worktree_changed_files(root))
        if args.include_pre or (not args.skip_pre and not has_merge_result):
            pre_ok, pre_errors, pre_details = _check_pre(root, state)
        else:
            pre_ok, pre_errors, pre_details = (None, [], {"skipped": "merge result present; use --include-pre to force"})
        post_ok, post_errors, post_details = _check_post(root, state, args.assistant_message or "") if not args.skip_post else (None, [], {})
    except Exception as exc:
        return _fail(str(exc))
    return _ok(
        "merge safety report",
        checked_root=str(root),
        state_path=str(_state_path(root)),
        state=state,
        pre={"ok": pre_ok, "errors": pre_errors, "next_action": _precheck_next_action(pre_errors) if pre_errors else "", **pre_details},
        post={"ok": post_ok, "errors": post_errors, "next_action": _postcheck_next_action(post_errors) if post_errors else "", **post_details},
    )


def cmd_hook_pretool(args: argparse.Namespace) -> int:
    try:
        payload = _read_payload(args.payload)
        cwd = _payload_cwd(payload)
        tool_name = _payload_tool_name(payload)
        text = _payload_text(payload.get("tool_input", {}))
        is_merge_command = command_is_mutating_merge(text)
        is_patch_application = command_is_patch_application(text)
        if not is_merge_command and not is_patch_application and tool_name not in CODE_EDIT_TOOL_NAMES:
            return _ok("not a mutating merge command", should_block=False)
        if is_merge_command and command_mixes_guard_setup_and_merge(text):
            return _ok(
                "merge guard setup and mutating merge must be separate tool calls",
                should_block=True,
                errors=["do not combine merge_guard.py init/check-pre and git merge/rebase/cherry-pick/pull in one shell command"],
                next_action=(
                    "Run merge_guard.py init and check-pre as their own command first. "
                    "After that tool call succeeds, run the git merge/rebase/cherry-pick command separately in the same checked_root."
                ),
            )
        if command_uses_implicit_pull(text):
            return _ok(
                "git pull is blocked for merge-safety work; use git fetch plus explicit merge/rebase",
                should_block=True,
                errors=["implicit git pull is not allowed"],
                next_action="Run git fetch, inspect refs, then use an explicit merge or rebase under merge-safety.",
            )
        if command_uses_whole_file_resolution(text):
            return _ok(
                "whole-file ours/theirs conflict resolution is blocked by default",
                should_block=True,
                errors=["git checkout --ours/--theirs requires explicit user authorization and manual guard update"],
                next_action="Stop and ask for explicit conflict-resolution authorization, or resolve hunks manually under the locked intent.",
            )
        try:
            root, root_details = _repo_root_for_hook(payload, text, prefer_command=is_merge_command or is_patch_application)
        except Exception as exc:
            if is_merge_command:
                return _ok("merge-like command outside a git repository is not merge-gated", should_block=False, errors=[str(exc)])
            return _ok("code edit outside a git repository is not merge-gated", should_block=False)
        try:
            state = _load_state(root)
        except Exception as exc:
            if is_merge_command or is_patch_application:
                return _ok(
                    "merge safety intent is required before mutating merge or patch-application commands",
                    should_block=True,
                    errors=[str(exc)],
                    checked_root=str(root),
                    state_path=str(_state_path(root)),
                    next_action=(
                        "Initialize merge intent in this checked_root, run check-pre, then rerun the merge command as a separate tool call."
                    ),
                    **root_details,
                )
            return _ok("no active merge safety intent for this code edit", should_block=False)
        if not _state_is_active(state):
            if is_merge_command or is_patch_application:
                return _ok(
                    "merge safety intent is stale; clear or reinitialize it before merge commands",
                    should_block=True,
                    errors=["stale merge safety intent"],
                    checked_root=str(root),
                    state_path=str(_state_path(root)),
                    next_action="Run merge_guard.py report --cwd <checked_root>; then clear/reinitialize the intent for the current target/source refs.",
                    **root_details,
                )
            return _ok("stale merge safety intent does not gate this code edit", should_block=False)
        if is_patch_application:
            return _ok(
                "manual patch application is blocked during an active merge-safety workflow",
                should_block=True,
                errors=[
                    "use git merge/cherry-pick/rebase with merge-safety, or stop and get explicit user approval to re-scope this as a manual patch task"
                ],
                checked_root=str(root),
                state_path=str(_state_path(root)),
                next_action="Use the declared source_ref with git merge/cherry-pick/rebase, or stop and ask the user to re-scope this as manual patching.",
                **root_details,
            )
        if tool_name in CODE_EDIT_TOOL_NAMES and not is_merge_command:
            unknowns = _state_unknowns_open(state)
            if unknowns and not state.get("user_confirmed_unknowns_clear"):
                return _ok(
                    "merge safety blocks code edits while unknowns are open",
                    should_block=True,
                    errors=["unknown business/merge questions are still open"],
                    unknowns=unknowns,
                    next_action="Stop and ask the user to answer the open unknowns before editing or merging.",
                )
            if state.get("merge_started") and not _has_unmerged_paths(root):
                return _ok(
                    "code edits are blocked in an integration worktree after merge has started",
                    should_block=True,
                    errors=[
                        "do not patch or edit pre/master/test/main integration results; return fixes to the declared source_ref unless the user explicitly requested a hotfix"
                    ],
                    checked_root=str(root),
                    state_path=str(_state_path(root)),
                    next_action="Return the fix to the declared source_ref and rebuild the integration worktree, unless the user explicitly authorizes a hotfix.",
                    **root_details,
                )
            return _ok("merge safety intent present for code edit", should_block=False, unknowns=unknowns)
        if command_is_continuation(text):
            state["merge_started"] = True
            _write_state(root, state)
            ok, errors, details = _check_pre(root, state, allow_dirty=True)
            details["continuation"] = True
        else:
            ok, errors, details = _check_pre(root, state)
            if ok and is_merge_command:
                state["merge_started"] = True
                state["last_pre_check"] = {"ok": True, "checked_at": _now(), **details}
                _write_state(root, state)
    except Exception as exc:
        if "is_merge_command" in locals() and is_merge_command:
            return _ok("merge safety guard failed on a mutating merge command", should_block=True, errors=[str(exc)])
        return _ok("merge safety guard failed safely", should_block=False, errors=[str(exc)])
    if not ok:
        return _ok(
            "merge safety pre-check failed",
            should_block=True,
            errors=errors,
            next_action=_precheck_next_action(errors),
            **root_details,
            **details,
        )
    return _ok("merge safety pre-check passed", should_block=False, **root_details, **details)


def cmd_hook_stop(args: argparse.Namespace) -> int:
    payload = _read_payload(args.payload)
    assistant_message = str(payload.get("last_assistant_message") or "")
    candidates = _hook_stop_root_candidates(payload)
    candidate_summary: list[dict[str, Any]] = []
    selected: tuple[Path, dict[str, Any], dict[str, Any]] | None = None
    active_errors: list[str] = []
    for candidate in candidates:
        if "error" in candidate:
            candidate_summary.append({"source": candidate.get("source"), "error": candidate.get("error")})
            continue
        root = candidate["root"]
        details = dict(candidate.get("details") or {})
        active, state, state_error = _root_has_active_intent(root)
        candidate_summary.append({
            "source": candidate.get("source"),
            "root": str(root),
            "state_path": str(_state_path(root)),
            "active_intent": active,
            "state_error": state_error,
        })
        if active and state is None:
            return _ok(
                "merge safety state is invalid",
                should_block=True,
                errors=[state_error],
                checked_root=str(root),
                state_path=str(_state_path(root)),
                stop_root_candidates=candidate_summary,
                **details,
            )
        if active and state is not None:
            selected = (root, state, details)
            break
        if state_error and _state_path(root).exists():
            active_errors.append(state_error)
    if not selected:
        if active_errors:
            return _ok("merge safety hook-stop could not validate state", should_block=True, errors=active_errors, stop_root_candidates=candidate_summary)
        return _ok("no active merge safety intent", should_block=False, stop_root_candidates=candidate_summary)
    root, state, root_details = selected
    try:
        ok, errors, details = _check_post(root, state, assistant_message)
    except Exception as exc:
        return _ok(
            "merge safety post-check failed closed",
            should_block=True,
            errors=[str(exc)],
            checked_root=str(root),
            state_path=str(_state_path(root)),
            stop_root_candidates=candidate_summary,
            **root_details,
        )
    if not ok:
        return _ok(
            "merge safety post-check failed",
            should_block=True,
            errors=errors,
            next_action=_postcheck_next_action(errors),
            stop_root_candidates=candidate_summary,
            **root_details,
            **details,
        )
    return _ok("merge safety post-check passed", should_block=False, stop_root_candidates=candidate_summary, **root_details, **details)


def cmd_clear(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.cwd)
        _clear_state(root)
    except Exception as exc:
        return _fail(str(exc))
    return _ok("merge safety intent cleared", path=str(_state_path(root)))


def cmd_status(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.cwd)
    except Exception as exc:
        return _fail(str(exc))
    state_path = _state_path(root)
    state: dict[str, Any] | None = None
    state_error = ""
    if state_path.exists():
        try:
            state = _load_state(root)
        except Exception as exc:
            state_error = str(exc)
    dirty_info = _dirty_report(root)
    branch = _git_value(root, ["symbolic-ref", "--quiet", "--short", "HEAD"]) or "DETACHED"
    head = _git_value(root, ["rev-parse", "HEAD"])
    active_intent = bool(state and _state_is_active(state))
    stale_intent = bool(state and not active_intent)
    can_start_merge = dirty_info["classification"] in {"clean", "tool_state_only"} and not stale_intent
    return _ok(
        "merge safety status",
        checked_root=str(root),
        branch=branch,
        head=head,
        state_path=str(state_path),
        intent_present=state is not None,
        intent_active=active_intent,
        intent_stale=stale_intent,
        state_error=state_error,
        dirty=dirty_info,
        can_start_merge=can_start_merge,
    )


def _plan_required_fields(args: argparse.Namespace) -> list[str]:
    missing: list[str] = []
    for attr, label in (
        ("goal", "goal"),
        ("target_ref", "target_ref"),
        ("source_ref", "source_ref"),
        ("merge_mode", "merge_mode"),
    ):
        if not str(getattr(args, attr, "") or "").strip():
            missing.append(label)
    return missing


def _plan_lane(target_ref: str, goal: str = "", merge_mode: str = "") -> str:
    text = " ".join([target_ref or "", goal or "", merge_mode or ""]).lower()
    if HOTFIX_MARKER_RE.search(text):
        return "hotfix"
    normalized = (target_ref or "").strip().removeprefix("refs/heads/")
    if normalized.startswith("origin/"):
        normalized = normalized.removeprefix("origin/")
    if normalized in {"pre", "test", "master", "main"}:
        return "protected_integration"
    if "conflict" in text or "冲突" in text:
        return "conflict_resolution"
    return "standard_merge"


def _plan_subagents(lane: str, conflict_files: list[str], target_ref: str) -> dict[str, list[str]]:
    required: list[str] = []
    optional: list[str] = []
    normalized = (target_ref or "").strip().removeprefix("origin/")
    if lane in {"protected_integration", "hotfix"} or normalized in PROTECTED_INTEGRATION_REFS:
        required.append("reviewer")
    if conflict_files or lane == "conflict_resolution":
        required.append("conflict_resolver")
    if lane in {"protected_integration", "hotfix", "standard_merge"}:
        optional.append("test_runner")
    return {"required": required, "optional": optional}


def _shell_command(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def _display_cwd(cwd: str | None, root: Path) -> str:
    if not cwd or not str(cwd).strip():
        return str(root)
    return str(Path(str(cwd).strip()).expanduser())


def _guard_command(cwd: str, command: str, *args: str) -> str:
    return _shell_command([sys.executable, str(Path(__file__).resolve()), command, "--cwd", cwd, *args])


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.cwd)
    except Exception as exc:
        return _fail(str(exc))

    state_path = _state_path(root)
    dirty_info = _dirty_report(root)
    conflict_markers = _conflict_marker_hits(root)
    conflict_files = sorted({hit["file"] for hit in conflict_markers})
    state: dict[str, Any] | None = None
    state_error = ""
    if state_path.exists():
        try:
            state = _load_state(root)
        except Exception as exc:
            state_error = str(exc)

    active_intent = bool(state and _state_is_active(state))
    stale_intent = bool(state and not active_intent)
    target_ref = args.target_ref or (str(state.get("target_ref") or "") if state else "")
    source_ref = args.source_ref or (str(state.get("source_ref") or "") if state else "")
    merge_mode = args.merge_mode or (str(state.get("merge_mode") or "") if state else "")
    goal = args.goal or (str(state.get("goal") or "") if state else "")
    lane = _plan_lane(target_ref, goal, merge_mode)
    missing_fields = [] if active_intent else _plan_required_fields(
        argparse.Namespace(goal=goal, target_ref=target_ref, source_ref=source_ref, merge_mode=merge_mode)
    )
    blockers: list[str] = []
    next_action = ""
    can_initialize_intent = dirty_info["classification"] in {"clean", "tool_state_only"} and not stale_intent and not active_intent and not missing_fields
    can_mutate = False
    recommended_commands: list[str] = []
    command_cwd = _display_cwd(args.cwd, root)

    if state_error:
        blockers.append("merge safety state is invalid")
        next_action = "Fix or clear the invalid merge safety state, then rerun plan."
    elif stale_intent:
        blockers.append("stale merge safety intent")
        next_action = "Run merge_guard.py report --cwd <checked_root>; then clear or reinitialize the intent for the current target/source refs."
    elif dirty_info["classification"] == "code_dirty" and not active_intent:
        blockers.append("worktree is dirty before merge intent initialization")
        next_action = "Stop and classify the dirty files in checked_root. Use a clean dedicated integration worktree before initializing merge intent."
    elif missing_fields:
        blockers.append("merge intent fields are missing")
        next_action = "Ask once for the missing target/source/operation details, then rerun plan or init."
    elif active_intent and state is not None:
        pre_ok, pre_errors, pre_details = _check_pre(root, state, allow_dirty=bool(state.get("merge_started")))
        if pre_ok:
            can_mutate = True
            next_action = "Run the declared mutating merge/rebase/cherry-pick command as a separate command, then record evidence and run check-post."
        else:
            blockers.extend(pre_errors)
            next_action = _precheck_next_action(pre_errors)
        recommended_commands = [
            _guard_command(command_cwd, "check-pre"),
            "# run the declared git merge/rebase/cherry-pick command separately after check-pre passes",
            _guard_command(command_cwd, "record-evidence", "--test", "<validation>", "--review", "<review>", "--subagents-closed"),
            _guard_command(command_cwd, "check-post"),
        ]
        if pre_details:
            conflict_files = sorted(set(conflict_files) | set(pre_details.get("conflict_files") or []))
    else:
        next_action = "Initialize merge intent, run check-pre as a separate command, then run the mutating git command separately."
        recommended_commands = [
            _guard_command(command_cwd, "init", "--goal", "<goal>", "--target-ref", "<target>", "--source-ref", "<source>", "--merge-mode", "<merge mode>", "--allowed-paths", "<paths>", "--acceptance", "<acceptance>"),
            _guard_command(command_cwd, "check-pre"),
        ]

    subagents = _plan_subagents(lane, conflict_files, target_ref)
    return _ok(
        "merge safety plan",
        checked_root=str(root),
        state_path=str(state_path),
        lane=lane,
        target_ref=target_ref,
        source_ref=source_ref,
        merge_mode=merge_mode,
        goal=goal,
        intent_present=state is not None,
        intent_active=active_intent,
        intent_stale=stale_intent,
        state_error=state_error,
        dirty=dirty_info,
        conflict_files=conflict_files,
        missing_fields=missing_fields,
        blockers=blockers,
        can_initialize_intent=can_initialize_intent,
        can_mutate=can_mutate,
        required_subagents=subagents["required"],
        optional_subagents=subagents["optional"],
        recommended_commands=recommended_commands,
        next_action=next_action,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Personal Codex merge safety guard")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="write .codex-local/merge-safety/current.json")
    init.add_argument("--cwd", "--repo", dest="cwd")
    init.add_argument("--goal", required=True)
    init.add_argument("--target-ref", required=True)
    init.add_argument("--source-ref", required=True)
    init.add_argument("--merge-mode", required=True)
    init.add_argument("--allowed-paths", default="")
    init.add_argument("--forbidden-paths", default="")
    init.add_argument("--forbidden-changes", default="")
    init.add_argument("--acceptance", default="")
    init.add_argument("--unknowns", default="")
    init.add_argument("--confirm-unknowns-clear", action="store_true")
    init.add_argument("--tests", default="")
    init.add_argument("--review-status", default="")
    init.add_argument("--hotfix-requested-by-user", action="store_true")
    init.set_defaults(func=cmd_init)

    check_pre = sub.add_parser("check-pre", help="validate intent before merge execution")
    check_pre.add_argument("--cwd", "--repo", dest="cwd")
    check_pre.set_defaults(func=cmd_check_pre)

    check_post = sub.add_parser("check-post", help="validate merge result before completion")
    check_post.add_argument("--cwd", "--repo", dest="cwd")
    check_post.add_argument("--assistant-message", default="")
    check_post.set_defaults(func=cmd_check_post)

    record_lineage = sub.add_parser("record-lineage", help="record validated/source/result lineage evidence")
    record_lineage.add_argument("--cwd", "--repo", dest="cwd")
    record_lineage.add_argument("--validated-ref", required=True)
    record_lineage.add_argument("--source-ref", default="")
    record_lineage.add_argument("--target-ref", default="")
    record_lineage.add_argument("--merge-ref", "--result-ref", dest="merge_ref", default="HEAD")
    record_lineage.add_argument("--allow-patch-equivalent", action="store_true")
    record_lineage.set_defaults(func=cmd_record_lineage)

    check_lineage = sub.add_parser("check-lineage", help="validate that source/validated refs are included in the result")
    check_lineage.add_argument("--cwd", "--repo", dest="cwd")
    check_lineage.add_argument("--validated-ref", default="")
    check_lineage.add_argument("--source-ref", default="")
    check_lineage.add_argument("--result-ref", default="HEAD")
    check_lineage.add_argument("--require-validated", action="store_true")
    check_lineage.add_argument("--allow-patch-equivalent", action="store_true")
    check_lineage.set_defaults(func=cmd_check_lineage)

    record_evidence = sub.add_parser("record-evidence", help="record test/review/subagent evidence without editing current.json manually")
    record_evidence.add_argument("--cwd", "--repo", dest="cwd")
    record_evidence.add_argument("--test", default="")
    record_evidence.add_argument("--review", default="")
    record_evidence.add_argument("--review-status", default="")
    record_evidence.add_argument("--p1-now", default="")
    record_evidence.add_argument("--subagents-closed", action="store_true")
    record_evidence.add_argument("--subagents-summary", default="")
    record_evidence.add_argument("--push-auth", default="")
    record_evidence.set_defaults(func=cmd_record_evidence)

    report = sub.add_parser("report", help="print intent and check results")
    report.add_argument("--cwd", "--repo", dest="cwd")
    report.add_argument("--assistant-message", default="")
    report.add_argument("--skip-pre", action="store_true")
    report.add_argument("--include-pre", action="store_true")
    report.add_argument("--skip-post", action="store_true")
    report.set_defaults(func=cmd_report)

    status = sub.add_parser("status", help="classify worktree dirty state and active merge intent")
    status.add_argument("--cwd", "--repo", dest="cwd")
    status.set_defaults(func=cmd_status)

    plan = sub.add_parser("plan", help="print machine-readable merge lane, blockers, subagents, commands, and next_action")
    plan.add_argument("--cwd", "--repo", dest="cwd")
    plan.add_argument("--goal", default="")
    plan.add_argument("--target-ref", default="")
    plan.add_argument("--source-ref", default="")
    plan.add_argument("--merge-mode", default="")
    plan.set_defaults(func=cmd_plan)

    clear = sub.add_parser("clear", help="clear active merge safety intent")
    clear.add_argument("--cwd", "--repo", dest="cwd")
    clear.set_defaults(func=cmd_clear)

    hook_pretool = sub.add_parser("hook-pretool", help="hook entry for PreToolUse")
    hook_pretool.add_argument("--payload")
    hook_pretool.set_defaults(func=cmd_hook_pretool)

    hook_stop = sub.add_parser("hook-stop", help="hook entry for Stop")
    hook_stop.add_argument("--payload")
    hook_stop.set_defaults(func=cmd_hook_stop)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
