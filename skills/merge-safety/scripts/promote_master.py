#!/usr/bin/env python3
"""Run the repository-owned, non-force master promotion path.

This is deliberately separate from the emergency Git-guard bypass.  It creates
an ephemeral integration/master/* branch, performs the already-checked merge,
and pushes exactly that result with the guard's process-local promotion
contract.  Without --execute it only prints the plan.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time


PROMOTION_ENV = "COMPANY_GIT_GUARD_MASTER_PROMOTION"
TARGET_ENV = "COMPANY_GIT_GUARD_MASTER_TARGET"
SOURCE_ENV = "COMPANY_GIT_GUARD_MASTER_SOURCE"
SOURCE_RE = re.compile(r"^(feat|fix|refactor|docs|test|chore|hotfix)/[a-z0-9._/-]+$")


def run(repo: Path, *args: str, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
    return result


def value(repo: Path, *args: str) -> str:
    return run(repo, *args).stdout.strip()


def load_checked_state(repo: Path, target: str) -> dict:
    path = repo / ".codex-local" / "merge-safety" / "current.json"
    if not path.is_file():
        raise RuntimeError(f"merge-safety state is missing: {path}")
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("target_ref") not in {target, f"origin/{target}", f"refs/heads/{target}"}:
        raise RuntimeError("merge-safety state targets a different protected branch")
    if not (state.get("last_post_check") or {}).get("ok"):
        raise RuntimeError("merge-safety post-check has not passed")
    return state


def promotion_plan(repo: Path, source_ref: str, target: str, remote: str) -> dict:
    source_branch = value(repo, "symbolic-ref", "--quiet", "--short", source_ref)
    if not SOURCE_RE.fullmatch(source_branch):
        raise RuntimeError(f"source branch is not an allowed promotion source: {source_branch or source_ref}")
    target_ref = f"refs/remotes/{remote}/{target}"
    if not value(repo, "rev-parse", "--verify", target_ref):
        target_ref = target
    source_head = value(repo, "rev-parse", "--verify", source_ref)
    target_head = value(repo, "rev-parse", "--verify", target_ref)
    return {
        "repo": str(repo),
        "source_ref": source_ref,
        "source_branch": source_branch,
        "source_head": source_head,
        "target": target,
        "target_ref": target_ref,
        "target_head": target_head,
        "remote": remote,
        "operation": "non-force merge then push to protected master",
    }


def execute(plan: dict) -> dict:
    repo = Path(plan["repo"])
    checked = load_checked_state(repo, plan["target"])
    parent = Path(tempfile.mkdtemp(prefix="codex-master-promotion-", dir=str(repo.parent)))
    branch = f"integration/master/codex-{os.getpid()}-{int(time.time())}"
    worktree = parent / "worktree"
    env = os.environ.copy()
    env.update({PROMOTION_ENV: "1", TARGET_ENV: plan["target"], SOURCE_ENV: plan["source_branch"]})
    try:
        run(repo, "worktree", "add", "-b", branch, str(worktree), plan["target_ref"])
        # Resolve HEAD/branch names before changing worktrees; HEAD in the
        # ephemeral worktree is the target branch, not the checked source.
        run(worktree, "merge", "--no-ff", "--no-edit", plan["source_head"], env=env)
        result = run(worktree, "push", plan["remote"], f"HEAD:refs/heads/{plan['target']}", env=env)
        return {
            "ok": True,
            "branch": branch,
            "worktree": str(worktree),
            "target": plan["target"],
            "pushed_head": value(worktree, "rev-parse", "HEAD"),
            "checked_state": checked.get("last_post_check"),
            "push_output": result.stdout.strip(),
        }
    finally:
        run(repo, "worktree", "remove", "--force", str(worktree), check=False)
        run(repo, "branch", "-D", branch, check=False)
        shutil.rmtree(parent, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--source-ref", default="HEAD")
    parser.add_argument("--target", default="master", choices=("master", "main"))
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--execute", action="store_true", help="perform the checked promotion; otherwise print the plan")
    args = parser.parse_args()
    repo = Path(args.repo).expanduser().resolve()
    plan = promotion_plan(repo, args.source_ref, args.target, args.remote)
    if not args.execute:
        print(json.dumps({"ok": True, "mode": "plan", **plan}, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps({"ok": True, "mode": "execute", **execute(plan)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
