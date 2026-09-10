import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "branch_lineage.py"


def run(cmd, cwd: Path, check: bool = True):
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def git(repo: Path, *args: str) -> str:
    return run(["git", *args], repo).stdout.strip()


def init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "master")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    (path / "README.md").write_text("base\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "base")


def lineage(repo: Path, *args: str, expect: int = 0) -> dict:
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = repo / common
    env = os.environ.copy()
    env["CODEX_MERGE_LINEAGE_COMMON_GIT_DIR"] = str(common.resolve())
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--repo", str(repo)],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if proc.returncode != expect:
        raise AssertionError(f"exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}")
    if not proc.stdout:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout)


def commit_file(repo: Path, name: str, contents: str, message: str) -> str:
    (repo / name).write_text(contents, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def attach_origin(repo: Path) -> Path:
    origin = repo.parent / "origin.git"
    run(["git", "init", "--bare", str(origin)], repo)
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-u", "origin", "master")
    return origin


class BranchLineageTests(unittest.TestCase):
    def test_register_source_and_adopt_are_explicit(self):
        # An ordinary registration is allowed for a newly-created feature branch.
        # A pre-existing branch must require --adopt rather than being inferred.
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            init_repo(repo)
            git(repo, "switch", "-c", "feat/lineage")
            tip = git(repo, "rev-parse", "HEAD")

            registered = lineage(repo, "register-source", "--source-branch", "feat/lineage", "--worktree", str(repo))
            self.assertEqual(registered["event"]["source_branch"], "feat/lineage")
            self.assertEqual(registered["event"]["source_tip"], tip)

            common = Path(git(repo, "rev-parse", "--git-common-dir"))
            if not common.is_absolute():
                common = repo / common
            event_log = common / "codex" / "lineage" / "v1.jsonl"
            self.assertTrue(event_log.is_file())
            events = [json.loads(line) for line in event_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[-1]["kind"], "source_registered")
            self.assertEqual(events[-1]["source_branch"], "feat/lineage")

            git(repo, "branch", "fix/legacy")
            refused = lineage(repo, "register-source", "--source-branch", "fix/legacy", expect=1)
            self.assertIn("adopt", json.dumps(refused).lower())
            adopted = lineage(repo, "register-source", "--source-branch", "fix/legacy", "--adopt")
            self.assertEqual(adopted["event"]["source_branch"], "fix/legacy")

    def test_pre_events_are_pending_then_completed_and_resolve_source(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            init_repo(repo)
            attach_origin(repo)
            base = git(repo, "rev-parse", "HEAD")
            git(repo, "switch", "-c", "feat/a")
            source_tip = commit_file(repo, "a.txt", "A\n", "feature A")
            lineage(repo, "register-source", "--source-branch", "feat/a", "--worktree", str(repo))

            git(repo, "switch", "-c", "pre", base)
            git(repo, "merge", "--no-ff", "feat/a", "-m", "integrate A")
            integration = git(repo, "rev-parse", "HEAD")
            pending = lineage(
                repo,
                "prepare-pre",
                "--source-branch", "feat/a",
                "--source-tip", source_tip,
                "--pre-before", base,
                "--integration-sha", integration,
                "--mode", "merge",
                "--validated-ref", integration,
            )
            operation_id = pending["operation_id"]
            self.assertEqual(pending["event"]["kind"], "pre_pending")

            git(repo, "push", "origin", "HEAD:pre")

            completed = lineage(repo, "record-pre", "--operation-id", operation_id, "--pre-after", integration)
            self.assertEqual(completed["event"]["kind"], "pre_integrated")
            resolved = lineage(repo, "resolve-repair", "--pre-ref", integration)
            self.assertEqual(resolved["status"], "resolved")
            self.assertEqual(resolved["candidate"]["source_branch"], "feat/a")

    def test_master_records_hotfix_as_pre_sync_pending(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            init_repo(repo)
            attach_origin(repo)
            before = git(repo, "rev-parse", "HEAD")
            git(repo, "switch", "-c", "hotfix/login", before)
            tip = commit_file(repo, "hotfix.txt", "fix\n", "hotfix")
            lineage(repo, "register-source", "--source-branch", "hotfix/login", "--worktree", str(repo))
            git(repo, "switch", "master")
            git(repo, "merge", "--ff-only", "hotfix/login")
            git(repo, "push", "origin", "master")
            event = lineage(
                repo,
                "record-master",
                "--source-branch", "hotfix/login",
                "--source-tip", tip,
                "--master-before", before,
                "--master-after", tip,
                "--hotfix",
            )
            self.assertEqual(event["event"]["source_branch"], "hotfix/login")
            audit = lineage(repo, "audit", expect=1)
            self.assertIn("hotfix/login", json.dumps(audit))
            self.assertIn("pending", json.dumps(audit).lower())

    def test_hotfix_flag_requires_hotfix_source_branch(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            init_repo(repo)
            attach_origin(repo)
            git(repo, "switch", "-c", "fix/not-a-hotfix")
            tip = commit_file(repo, "fix.txt", "fix\n", "fix")
            lineage(repo, "register-source", "--source-branch", "fix/not-a-hotfix")
            refused = lineage(
                repo,
                "validate-master",
                "--source-branch",
                "fix/not-a-hotfix",
                "--source-tip",
                tip,
                "--hotfix",
                expect=1,
            )
            self.assertIn("hotfix/*", refused["message"])


if __name__ == "__main__":
    unittest.main()
