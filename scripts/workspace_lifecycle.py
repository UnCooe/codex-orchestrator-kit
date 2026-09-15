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
PENDING_STATES = {"pending_closure", "quarantined"}
TERMINAL_STATES = {"ready", "archived", "expired"}
ACTIVE_STATES = {"active", "handoff"}
VALID_STATES = ACTIVE_STATES | PENDING_STATES | TERMINAL_STATES


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


def _queue_root(state_dir: str | os.PathLike[str] | None, *, create: bool = False) -> Path:
    root = _state_root(state_dir, create=create) / "queue"
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


def _queue_id(worktree: str, paths_sha256: str) -> str:
    return hashlib.sha256(f"{Path(worktree).resolve()}\n{paths_sha256}".encode("utf-8")).hexdigest()


def _save_queue_item(item: dict[str, Any], state_dir: str | os.PathLike[str] | None) -> Path:
    path = _queue_root(state_dir, create=True) / f"{item['queue_id']}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)
    return path


def list_queue(state_dir: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    """Return pending reconciliation items without changing the queue."""

    items: list[dict[str, Any]] = []
    root = _queue_root(state_dir)
    if not root.is_dir():
        return items
    for path in sorted(root.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            items.append({"queue_file": str(path), "status": "invalid", "error": str(exc)})
            continue
        if isinstance(item, dict):
            item.setdefault("queue_file", str(path))
            if item.get("status", "pending") == "pending":
                items.append(item)
    return items


def _resolve_queue_for_worktree(
    worktree: str | os.PathLike[str],
    state_dir: str | os.PathLike[str] | None,
    *,
    reason: str,
) -> int:
    """Mark queue items resolved after the worktree reaches a clean terminal state."""

    count = 0
    root = _queue_root(state_dir)
    if not root.is_dir():
        return count
    target = str(Path(worktree).resolve())
    for path in sorted(root.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(item, dict) or str(Path(str(item.get("worktree", ""))).resolve()) != target:
            continue
        if item.get("status", "pending") != "pending":
            continue
        item["status"] = "resolved"
        item["resolved_at"] = _now()
        item["resolution"] = reason
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
        count += 1
    return count


def _queue_item(
    *,
    identity: dict[str, str],
    task_id: str,
    thread_id: str,
    owner: str,
    paths: list[str],
    kind: str,
    reason: str,
    owner_confidence: str,
    state: str,
) -> dict[str, Any]:
    paths_sha256 = _paths_digest(paths)
    now = _now()
    return {
        "schema_version": SCHEMA_VERSION,
        "queue_id": _queue_id(identity["worktree"], paths_sha256),
        "status": "pending",
        "kind": kind,
        "state": state,
        "reason": reason,
        "owner_confidence": owner_confidence,
        "repo_root": identity["repo_root"],
        "worktree": identity["worktree"],
        "branch": identity["branch"],
        "head": identity["head"],
        "task_id": task_id,
        "thread_id": thread_id,
        "owner": owner,
        "changed_paths": paths,
        "changed_paths_sha256": paths_sha256,
        "created_at": now,
        "updated_at": now,
    }


def record_pending_closure(
    worktree: str | os.PathLike[str],
    *,
    task_id: str,
    thread_id: str = "",
    owner: str = "root",
    state_dir: str | os.PathLike[str] | None = None,
    reason: str = "dirty_stop",
) -> dict[str, Any]:
    """Persist an owner recall item when a known task stops with changes.

    Only lifecycle metadata and changed path names are recorded. Git content is
    never modified, and the item remains pending until the owner commits or
    explicitly hands it off.
    """

    if not task_id.strip():
        raise LifecycleError("task_id is required")
    identity = discover_worktree(worktree)
    paths = changed_paths(identity["worktree"])
    binding = load_binding(identity["worktree"], state_dir)
    if not binding or binding.get("task_id") != task_id:
        return {
            "ok": False,
            "status": "unowned_dirty" if paths else "clean",
            "message": "cannot create an owner-closure item without a matching task claim",
            "changed_paths": paths,
            "mutated": False,
        }
    if not paths:
        return {"ok": True, "status": "clean", "message": "worktree is clean", "mutated": False}
    if binding.get("state") not in ACTIVE_STATES | PENDING_STATES:
        return {
            "ok": False,
            "status": "not_active",
            "message": "only an active or pending task can create an owner-closure item",
            "changed_paths": paths,
            "mutated": False,
        }
    binding.update(identity)
    binding["state"] = "pending_closure"
    binding["thread_id"] = thread_id or str(binding.get("thread_id") or "")
    binding["owner"] = owner or str(binding.get("owner") or "root")
    binding["pending_reason"] = reason
    binding["pending_since"] = str(binding.get("pending_since") or _now())
    binding["changed_paths"] = paths
    binding["changed_paths_sha256"] = _paths_digest(paths)
    binding["updated_at"] = _now()
    state_file = _save_binding(binding, state_dir)
    item = _queue_item(
        identity=identity,
        task_id=task_id,
        thread_id=str(binding.get("thread_id") or ""),
        owner=str(binding.get("owner") or "root"),
        paths=paths,
        kind="owner_recall",
        reason=reason,
        owner_confidence="bound",
        state="pending_closure",
    )
    item["pending_since"] = binding["pending_since"]
    queue_file = _save_queue_item(item, state_dir)
    return {
        "ok": True,
        "status": "pending_closure",
        "binding": binding,
        "state_file": str(state_file),
        "queue_file": str(queue_file),
        "queue_id": item["queue_id"],
        "changed_paths": paths,
        "mutated": True,
    }


def quarantine_legacy(
    worktree: str | os.PathLike[str],
    *,
    state_dir: str | os.PathLike[str] | None = None,
    reason: str = "legacy_dirty_without_verified_owner",
    candidate_task_id: str = "",
    candidate_thread_id: str = "",
) -> dict[str, Any]:
    """Isolate an unattributed dirty worktree without claiming its content."""

    identity = discover_worktree(worktree)
    paths = changed_paths(identity["worktree"])
    if not paths:
        return {"ok": True, "status": "clean", "mutated": False, "changed_paths": []}
    existing = load_binding(identity["worktree"], state_dir)
    previous = dict(existing) if existing else None
    pending_since = str((existing or {}).get("pending_since") or _now())
    binding = {
        "schema_version": SCHEMA_VERSION,
        **identity,
        "state": "quarantined",
        "task_id": "",
        "thread_id": "",
        "owner": "",
        "owner_confidence": "unknown",
        "historical_task_id": candidate_task_id or str((existing or {}).get("task_id") or ""),
        "historical_thread_id": candidate_thread_id or str((existing or {}).get("thread_id") or ""),
        "previous_binding": previous,
        "quarantine_reason": reason,
        "pending_since": pending_since,
        "changed_paths": paths,
        "changed_paths_sha256": _paths_digest(paths),
        "created_at": str((existing or {}).get("created_at") or _now()),
        "updated_at": _now(),
        "history": list((existing or {}).get("history", [])),
    }
    state_file = _save_binding(binding, state_dir)
    item = _queue_item(
        identity=identity,
        task_id="",
        thread_id="",
        owner="",
        paths=paths,
        kind="legacy_quarantine",
        reason=reason,
        owner_confidence="unknown",
        state="quarantined",
    )
    item.update({
        "historical_task_id": binding["historical_task_id"],
        "historical_thread_id": binding["historical_thread_id"],
        "pending_since": pending_since,
    })
    queue_file = _save_queue_item(item, state_dir)
    return {
        "ok": True,
        "status": "quarantined",
        "binding": binding,
        "state_file": str(state_file),
        "queue_file": str(queue_file),
        "queue_id": item["queue_id"],
        "changed_paths": paths,
        "mutated": True,
    }


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
    if existing and existing.get("state") in PENDING_STATES and existing.get("task_id") != task_id:
        pending_status = "quarantined" if existing.get("state") == "quarantined" else "pending_owner"
        return {
            "ok": False,
            "status": pending_status,
            "message": "worktree has unresolved changes reserved for its recorded owner; use the owner-closure queue",
            "binding": existing,
            "worktree": identity["worktree"],
            "changed_paths": paths,
        }
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
    if existing and existing.get("state") in PENDING_STATES and same_task:
        binding["resumed_from"] = existing.get("state")
        binding["resumed_at"] = _now()
    path = _save_binding(binding, state_dir)
    return {"ok": True, "status": "active", "binding": binding, "state_file": str(path)}


def adopt(
    worktree: str | os.PathLike[str],
    *,
    task_id: str,
    thread_id: str = "",
    owner: str = "root",
    state_dir: str | os.PathLike[str] | None = None,
    reason: str = "explicit_owner_handoff",
    previous_task_id: str = "",
) -> dict[str, Any]:
    """Explicitly take ownership of terminal-state dirty changes.

    This is the escape hatch for a task that has verified the previous owner
    is complete or unavailable. It records the old binding and reason; it does
    not hide provenance and never changes Git content.
    """

    if not task_id.strip():
        raise LifecycleError("task_id is required")
    identity = discover_worktree(worktree)
    paths = changed_paths(identity["worktree"])
    existing = load_binding(identity["worktree"], state_dir)
    if not paths:
        return {"ok": False, "status": "clean", "message": "adopt requires dirty changes", "mutated": False}
    if existing and existing.get("state") in ACTIVE_STATES and existing.get("task_id") != task_id:
        return {
            "ok": False,
            "status": "conflict",
            "message": "cannot adopt changes from another active task",
            "binding": existing,
            "changed_paths": paths,
            "mutated": False,
        }
    prior_id = str((existing or {}).get("task_id") or "")
    if previous_task_id and prior_id and previous_task_id != prior_id:
        raise LifecycleError("previous_task_id does not match the recorded binding")
    history = list((existing or {}).get("history", []))
    if existing:
        history.append({
            "task_id": existing.get("task_id", ""),
            "thread_id": existing.get("thread_id", ""),
            "state": existing.get("state", ""),
            "updated_at": existing.get("updated_at", ""),
            "adopted": True,
        })
    binding = {
        "schema_version": SCHEMA_VERSION,
        **identity,
        "task_id": task_id,
        "thread_id": thread_id,
        "owner": owner,
        # The old binding may predate a commit made by its owner. The current
        # Git HEAD is the only safe base for newly adopted edits; the complete
        # old binding remains in ``previous_binding`` for provenance.
        "base_sha": identity["head"],
        "state": "active",
        "changed_paths": paths,
        "changed_paths_sha256": _paths_digest(paths),
        "created_at": _now(),
        "updated_at": _now(),
        "history": history,
        "adopted_from": prior_id,
        "adoption_reason": reason,
        "adopted_at": _now(),
        "previous_binding": existing,
    }
    state_file = _save_binding(binding, state_dir)
    return {
        "ok": True,
        "status": "active",
        "binding": binding,
        "state_file": str(state_file),
        "changed_paths": paths,
        "mutated": True,
    }


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
    if state == "pending_closure":
        if not paths:
            raise LifecycleError("pending_closure requires a dirty worktree")
        return record_pending_closure(
            identity["worktree"],
            task_id=task_id,
            thread_id=str(binding.get("thread_id") or ""),
            owner=str(binding.get("owner") or "root"),
            state_dir=state_dir,
            reason="explicit_pending_closure",
        )
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
    if state in {"ready", "archived", "expired"}:
        _resolve_queue_for_worktree(identity["worktree"], state_dir, reason=f"transition:{state}")
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

    adopt_parser = sub.add_parser("adopt", help="explicitly adopt terminal-state dirty changes")
    adopt_parser.add_argument("--worktree", default=".")
    adopt_parser.add_argument("--task-id", required=True)
    adopt_parser.add_argument("--thread-id", default="")
    adopt_parser.add_argument("--owner", default="root")
    adopt_parser.add_argument("--previous-task-id", default="")
    adopt_parser.add_argument("--reason", default="explicit_owner_handoff")
    adopt_parser.add_argument("--state-dir")

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

    queue_parser = sub.add_parser("queue", help="list pending owner-recall and quarantine items")
    queue_parser.add_argument("--state-dir")

    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            identity = discover_worktree(args.worktree)
            paths = changed_paths(identity["worktree"])
            return _emit({**identity, "changed_paths": paths, "changed_paths_sha256": _paths_digest(paths), "binding": load_binding(identity["worktree"], args.state_dir), "mutated": False})
        if args.command == "claim":
            return _emit(claim(args.worktree, task_id=args.task_id, thread_id=args.thread_id, owner=args.owner, state_dir=args.state_dir))
        if args.command == "adopt":
            return _emit(adopt(
                args.worktree,
                task_id=args.task_id,
                thread_id=args.thread_id,
                owner=args.owner,
                previous_task_id=args.previous_task_id,
                reason=args.reason,
                state_dir=args.state_dir,
            ))
        if args.command == "transition":
            return _emit(transition(args.worktree, task_id=args.task_id, state=args.state, state_dir=args.state_dir, artifact=args.artifact))
        if args.command == "audit":
            return _emit(audit(args.repo, state_dir=args.state_dir, stale_after_hours=args.stale_after_hours))
        if args.command == "queue":
            return _emit({"ok": True, "items": list_queue(args.state_dir), "mutated": False})
    except LifecycleError as exc:
        return _emit({"ok": False, "status": "error", "message": str(exc), "mutated": False})
    return 2


if __name__ == "__main__":
    sys.exit(main())
