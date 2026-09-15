from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import workspace_lifecycle as lifecycle
import workspace_lifecycle_reconcile as reconcile


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "codex@example.test")
    git(repo, "config", "user.name", "Codex Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "base")
    return repo


class WorkspaceLifecycleReconcileTests(unittest.TestCase):
    def test_inventory_classifies_known_owner_and_unowned_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root)
            child = root / "child"
            git(repo, "worktree", "add", "-q", "-b", "child", str(child))
            state = root / "state"
            lifecycle.claim(child, task_id="task-1", thread_id="thread-1", state_dir=state)
            (child / "README.md").write_text("owned\n", encoding="utf-8")
            (repo / "README.md").write_text("unowned\n", encoding="utf-8")

            report = reconcile.inventory(repo, state_dir=state)

            self.assertFalse(report["mutated"])
            by_path = {item["worktree"]: item for item in report["worktrees"]}
            self.assertEqual(by_path[str(child.resolve())]["action"], "owner_recall")
            self.assertEqual(by_path[str(repo.resolve())]["action"], "quarantine_review")
            self.assertFalse(report["expiry_candidates"])

    def test_quarantine_option_is_explicit_and_preserves_candidate_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root)
            state = root / "state"
            (repo / "README.md").write_text("legacy\n", encoding="utf-8")

            report = reconcile.inventory(repo, state_dir=state, quarantine=True)

            self.assertTrue(report["mutated"])
            self.assertEqual(report["mutations"][0]["status"], "quarantined")
            binding = lifecycle.load_binding(repo, state)
            self.assertEqual(binding["state"], "quarantined")
            self.assertEqual(binding["task_id"], "")

    def test_expire_is_dry_run_by_default_and_refuses_primary_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root)
            state = root / "state"
            (repo / "README.md").write_text("old\n", encoding="utf-8")

            preview = reconcile.expire(repo, [str(repo)], state_dir=state, older_than_days=0)

            self.assertFalse(preview["mutated"])
            self.assertEqual(preview["results"][0]["status"], "refused_primary_worktree")
            self.assertEqual(git(repo, "status", "--porcelain"), "M README.md")

    def test_expire_removes_only_named_stale_secondary_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root)
            child = root / "child"
            git(repo, "worktree", "add", "-q", "-b", "child", str(child))
            state = root / "state"
            (child / "README.md").write_text("legacy\n", encoding="utf-8")
            # A filesystem mtime is the only available lower-bound evidence for
            # an unowned worktree; zero days makes this an explicit test target.
            preview = reconcile.expire(repo, [str(child)], state_dir=state, older_than_days=0)
            self.assertEqual(preview["results"][0]["status"], "would_expire")
            self.assertTrue(child.exists())

            applied = reconcile.expire(repo, [str(child)], state_dir=state, older_than_days=0, apply=True)

            self.assertTrue(applied["mutated"])
            self.assertEqual(applied["results"][0]["status"], "expired")
            self.assertFalse(child.exists())
            self.assertNotIn(str(child.resolve()), {str(item["worktree"]) for item in reconcile.inventory(repo, state_dir=state)["worktrees"]})

    def test_cli_queue_renders_owner_message_without_payload_contents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root)
            state = root / "state"
            lifecycle.claim(repo, task_id="task-1", thread_id="thread-1", state_dir=state)
            (repo / "README.md").write_text("dirty\n", encoding="utf-8")
            lifecycle.record_pending_closure(repo, task_id="task-1", state_dir=state)

            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/workspace_lifecycle_reconcile.py"), "queue", "--state-dir", str(state)],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload["items"]), 1)
            self.assertIn("thread-1", payload["owner_messages"][0])
            self.assertNotIn("dirty", payload["owner_messages"][0])


if __name__ == "__main__":
    unittest.main()
