#!/usr/bin/env python3
"""Track ownership and handoff state for Codex-managed Git worktrees.

This module is intentionally advisory and local.  It never removes a worktree,
switches branches, or creates commits.  A later integration point may call the
same contract at task start and stop without changing the state semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
TERMINAL_STATES = {"ready", "archived"}
ACTIVE_STATES = {"active", "handoff"}
VALID_STATES = ACTIVE_STATES | TERMINAL_STATES


class LifecycleError(RuntimeError):
    """A lifecycle precondition was not met."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run_git(worktree: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=worktree,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise LifecycleError(detail)
    # Preserve leading spaces: porcelain status uses them as status columns.
    return result.stdout.rstrip("\r\n")


def discover_worktree(path: str | os.PathLike[str]) -> dict[str, str]:
    """Return stable Git identity for the worktree containing *path*."""

    candidate = Path(path).expanduser().resolve()
    root = Path(_run_git(candidate, "rev-parse", "--show-toplevel")).resolve()
    common_dir = Path(_run_git(root, "rev-parse", "--git-common-dir"))
    if not common_dir.is_absolute():
        common_dir = (root / common_dir).resolve()
    try:
        branch = _run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    except LifecycleError:
        branch = ""
    if not branch:
        branch = "detached"
    return {
        "repo_root": str(root),
        "git_common_dir": str(common_dir),
        "worktree": str(root),
        "branch": branch,
        "head": _run_git(root, "rev-parse", "HEAD"),
    }


def changed_paths(worktree: str | os.PathLike[str]) -> list[str]:
    """Return normal (non-ignored) changed paths in porcelain form."""

    root = Path(worktree).resolve()
    output = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        paths.append(path)
    return paths


def _paths_digest(paths: list[str]) -> str:
    payload = "\n".join(sorted(paths)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _artifact_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _state_root(state_dir: str | os.PathLike[str] | None, *, create: bool = False) -> Path:
    raw = state_dir or os.environ.get("CODEX_WORKSPACE_STATE_DIR")
    root = Path(raw).expanduser() if raw else Path.home() / ".codex" / "workspace-ledger-v1"
    if create:
        root.mkdir(parents=True, exist_ok=True)
        root.chmod(0o700)
    return root


def _binding_path(worktree: str | os.PathLike[str], state_dir: str | os.PathLike[str] | None, *, create: bool = False) -> Path:
    key = hashlib.sha256(str(Path(worktree).resolve()).encode("utf-8")).hexdigest()
    return _state_root(state_dir, create=create) / f"{key}.json"


def load_binding(worktree: str | os.PathLike[str], state_dir: str | os.PathLike[str] | None = None) -> dict[str, Any] | None:
    path = _binding_path(worktree, state_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleError(f"invalid lifecycle binding {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise LifecycleError(f"unsupported lifecycle binding {path}")
    return data


def _save_binding(binding: dict[str, Any], state_dir: str | os.PathLike[str] | None) -> Path:
    path = _binding_path(binding["worktree"], state_dir, create=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(binding, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)
    return path


def claim(
    worktree: str | os.PathLike[str],
    *,
    task_id: str,
    thread_id: str = "",
    owner: str = "root",
    state_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Claim a clean or already-owned worktree for a task."""

    if not task_id.strip():
        raise LifecycleError("task_id is required")
    identity = discover_worktree(worktree)
    paths = changed_paths(identity["worktree"])
    existing = load_binding(identity["worktree"], state_dir)
    if existing and existing.get("state") in ACTIVE_STATES and existing.get("task_id") != task_id:
        return {
            "ok": False,
            "status": "conflict",
            "message": "worktree is already owned by another active task",
            "binding": existing,
            "worktree": identity["worktree"],
            "changed_paths": paths,
        }
    if paths and (not existing or existing.get("task_id") != task_id):
        return {
            "ok": False,
            "status": "unowned_dirty",
            "message": "worktree has changes without a matching task claim",
            "worktree": identity["worktree"],
            "changed_paths": paths,
            "next_action": "attribute the changes or create a new dedicated worktree before writing",
        }

    history = list(existing.get("history", [])) if existing else []
    if existing and existing.get("task_id") != task_id:
        history.append({key: existing.get(key) for key in ("task_id", "thread_id", "state", "updated_at")})
    same_task = bool(existing and existing.get("task_id") == task_id)
    binding = {
        "schema_version": SCHEMA_VERSION,
        **identity,
        "task_id": task_id,
        "thread_id": thread_id,
        "owner": owner,
        "base_sha": existing.get("base_sha", identity["head"]) if same_task else identity["head"],
        "state": "active",
        "changed_paths": paths,
        "changed_paths_sha256": _paths_digest(paths),
        "created_at": existing.get("created_at", _now()) if same_task else _now(),
        "updated_at": _now(),
        "history": history,
    }
    path = _save_binding(binding, state_dir)
    return {"ok": True, "status": "active", "binding": binding, "state_file": str(path)}


def transition(
    worktree: str | os.PathLike[str],
    *,
    task_id: str,
    state: str,
    state_dir: str | os.PathLike[str] | None = None,
    artifact: str = "",
) -> dict[str, Any]:
    """Move an owned task to an explicit lifecycle state."""

    if state not in VALID_STATES:
        raise LifecycleError(f"state must be one of {sorted(VALID_STATES)}")
    identity = discover_worktree(worktree)
    binding = load_binding(identity["worktree"], state_dir)
    if not binding or binding.get("task_id") != task_id:
        raise LifecycleError("worktree is not claimed by this task")
    paths = changed_paths(identity["worktree"])
    if state == "ready" and paths:
        return {
            "ok": False,
            "status": "dirty_ready",
            "message": "ready requires a clean worktree; commit or explicitly hand off the changes",
            "changed_paths": paths,
        }
    if state == "archived" and paths:
        return {
            "ok": False,
            "status": "dirty_archive",
            "message": "archived requires a clean worktree or a separately preserved handoff artifact",
            "changed_paths": paths,
        }
    if state == "handoff":
        if not artifact:
            raise LifecycleError("handoff requires an artifact path")
        artifact_path = Path(artifact).expanduser().resolve()
        if not artifact_path.is_file():
            raise LifecycleError(f"handoff artifact does not exist: {artifact_path}")
        binding["handoff_artifact"] = str(artifact_path)
        binding["handoff_artifact_sha256"] = _artifact_digest(artifact_path)
    binding.update(identity)
    binding["state"] = state
    binding["changed_paths"] = paths
    binding["changed_paths_sha256"] = _paths_digest(paths)
    binding["updated_at"] = _now()
    path = _save_binding(binding, state_dir)
    return {"ok": True, "status": state, "binding": binding, "state_file": str(path)}


def _parse_worktree_porcelain(output: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        if line.startswith("worktree "):
            if current:
                records.append(current)
            current = {"worktree": line[9:]}
        elif line.startswith("HEAD "):
            current["head"] = line[5:]
        elif line.startswith("branch "):
            current["branch"] = line[7:].removeprefix("refs/heads/")
        elif line == "detached":
            current["branch"] = "detached"
        elif line == "bare":
            current["bare"] = "true"
    if current:
        records.append(current)
    return [item for item in records if item.get("bare") != "true"]


def audit(repo_path: str | os.PathLike[str], *, state_dir: str | os.PathLike[str] | None = None, stale_after_hours: float = 0) -> dict[str, Any]:
    """Classify every registered worktree without changing Git state."""

    repo = Path(repo_path).expanduser().resolve()
    root_identity = discover_worktree(repo)
    records = _parse_worktree_porcelain(_run_git(repo, "worktree", "list", "--porcelain"))
    observed: list[dict[str, Any]] = []
    seen: set[str] = set()
    now = datetime.now(timezone.utc)
    for record in records:
        worktree = Path(record["worktree"]).resolve()
        paths: list[str]
        try:
            paths = changed_paths(worktree)
        except FileNotFoundError as exc:
            observed.append({**record, "worktree": str(worktree), "status": "missing_worktree", "error": str(exc)})
            continue
        except (LifecycleError, OSError) as exc:
            observed.append({**record, "worktree": str(worktree), "status": "unknown", "error": str(exc)})
            continue
        seen.add(str(worktree))
        binding = load_binding(worktree, state_dir)
        status = "clean"
        if paths and not binding:
            status = "unowned_dirty"
        elif binding:
            binding_state = str(binding.get("state", ""))
            status = f"{binding_state}_{'dirty' if paths else 'clean'}"
            if stale_after_hours > 0 and binding_state in ACTIVE_STATES:
                try:
                    updated = datetime.fromisoformat(str(binding["updated_at"]))
                    if (now - updated).total_seconds() > stale_after_hours * 3600:
                        status = "stale"
                except (KeyError, TypeError, ValueError):
                    status = "unknown"
        observed.append({
            **record,
            "worktree": str(worktree),
            "changed_paths": paths,
            "changed_paths_sha256": _paths_digest(paths),
            "status": status,
            "binding": binding,
        })
    stale_bindings: list[dict[str, Any]] = []
    for path in _state_root(state_dir).glob("*.json"):
        try:
            binding = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stale_bindings.append({"state_file": str(path), "status": "unknown"})
            continue
        if isinstance(binding, dict) and binding.get("worktree") not in seen:
            stale_bindings.append({"state_file": str(path), "status": "stale_binding", "binding": binding})
    counts: dict[str, int] = {}
    for item in observed:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "repo_root": root_identity["repo_root"],
        "git_common_dir": root_identity["git_common_dir"],
        "worktree_count": len(observed),
        "counts": dict(sorted(counts.items())),
        "worktrees": observed,
        "stale_bindings": stale_bindings,
        "mutated": False,
    }


def _emit(value: dict[str, Any]) -> int:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if value.get("ok", True) else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_parser = sub.add_parser("inspect")
    inspect_parser.add_argument("--worktree", default=".")
    inspect_parser.add_argument("--state-dir")

    claim_parser = sub.add_parser("claim")
    claim_parser.add_argument("--worktree", default=".")
    claim_parser.add_argument("--task-id", required=True)
    claim_parser.add_argument("--thread-id", default="")
    claim_parser.add_argument("--owner", default="root")
    claim_parser.add_argument("--state-dir")

    transition_parser = sub.add_parser("transition")
    transition_parser.add_argument("--worktree", default=".")
    transition_parser.add_argument("--task-id", required=True)
    transition_parser.add_argument("--state", choices=sorted(VALID_STATES), required=True)
    transition_parser.add_argument("--artifact", default="")
    transition_parser.add_argument("--state-dir")

    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--repo", default=".")
    audit_parser.add_argument("--state-dir")
    audit_parser.add_argument("--stale-after-hours", type=float, default=0)

    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            identity = discover_worktree(args.worktree)
            paths = changed_paths(identity["worktree"])
            return _emit({**identity, "changed_paths": paths, "changed_paths_sha256": _paths_digest(paths), "binding": load_binding(identity["worktree"], args.state_dir), "mutated": False})
        if args.command == "claim":
            return _emit(claim(args.worktree, task_id=args.task_id, thread_id=args.thread_id, owner=args.owner, state_dir=args.state_dir))
        if args.command == "transition":
            return _emit(transition(args.worktree, task_id=args.task_id, state=args.state, state_dir=args.state_dir, artifact=args.artifact))
        if args.command == "audit":
            return _emit(audit(args.repo, state_dir=args.state_dir, stale_after_hours=args.stale_after_hours))
    except LifecycleError as exc:
        return _emit({"ok": False, "status": "error", "message": str(exc), "mutated": False})
    return 2


if __name__ == "__main__":
    sys.exit(main())
