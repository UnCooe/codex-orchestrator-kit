#!/usr/bin/env python3
"""Reconcile dirty Codex worktrees without guessing ownership.

The lifecycle hook records facts; this command turns them into a small,
human-actionable queue.  It can produce owner-recall messages for a known
thread and can remove only explicitly named, stale *secondary* worktrees.
The primary checkout is never eligible for automatic deletion.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from workspace_lifecycle import (
        ACTIVE_STATES,
        LifecycleError,
        PENDING_STATES,
        audit,
        changed_paths,
        discover_worktree,
        list_queue,
        quarantine_legacy,
    )
    from workspace_lifecycle_owner_index import index_sessions, unique_owner
except ModuleNotFoundError:  # pragma: no cover - direct path execution fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from workspace_lifecycle import (
        ACTIVE_STATES,
        LifecycleError,
        PENDING_STATES,
        audit,
        changed_paths,
        discover_worktree,
        list_queue,
        quarantine_legacy,
    )
    from workspace_lifecycle_owner_index import index_sessions, unique_owner


DEFAULT_EXPIRY_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age(updated_at: Any) -> tuple[float | None, str]:
    if not isinstance(updated_at, str) or not updated_at.strip():
        return None, "unknown"
    try:
        when = datetime.fromisoformat(updated_at)
    except ValueError:
        return None, "unknown"
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (_now() - when).total_seconds() / 86400), "binding.updated_at"


def _filesystem_age(worktree: Path, paths: list[str]) -> tuple[float | None, str]:
    mtimes: list[float] = []
    for raw in paths:
        candidate = worktree / raw
        try:
            mtimes.append(candidate.stat().st_mtime)
        except OSError:
            continue
    if not mtimes:
        return None, "unknown"
    return max(0.0, (_now().timestamp() - min(mtimes)) / 86400), "filesystem_mtime_lower_bound"


def _primary_root(repo: str | os.PathLike[str]) -> str:
    return discover_worktree(repo)["worktree"]


def _classify(
    item: dict[str, Any],
    *,
    primary_root: str,
    older_than_days: float,
    recovered_owner: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = list(item.get("changed_paths") or [])
    binding = item.get("binding") if isinstance(item.get("binding"), dict) else {}
    state = str(binding.get("state") or "")
    age, age_source = _age(binding.get("pending_since") or binding.get("updated_at"))
    if age is None:
        age, age_source = _filesystem_age(Path(item["worktree"]), paths)
    thread_id = str(binding.get("thread_id") or "")
    task_id = str(binding.get("task_id") or "")
    if state in ACTIVE_STATES | PENDING_STATES and thread_id and task_id:
        action = "owner_recall"
    elif recovered_owner and not thread_id:
        thread_id = str(recovered_owner.get("thread_id") or "")
        task_id = str(recovered_owner.get("session_id") or "")
        action = "owner_recall"
    elif state == "quarantined" or not thread_id:
        action = "quarantine_review"
    else:
        action = "owner_recall"
    can_expire = (
        str(item["worktree"]) != primary_root
        and bool(paths)
        and age is not None
        and age >= older_than_days
        and action != "owner_recall"
    )
    return {
        "worktree": item["worktree"],
        "repo_root": item.get("repo_root", primary_root),
        "branch": item.get("branch", ""),
        "head": item.get("head", ""),
        "status": item.get("status", "unknown"),
        "state": state,
        "task_id": task_id,
        "thread_id": thread_id,
        "changed_paths": paths,
        "changed_paths_sha256": item.get("changed_paths_sha256", ""),
        "action": action,
        "age_days": round(age, 3) if age is not None else None,
        "age_source": age_source,
        "expiry_candidate": can_expire,
        "expiry_reason": (
            f"secondary worktree is at least {older_than_days:g} days old and has no verified owner"
            if can_expire
            else ""
        ),
        "owner_source": "session_worktree_add" if recovered_owner and not binding.get("thread_id") else ("ledger" if thread_id else ""),
        "owner_evidence": recovered_owner or {},
    }


def inventory(
    repo: str | os.PathLike[str],
    *,
    state_dir: str | os.PathLike[str] | None = None,
    older_than_days: float = DEFAULT_EXPIRY_DAYS,
    quarantine: bool = False,
    sessions_root: str | os.PathLike[str] | None = None,
    session_index: bool = True,
) -> dict[str, Any]:
    """Produce a reconciliation report; optionally quarantine unknown dirty worktrees."""

    root = _primary_root(repo)
    report = audit(repo, state_dir=state_dir)
    candidates: list[dict[str, Any]] = []
    mutations: list[dict[str, Any]] = []
    dirty = [raw for raw in report.get("worktrees", []) if raw.get("changed_paths")]
    owner_index: dict[str, list[dict[str, Any]]] = {}
    if session_index:
        session_root = sessions_root or (Path.home() / ".codex" / "sessions")
        owner_index = index_sessions(session_root, [raw["worktree"] for raw in dirty])
    for raw in report.get("worktrees", []):
        if not raw.get("changed_paths"):
            continue
        binding = raw.get("binding") if isinstance(raw.get("binding"), dict) else {}
        recovered = None
        if not binding.get("thread_id"):
            recovered = unique_owner(owner_index.get(str(Path(raw["worktree"]).resolve()), []))
        classified = _classify(raw, primary_root=root, older_than_days=older_than_days, recovered_owner=recovered)
        if quarantine and classified["action"] == "quarantine_review" and raw.get("status") in {
            "unowned_dirty",
            "ready_dirty",
            "archived_dirty",
        }:
            result = quarantine_legacy(
                raw["worktree"],
                state_dir=state_dir,
                candidate_task_id=str((raw.get("binding") or {}).get("task_id") or ""),
                candidate_thread_id=str((raw.get("binding") or {}).get("thread_id") or ""),
            )
            mutations.append({
                "worktree": raw["worktree"],
                "status": result.get("status"),
                "queue_id": result.get("queue_id"),
            })
            # Re-read the binding only through a fresh audit on the next run;
            # this report remains an evidence snapshot of what was observed.
        candidates.append(classified)
    return {
        "schema_version": 1,
        "repo_root": root,
        "older_than_days": older_than_days,
        "worktrees": candidates,
        "expiry_candidates": [item for item in candidates if item["expiry_candidate"]],
        "queue": list_queue(state_dir),
        "mutations": mutations,
        "mutated": bool(mutations),
        "owner_index": {
            "enabled": session_index,
            "sessions_root": str(sessions_root or (Path.home() / ".codex" / "sessions")) if session_index else "",
            "unique_owner_count": sum(1 for item in candidates if item.get("owner_source") == "session_worktree_add"),
        },
    }


def _git_worktrees(repo: str | os.PathLike[str]) -> set[str]:
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            paths.add(str(Path(line[9:]).resolve()))
    return paths


def expire(
    repo: str | os.PathLike[str],
    worktrees: list[str],
    *,
    state_dir: str | os.PathLike[str] | None = None,
    older_than_days: float = DEFAULT_EXPIRY_DAYS,
    apply: bool = False,
) -> dict[str, Any]:
    """Preview or remove exactly named stale secondary worktrees."""

    report = inventory(repo, state_dir=state_dir, older_than_days=older_than_days)
    by_path = {str(Path(item["worktree"]).resolve()): item for item in report["worktrees"]}
    primary = report["repo_root"]
    registered = _git_worktrees(repo)
    results: list[dict[str, Any]] = []
    for raw in worktrees:
        target = str(Path(raw).expanduser().resolve())
        item = by_path.get(target)
        if target == primary:
            results.append({"worktree": target, "status": "refused_primary_worktree"})
            continue
        if target not in registered:
            results.append({"worktree": target, "status": "refused_not_registered"})
            continue
        if not item or not item["expiry_candidate"]:
            results.append({"worktree": target, "status": "refused_not_expired", "item": item})
            continue
        if not apply:
            results.append({"worktree": target, "status": "would_expire", "item": item})
            continue
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", target],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
        results.append({
            "worktree": target,
            "status": "expired" if result.returncode == 0 else "expire_failed",
            "stderr": result.stderr.strip(),
        })
    return {
        "schema_version": 1,
        "repo_root": primary,
        "older_than_days": older_than_days,
        "apply": apply,
        "results": results,
        "mutated": any(item["status"] == "expired" for item in results),
    }


def owner_recall_message(item: dict[str, Any]) -> str:
    """Render a concise prompt suitable for send_message_to_thread."""

    return (
        "你的任务在 "
        + str(item.get("worktree") or "当前 worktree")
        + " 停止时留下未提交改动。原任务 thread_id="
        + str(item.get("thread_id") or "unknown")
        + "。请在原任务中复核并提交，或明确 handoff；"
        "不要删除或覆盖未确认内容。处理完成后回复提交 SHA、handoff 说明或明确放弃原因。"
    )


def _emit(value: dict[str, Any]) -> int:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if value.get("ok", True) else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("inventory", help="classify dirty worktrees and pending queue")
    inv.add_argument("--repo", default=".")
    inv.add_argument("--state-dir")
    inv.add_argument("--older-than-days", type=float, default=DEFAULT_EXPIRY_DAYS)
    inv.add_argument("--quarantine", action="store_true")
    inv.add_argument("--sessions-root", help="session JSONL root used for exact worktree-add owner lookup")
    inv.add_argument("--no-session-index", action="store_true", help="do not inspect session metadata")

    exp = sub.add_parser("expire", help="preview or remove named stale secondary worktrees")
    exp.add_argument("--repo", default=".")
    exp.add_argument("--state-dir")
    exp.add_argument("--older-than-days", type=float, default=DEFAULT_EXPIRY_DAYS)
    exp.add_argument("--worktree", action="append", required=True)
    exp.add_argument("--apply", action="store_true")

    queue = sub.add_parser("queue", help="list pending queue and owner prompts")
    queue.add_argument("--state-dir")

    args = parser.parse_args(argv)
    try:
        if args.command == "inventory":
            return _emit(inventory(args.repo, state_dir=args.state_dir, older_than_days=args.older_than_days, quarantine=args.quarantine, sessions_root=args.sessions_root, session_index=not args.no_session_index))
        if args.command == "expire":
            return _emit(expire(args.repo, args.worktree, state_dir=args.state_dir, older_than_days=args.older_than_days, apply=args.apply))
        if args.command == "queue":
            items = list_queue(args.state_dir)
            return _emit({
                "ok": True,
                "items": items,
                "owner_messages": [owner_recall_message(item) for item in items if item.get("thread_id")],
                "mutated": False,
            })
    except (LifecycleError, OSError, subprocess.SubprocessError, ValueError) as exc:
        return _emit({"ok": False, "status": "error", "message": str(exc), "mutated": False})
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
