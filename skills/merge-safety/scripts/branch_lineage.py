#!/usr/bin/env python3
"""Local, append-only lineage for protected-branch workflows.

This tool deliberately keeps long-lived lineage separate from merge_guard's
per-worktree, short-lived integration intent.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
PROTECTED_BRANCHES = {"master", "main", "pre", "test"}
SOURCE_BRANCH_RE = re.compile(r"^(?:feat|fix|refactor|docs|test|chore|hotfix)/[A-Za-z0-9._/-]+$")
MASTER_SOURCE_RE = re.compile(r"^(?:feat|fix|hotfix)/[A-Za-z0-9._/-]+$")
COMMON_GIT_DIR_ENV = "CODEX_MERGE_LINEAGE_COMMON_GIT_DIR"
LEGACY_COMMON_GIT_DIR_ENV = "AISERVER_LINEAGE_COMMON_GIT_DIR"
INTEGRATE_ENTRYPOINT_ENV = "CODEX_MERGE_LINEAGE_INTEGRATE_ENTRYPOINT"
PROMOTE_ENTRYPOINT_ENV = "CODEX_MERGE_LINEAGE_PROMOTE_ENTRYPOINT"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n"


def _emit(ok: bool, message: str, **extra: Any) -> int:
    print(_json({"ok": ok, "message": message, **extra}), end="")
    return 0 if ok else 1


def _run(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(root), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _value(root: Path, args: list[str]) -> str:
    result = _run(root, args)
    if result.returncode:
        return ""
    return result.stdout.strip()


def _repo_root(path: str | None) -> Path:
    start = Path(path or os.getcwd()).expanduser()
    result = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "not inside a git repository")
    return Path(result.stdout.strip()).resolve()


def _common_dir(root: Path) -> Path:
    raw = _value(root, ["rev-parse", "--git-common-dir"])
    if not raw:
        raise RuntimeError("cannot resolve git common directory")
    common = Path(raw)
    if not common.is_absolute():
        common = root / common
    return common.resolve()


def _managed_common_dir(root: Path) -> Path:
    common = _common_dir(root)
    configured = os.environ.get(COMMON_GIT_DIR_ENV) or os.environ.get(LEGACY_COMMON_GIT_DIR_ENV)
    if configured and common != Path(configured).expanduser().resolve():
        raise RuntimeError(f"branch lineage common git dir does not match configured repository: {common}")
    return common


def _store_path(common: Path) -> Path:
    return common / "codex" / "lineage" / "v1.jsonl"


def _read_events(common: Path) -> list[dict[str, Any]]:
    path = _store_path(common)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid lineage JSON at {path}:{number}: {exc.msg}") from exc
        if not isinstance(event, dict):
            raise RuntimeError(f"invalid lineage event at {path}:{number}")
        events.append(event)
    return events


def _append_event(common: Path, event: dict[str, Any]) -> None:
    path = _store_path(common)
    path.parent.mkdir(parents=True, exist_ok=True)
    event.setdefault("schema_version", SCHEMA_VERSION)
    event.setdefault("event_id", str(uuid.uuid4()))
    event.setdefault("recorded_at", _now())
    encoded = _json(event).encode("utf-8")
    with path.open("ab") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _branch(root: Path) -> str:
    return _value(root, ["symbolic-ref", "--quiet", "--short", "HEAD"]) or "DETACHED"


def _sha(root: Path, ref: str) -> str:
    value = _value(root, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
    if not value:
        raise RuntimeError(f"cannot resolve commit ref: {ref}")
    return value


def _is_ancestor(root: Path, older: str, newer: str) -> bool:
    return _run(root, ["merge-base", "--is-ancestor", older, newer]).returncode == 0


def _registered(events: Iterable[dict[str, Any]], branch: str) -> dict[str, Any] | None:
    found = [event for event in events if event.get("kind") in {"source_registered", "source_adopted"} and event.get("source_branch") == branch]
    return found[-1] if found else None


def _pending(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    completed = {event.get("operation_id") for event in events if event.get("kind") == "pre_integrated"}
    return [event for event in events if event.get("kind") == "pre_pending" and event.get("operation_id") not in completed]


def _remote_sha(root: Path, ref: str) -> str:
    result = _run(root, ["ls-remote", "origin", f"refs/heads/{ref}"])
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"cannot query origin/{ref}")
    parts = result.stdout.strip().split()
    if not parts:
        raise RuntimeError(f"origin/{ref} does not exist")
    return parts[0]


def _protected_worktrees(root: Path) -> list[dict[str, str]]:
    result = _run(root, ["worktree", "list", "--porcelain"])
    if result.returncode:
        return []
    current: dict[str, str] = {}
    found: list[dict[str, str]] = []
    for raw in [*result.stdout.splitlines(), ""]:
        if not raw.strip():
            branch = current.get("branch", "").removeprefix("refs/heads/")
            if current.get("worktree") and branch in PROTECTED_BRANCHES:
                found.append({"worktree": current["worktree"], "branch": branch})
            current = {}
            continue
        key, _, value = raw.partition(" ")
        current[key] = value.strip()
    return found


def _source_worktree(events: list[dict[str, Any]], branch: str) -> str:
    event = _registered(events, branch)
    return str(event.get("worktree", "")) if event else ""


def cmd_register_source(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.repo)
        common = _managed_common_dir(root)
        branch = str(args.source_branch).strip()
        if not SOURCE_BRANCH_RE.fullmatch(branch):
            raise RuntimeError("source branch must use feat/, fix/, refactor/, docs/, test/, chore/, or hotfix/")
        if not args.adopt and _branch(root) != branch:
            raise RuntimeError(f"source branch must be checked out before registration: current={_branch(root)} requested={branch}")
        events = _read_events(common)
        existing = _registered(events, branch)
        if existing and not args.adopt:
            raise RuntimeError("source branch is already registered; use --adopt only for an existing legacy branch")
        source_tip = _sha(root, branch)
        base_ref = "origin/master" if _value(root, ["rev-parse", "--verify", "origin/master"]) else ""
        event = {
            "kind": "source_adopted" if args.adopt else "source_registered",
            "source_branch": branch,
            "source_tip": source_tip,
            "base_ref": base_ref,
            "base_sha": _sha(root, base_ref) if base_ref else "",
            "worktree": str(Path(args.worktree or root).resolve()),
        }
        _append_event(common, event)
        return _emit(True, "source branch registered", event=event, store=str(_store_path(common)))
    except Exception as exc:
        return _emit(False, str(exc), next_action="Use a checked-out non-protected source branch, or explicitly adopt a legacy branch.")


def cmd_prepare_pre(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.repo)
        common = _managed_common_dir(root)
        events = _read_events(common)
        if _pending(events):
            raise RuntimeError("unresolved pre lineage operation exists; audit and recover it before another shared pre update")
        branch = str(args.source_branch).strip()
        if not _registered(events, branch):
            raise RuntimeError("source branch is not registered; register-source or --adopt it before integrating to pre")
        if args.mode == "cherry-pick" and not branch:
            raise RuntimeError("cherry-pick integration requires --source-branch")
        operation_id = str(uuid.uuid4())
        event = {
            "kind": "pre_pending",
            "operation_id": operation_id,
            "source_branch": branch,
            "source_tip": str(args.source_tip),
            "target_ref": "pre",
            "target_before": str(args.pre_before),
            "integration_sha": str(args.integration_sha),
            "mode": args.mode,
            "validated_ref": str(args.validated_ref or ""),
        }
        _append_event(common, event)
        return _emit(True, "pre lineage operation prepared", operation_id=operation_id, event=event)
    except Exception as exc:
        return _emit(False, str(exc), next_action="Resolve pending lineage state and register the exact source branch before updating pre.")


def cmd_record_pre(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.repo)
        common = _managed_common_dir(root)
        events = _read_events(common)
        pending = next((event for event in _pending(events) if event.get("operation_id") == args.operation_id), None)
        if not pending:
            raise RuntimeError("pending pre lineage operation was not found")
        remote = _remote_sha(root, "pre")
        if remote != args.pre_after or remote != pending.get("integration_sha"):
            raise RuntimeError("origin/pre does not match the declared integration SHA; refusing to record an unverified pre update")
        event = {**pending, "kind": "pre_integrated", "target_after": args.pre_after}
        _append_event(common, event)
        return _emit(True, "pre lineage recorded", event=event)
    except Exception as exc:
        return _emit(False, str(exc), next_action="Do not continue shared branch work. Audit the pending operation and recover it with the verified origin/pre SHA.")


def _latest_pre_event(events: list[dict[str, Any]], branch: str) -> dict[str, Any] | None:
    found = [event for event in events if event.get("kind") == "pre_integrated" and event.get("source_branch") == branch]
    return found[-1] if found else None


def _validate_master_source(root: Path, events: list[dict[str, Any]], branch: str, source_tip: str, validated_pre: str, hotfix: bool) -> None:
    if not MASTER_SOURCE_RE.fullmatch(branch):
        raise RuntimeError("master promotion source must use feat/, fix/, or hotfix/")
    if hotfix and not branch.startswith("hotfix/"):
        raise RuntimeError("--hotfix is only valid for a hotfix/* source branch")
    if not _registered(events, branch):
        raise RuntimeError("source branch is not registered")
    if hotfix:
        return
    pre = _latest_pre_event(events, branch)
    if not pre or pre.get("source_tip") != source_tip:
        raise RuntimeError("source tip has not completed a matching pre integration")
    if validated_pre and pre.get("target_after") != validated_pre:
        raise RuntimeError("validated pre SHA does not match the source branch's latest pre integration")


def cmd_record_master(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.repo)
        common = _managed_common_dir(root)
        branch = str(args.source_branch).strip()
        events = _read_events(common)
        _validate_master_source(root, events, branch, args.source_tip, args.validated_pre, args.hotfix)
        if _remote_sha(root, "master") != args.master_after:
            raise RuntimeError("origin/master does not match the declared master SHA")
        event = {
            "kind": "master_promoted",
            "source_branch": branch,
            "source_tip": args.source_tip,
            "target_ref": "master",
            "target_before": args.master_before,
            "target_after": args.master_after,
            "validated_pre": args.validated_pre or "",
            "hotfix": bool(args.hotfix),
        }
        _append_event(common, event)
        return _emit(True, "master lineage recorded", event=event, pre_sync_pending=bool(args.hotfix))
    except Exception as exc:
        return _emit(False, str(exc), next_action="Promote only a registered source branch after matching pre validation, unless this is an explicit hotfix source branch.")


def cmd_validate_master(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.repo)
        common = _managed_common_dir(root)
        _validate_master_source(root, _read_events(common), args.source_branch, args.source_tip, args.validated_pre, args.hotfix)
        return _emit(True, "master promotion source is eligible")
    except Exception as exc:
        return _emit(False, str(exc), next_action="Repair and validate the named source branch before creating an integration worktree or writing master.")


def _candidate_sources(root: Path, events: list[dict[str, Any]], pre_ref: str) -> list[dict[str, Any]]:
    pre_sha = _sha(root, pre_ref)
    promotions = {(event.get("source_branch"), event.get("source_tip")) for event in events if event.get("kind") == "master_promoted"}
    candidates: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("kind") != "pre_integrated":
            continue
        if not _is_ancestor(root, str(event.get("target_after")), pre_sha):
            continue
        key = (event.get("source_branch"), event.get("source_tip"))
        if key in promotions:
            continue
        candidates[str(event.get("source_branch"))] = event
    return [
        {
            "source_branch": branch,
            "source_tip": event.get("source_tip"),
            "pre_after": event.get("target_after"),
            "worktree": _source_worktree(events, branch),
        }
        for branch, event in sorted(candidates.items())
    ]


def cmd_resolve_repair(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.repo)
        common = _managed_common_dir(root)
        events = _read_events(common)
        pre_ref = args.pre_ref or ("origin/pre" if _value(root, ["rev-parse", "--verify", "origin/pre"]) else "pre")
        candidates = _candidate_sources(root, events, pre_ref)
        if len(candidates) == 1:
            return _emit(True, "repair source resolved", status="resolved", candidate=candidates[0])
        status = "ambiguous" if candidates else "none"
        return _emit(False, "repair source cannot be inferred", status=status, candidates=candidates, next_action="Ask the user to identify the source branch; do not patch pre or create a guessed branch.")
    except Exception as exc:
        return _emit(False, str(exc), next_action="Audit lineage state and ask the user to identify the repair source.")


def cmd_audit(args: argparse.Namespace) -> int:
    try:
        root = _repo_root(args.repo)
        common = _managed_common_dir(root)
        events = _read_events(common)
        issues: list[dict[str, Any]] = []
        protected_worktrees = _protected_worktrees(root)
        for item in protected_worktrees:
            issues.append({"kind": "protected_branch_worktree", **item})
        for event in _pending(events):
            issues.append({"kind": "pending_pre", "operation_id": event.get("operation_id")})
        hotfixes = [event for event in events if event.get("kind") == "master_promoted" and event.get("hotfix")]
        for hotfix in hotfixes:
            synced = any(
                event.get("kind") == "pre_integrated"
                and event.get("source_branch") == hotfix.get("source_branch")
                and event.get("source_tip") == hotfix.get("source_tip")
                and event.get("recorded_at", "") > hotfix.get("recorded_at", "")
                for event in events
            )
            if not synced:
                issues.append({"kind": "hotfix_pre_sync_pending", "source_branch": hotfix.get("source_branch")})
        completed = [event for event in events if event.get("kind") == "pre_integrated"]
        if not completed and _value(root, ["rev-parse", "--verify", "origin/pre"]):
            issues.append({"kind": "legacy_pre_without_lineage", "current_pre": _sha(root, "origin/pre")})
        if not any(event.get("kind") == "master_promoted" for event in events) and _value(root, ["rev-parse", "--verify", "origin/master"]):
            issues.append({"kind": "legacy_master_without_lineage", "current_master": _sha(root, "origin/master")})
        if completed and _value(root, ["rev-parse", "--verify", "origin/pre"]):
            latest = completed[-1].get("target_after", "")
            current = _sha(root, "origin/pre")
            if latest and latest != current and _is_ancestor(root, latest, current):
                issues.append({"kind": "untracked_pre_advance", "recorded_pre": latest, "current_pre": current})
        return _emit(not issues, "lineage audit passed" if not issues else "lineage audit needs attention", issues=issues, event_count=len(events))
    except Exception as exc:
        return _emit(False, str(exc), next_action="Fix invalid lineage state before shared branch work.")


def _payload_text(payload: dict[str, Any]) -> str:
    value = payload.get("tool_input", {})
    if isinstance(value, dict):
        return "\n".join(str(value.get(key, "")) for key in ("cmd", "command", "patch", "content"))
    return str(value)


def _payload_workdir(payload: dict[str, Any]) -> str | None:
    value = payload.get("tool_input", {})
    if isinstance(value, dict) and value.get("workdir"):
        return str(value["workdir"])
    return str(payload.get("cwd", "") or "") or None


def _shell_statements(command: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?:&&|;|\n)", command) if part.strip()]


def _git_commands(command: str, base: Path) -> list[tuple[Path, list[str]]]:
    commands: list[tuple[Path, list[str]]] = []
    current = base
    for statement in _shell_statements(command):
        try:
            tokens = shlex.split(statement, posix=True)
        except ValueError:
            continue
        while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
            tokens.pop(0)
        if not tokens:
            continue
        if tokens[0] == "cd" and len(tokens) >= 2:
            target = Path(tokens[1]).expanduser()
            current = (current / target).resolve() if not target.is_absolute() else target.resolve()
            continue
        if tokens[0] == "git":
            args = tokens[1:]
            if len(args) >= 2 and args[0] == "-C":
                target = Path(args[1]).expanduser()
                command_root = (current / target).resolve() if not target.is_absolute() else target.resolve()
                commands.append((command_root, args[2:]))
            else:
                commands.append((current, args))
    return commands


def _command_mentions_risk(command: str) -> bool:
    return bool(re.search(r"\bgit\s+(?:-C\s+\S+\s+)?(?:worktree\s+add|switch|checkout|add|commit|merge|cherry-pick|push|apply|am|revert|reset|restore|rm|mv)\b", command))


def _explicit_read_only_shell(command: str) -> bool:
    statements = _shell_statements(command)
    if not statements:
        return True
    allowed = {"status", "fetch", "merge-base", "log", "diff", "range-diff", "show", "rev-parse", "branch", "remote", "ls-remote", "for-each-ref", "name-rev"}
    read_only_programs = {"pwd", "ls", "cat", "rg", "grep", "head", "tail", "wc", "stat"}
    for statement in statements:
        try:
            tokens = shlex.split(statement, posix=True)
        except ValueError:
            return False
        while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
            tokens.pop(0)
        if not tokens:
            continue
        if tokens[0] == "git":
            args = tokens[1:]
            if len(args) >= 2 and args[0] == "-C":
                args = args[2:]
            if not args or args[0] not in allowed:
                return False
            if args[0] == "branch" and any(
                flag in args[1:]
                for flag in {"-f", "-m", "-M", "-c", "-C", "-d", "-D", "--delete", "--move", "--copy", "--force", "--set-upstream-to", "--unset-upstream", "--edit-description"}
            ):
                return False
            if args[0] == "remote" and len(args) > 1 and args[1] not in {"-v", "get-url", "show"}:
                return False
            continue
        if tokens[0] == "sed" and "-n" in tokens and "-i" not in tokens and "--in-place" not in tokens and "w" not in tokens:
            continue
        if tokens[0] in read_only_programs and ">" not in tokens and ">>" not in tokens:
            continue
        return False
    return True


def _protected_checkout(args: list[str]) -> str:
    if not args:
        return ""
    subcommand = args[0]
    tail = args[1:]
    if subcommand == "worktree" and tail and tail[0] == "add":
        values = tail[1:]
        for index, value in enumerate(values[:-1]):
            if value in {"-b", "-B"} and values[index + 1] in PROTECTED_BRANCHES:
                return values[index + 1]
        if "--detach" in values:
            return ""
        positional = [value for value in values if not value.startswith("-")]
        if len(positional) >= 2 and positional[-1] in PROTECTED_BRANCHES:
            return positional[-1]
    if subcommand in {"switch", "checkout"} and not any(value in {"--ours", "--theirs"} for value in tail):
        for value in tail:
            if value in PROTECTED_BRANCHES:
                return value
    return ""


def _protected_branch_ref_target(args: list[str]) -> str:
    if not args or args[0] != "branch":
        return ""
    mutation_flags = {"-f", "-m", "-M", "-c", "-C", "-d", "-D", "--delete", "--move", "--copy", "--force", "--set-upstream-to", "--unset-upstream", "--edit-description"}
    if not any(flag in args[1:] for flag in mutation_flags):
        return ""
    operands = [value for value in args[1:] if not value.startswith("-")]
    return next((value for value in operands if value in PROTECTED_BRANCHES), "")


def _protected_push_next_action(root: Path, common: Path, branch: str, command: list[str]) -> str:
    target = "master" if any(re.search(r":(?:master|main)$", value) for value in command[1:]) else "pre"
    entrypoint = os.environ.get(PROMOTE_ENTRYPOINT_ENV if target == "master" else INTEGRATE_ENTRYPOINT_ENV, "")
    events = _read_events(common)
    if not entrypoint:
        return (
            "Use the project-owned protected-branch wrapper from the registered source branch; "
            "do not push the protected ref directly."
        )
    if _registered(events, branch):
        return (
            f"Run `python {entrypoint} --repo {root} --source-branch {branch} ... --authorize-push` "
            "from the registered source branch; do not push the protected ref directly."
        )
    return (
        f"Run `python {entrypoint} --help` from a registered source branch. "
        "Do not infer a source branch from an integration worktree."
    )


def first_write_decision(payload: dict[str, Any]) -> dict[str, Any]:
    """Check the first code write of a Codex task in a managed worktree.

    The generic hook owns task-scoped caching. This adapter intentionally makes
    no decision outside the configured repository and never guesses ownership of an old
    worktree. A clean, registered source branch is the only silent allow.
    """
    try:
        root = _repo_root(_payload_workdir(payload))
        common = _managed_common_dir(root)
    except Exception:
        return {"applicable": False, "should_block": False}

    branch = _branch(root)
    if branch in PROTECTED_BRANCHES:
        # hook_decision owns the protected-branch explanation and is called first.
        return {"applicable": False, "should_block": False}

    dirty = bool(_value(root, ["status", "--porcelain"]))
    registered = _registered(_read_events(common), branch)
    if dirty:
        return {
            "applicable": True,
            "should_block": True,
            "worktree": str(root),
            "message": f"first code write is blocked: worktree {root} on {branch} already has uncommitted changes",
            "next_action": "Attribute and finish or archive those changes, then continue in this source worktree; otherwise create a new registered source worktree.",
        }
    if not registered:
        return {
            "applicable": True,
            "should_block": True,
            "worktree": str(root),
            "message": f"first code write is blocked: source branch {branch} is not registered in local lineage",
            "next_action": "Create work through the standard source-worktree entrypoint, or explicitly run register-source --adopt after confirming this branch owns the work.",
        }
    return {
        "applicable": True,
        "should_block": False,
        "worktree": str(root),
        "source_branch": branch,
    }


def hook_decision(payload: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(payload.get("tool_name", "")).lower()
    text = _payload_text(payload)
    is_edit = tool_name in {"apply_patch", "edit", "write"}
    if not is_edit and _explicit_read_only_shell(text):
        return {"applicable": False, "should_block": False}
    try:
        root = _repo_root(_payload_workdir(payload))
        common = _managed_common_dir(root)
    except Exception:
        if is_edit:
            return {"applicable": False, "should_block": False}
        root = Path(_payload_workdir(payload) or os.getcwd()).expanduser().resolve()
        common = None
    if is_edit:
        branch = _branch(root)
        if branch in PROTECTED_BRANCHES:
            if branch == "pre":
                result = _resolve_payload(root, common)
                return {"applicable": True, "should_block": True, **result}
            return {
                "applicable": True,
                "should_block": True,
                "message": f"direct code edits on protected branch {branch} are blocked",
                "next_action": "Create or continue a named source branch; do not develop on master/main/test.",
            }
        return {"applicable": True, "should_block": False}
    commands = _git_commands(text, root)
    if not commands:
        branch = _branch(root)
        if branch in PROTECTED_BRANCHES:
            if branch == "pre":
                return {"applicable": True, "should_block": True, **_resolve_payload(root, common)}
            return {
                "applicable": True,
                "should_block": True,
                "message": f"non-read-only shell operation on protected branch {branch} is blocked",
                "next_action": "Run it from a named source branch, not master/main/test.",
            }
        return {"applicable": True, "should_block": False}
    for command_root, command in commands:
        try:
            command_common = _managed_common_dir(_repo_root(str(command_root)))
        except Exception:
            continue
        protected = _protected_checkout(command)
        if protected:
            return {
                "applicable": True,
                "should_block": True,
                "message": f"checking out protected local branch {protected} in a managed Codex worktree is blocked",
                "next_action": "Use a named feature/fix/hotfix branch from origin/master, or --detach origin/master for read-only inspection.",
            }
        protected_ref = _protected_branch_ref_target(command)
        if protected_ref:
            return {
                "applicable": True,
                "should_block": True,
                "message": f"direct mutation of protected ref {protected_ref} is blocked",
                "next_action": "Use the standard source/integration workflow; do not force-move protected refs.",
            }
        subcommand = command[0] if command else ""
        branch = _branch(command_root)
        if subcommand == "worktree" and len(command) >= 2 and command[1] == "add":
            continue
        if subcommand in {"switch", "checkout"}:
            continue
        branch_ref_mutation = subcommand == "branch" and any(
            flag in command[1:]
            for flag in {"-f", "-m", "-M", "-c", "-C", "-d", "-D", "--delete", "--move", "--copy", "--force", "--set-upstream-to", "--unset-upstream", "--edit-description"}
        )
        remote_mutation = subcommand == "remote" and (len(command) < 2 or command[1] not in {"-v", "get-url", "show"})
        if branch in PROTECTED_BRANCHES and (branch_ref_mutation or remote_mutation):
            return {
                "applicable": True,
                "should_block": True,
                "message": f"protected branch ref mutation via git {subcommand} is blocked",
                "next_action": "Use the standard source/integration workflow; do not mutate protected refs directly.",
            }
        if branch in PROTECTED_BRANCHES and subcommand not in {"status", "fetch", "merge-base", "log", "diff", "range-diff", "show", "rev-parse", "branch", "remote", "ls-remote", "for-each-ref", "name-rev"}:
            if branch == "pre":
                return {"applicable": True, "should_block": True, **_resolve_payload(command_root, command_common)}
            return {
                "applicable": True,
                "should_block": True,
                "message": f"direct {subcommand} on protected branch {branch} is blocked",
                "next_action": "Use a named source branch and the standard shared-branch integration wrapper.",
            }
        if subcommand == "push" and any(re.search(r":(?:pre|master|main|test)$", value) for value in command[1:]):
            return {
                "applicable": True,
                "should_block": True,
                "message": "raw push to a protected shared branch is blocked",
                "next_action": _protected_push_next_action(command_root, command_common, branch, command),
            }
    return {"applicable": True, "should_block": False}


def _resolve_payload(root: Path, common: Path) -> dict[str, Any]:
    try:
        events = _read_events(common)
        candidates = _candidate_sources(root, events, "origin/pre" if _value(root, ["rev-parse", "--verify", "origin/pre"]) else "pre")
    except Exception as exc:
        return {"message": f"direct pre work is blocked: {exc}", "next_action": "Audit lineage and ask the user to identify the source branch."}
    if len(candidates) == 1:
        candidate = candidates[0]
        return {
            "message": f"direct pre work is blocked; repair the source branch {candidate['source_branch']}",
            "next_action": f"Continue {candidate['source_branch']} at {candidate['worktree'] or '<source worktree>'}, then update pre through the standard wrapper.",
            "candidate": candidate,
        }
    return {
        "message": "direct pre work is blocked and the repair source is not unique",
        "next_action": "Ask the user to identify the source branch; do not patch pre or create a guessed branch.",
        "candidates": candidates,
    }


def cmd_hook_pretool(args: argparse.Namespace) -> int:
    try:
        raw = Path(args.payload).read_text(encoding="utf-8") if args.payload else sys.stdin.read()
        payload = json.loads(raw)
        result = hook_decision(payload)
        message = str(result.pop("message", "branch lineage hook decision"))
        return _emit(True, message, **result)
    except Exception as exc:
        return _emit(False, str(exc), should_block=True, next_action="Stop protected branch work until the branch guard can be evaluated.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="local protected-branch lineage")
    sub = parser.add_subparsers(dest="command", required=True)
    register = sub.add_parser("register-source")
    register.add_argument("--repo", required=True)
    register.add_argument("--source-branch", required=True)
    register.add_argument("--worktree", default="")
    register.add_argument("--adopt", action="store_true")
    register.set_defaults(func=cmd_register_source)
    prepare = sub.add_parser("prepare-pre")
    prepare.add_argument("--repo", required=True)
    prepare.add_argument("--source-branch", required=True)
    prepare.add_argument("--source-tip", required=True)
    prepare.add_argument("--pre-before", required=True)
    prepare.add_argument("--integration-sha", required=True)
    prepare.add_argument("--mode", choices=("merge", "cherry-pick"), required=True)
    prepare.add_argument("--validated-ref", default="")
    prepare.set_defaults(func=cmd_prepare_pre)
    record = sub.add_parser("record-pre")
    record.add_argument("--repo", required=True)
    record.add_argument("--operation-id", required=True)
    record.add_argument("--pre-after", required=True)
    record.set_defaults(func=cmd_record_pre)
    master = sub.add_parser("record-master")
    master.add_argument("--repo", required=True)
    master.add_argument("--source-branch", required=True)
    master.add_argument("--source-tip", required=True)
    master.add_argument("--master-before", required=True)
    master.add_argument("--master-after", required=True)
    master.add_argument("--validated-pre", default="")
    master.add_argument("--hotfix", action="store_true")
    master.set_defaults(func=cmd_record_master)
    validate_master = sub.add_parser("validate-master")
    validate_master.add_argument("--repo", required=True)
    validate_master.add_argument("--source-branch", required=True)
    validate_master.add_argument("--source-tip", required=True)
    validate_master.add_argument("--validated-pre", default="")
    validate_master.add_argument("--hotfix", action="store_true")
    validate_master.set_defaults(func=cmd_validate_master)
    resolve = sub.add_parser("resolve-repair")
    resolve.add_argument("--repo", required=True)
    resolve.add_argument("--pre-ref", default="")
    resolve.set_defaults(func=cmd_resolve_repair)
    audit = sub.add_parser("audit")
    audit.add_argument("--repo", required=True)
    audit.set_defaults(func=cmd_audit)
    hook = sub.add_parser("hook-pretool")
    hook.add_argument("--payload")
    hook.set_defaults(func=cmd_hook_pretool)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
