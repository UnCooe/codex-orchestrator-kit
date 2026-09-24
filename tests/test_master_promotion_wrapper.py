"""Integration tests for the guarded master promotion entrypoint."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "skills/merge-safety/scripts/promote_master.py"
GUARD = Path.home() / ".codex/git-guard/bin/git_guard.py"


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def commit(repo: Path, message: str, content: str) -> str:
    (repo / "VERSION").write_text(content, encoding="utf-8")
    git(repo, "add", "VERSION")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def install_hooks(repo: Path, hooks: Path) -> None:
    hooks.mkdir()
    for name, command in (("pre-commit", "pre-commit"), ("pre-push", "pre-push")):
        path = hooks / name
        path.write_text(f"#!/bin/sh\nexec {sys.executable} {GUARD} {command}\n", encoding="utf-8")
        path.chmod(0o755)
    git(repo, "config", "core.hooksPath", str(hooks))


def test_wrapper_promotes_checked_source_without_bypass(tmp_path: Path):
    bare = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    hooks = tmp_path / "hooks"
    git(tmp_path, "init", "--bare", str(bare))
    git(tmp_path, "init", str(repo))
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "checkout", "-b", "master")
    # Seed the protected branch before installing the local guard.
    base = commit(repo, "base", "base")
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-q", "origin", "master")
    install_hooks(repo, hooks)
    git(repo, "update-ref", "refs/remotes/origin/master", base)
    git(repo, "checkout", "-b", "fix/promotion")
    source = commit(repo, "validated change", "change")
    state_path = repo / ".codex-local/merge-safety/current.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "target_ref": "master",
        "last_post_check": {"ok": True, "checked_at": "2026-09-21T00:00:00Z"},
    }), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(WRAPPER), "--repo", str(repo), "--source-ref", "HEAD", "--target", "master", "--execute"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    pushed = git(repo, "--git-dir", str(bare), "rev-parse", "refs/heads/master").stdout.strip()
    assert pushed != base
    assert pushed != source
    assert git(repo, "--git-dir", str(bare), "merge-base", "--is-ancestor", source, pushed, check=False).returncode == 0


def test_wrapper_is_plan_only_without_execute(tmp_path: Path):
    repo = tmp_path / "repo"
    git(tmp_path, "init", str(repo))
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "checkout", "-b", "master")
    base = commit(repo, "base", "base")
    git(repo, "checkout", "-b", "fix/plan")
    commit(repo, "change", "change")
    git(repo, "update-ref", "refs/remotes/origin/master", base)
    state_path = repo / ".codex-local/merge-safety/current.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"target_ref": "master", "last_post_check": {"ok": True}}), encoding="utf-8")
    result = subprocess.run([sys.executable, str(WRAPPER), "--repo", str(repo)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert result.returncode == 0
    assert json.loads(result.stdout)["mode"] == "plan"
