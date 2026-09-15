from __future__ import annotations

import json
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import workspace_lifecycle as lifecycle


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


class WorkspaceLifecycleTests(unittest.TestCase):
    def test_dirty_unowned_worktree_is_rejected_without_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            repo = make_repo(tmp_path)
            state = tmp_path / "state"
            (repo / "README.md").write_text("unowned\n", encoding="utf-8")

            result = lifecycle.claim(repo, task_id="task-1", state_dir=state)

            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "unowned_dirty")
            self.assertEqual(list(state.glob("*.json")), [])


    def test_same_task_can_resume_dirty_worktree_but_other_task_cannot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            repo = make_repo(tmp_path)
            state = tmp_path / "state"
            first = lifecycle.claim(repo, task_id="task-1", thread_id="thread-1", state_dir=state)
            self.assertTrue(first["ok"])
            (repo / "README.md").write_text("task one\n", encoding="utf-8")

            resumed = lifecycle.claim(repo, task_id="task-1", thread_id="thread-1", state_dir=state)
            other = lifecycle.claim(repo, task_id="task-2", thread_id="thread-2", state_dir=state)

            self.assertTrue(resumed["ok"])
            self.assertEqual(resumed["binding"]["state"], "active")
            self.assertFalse(other["ok"])
            self.assertEqual(other["status"], "conflict")

    def test_pending_closure_records_owner_recall_without_touching_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            repo = make_repo(tmp_path)
            state = tmp_path / "state"
            lifecycle.claim(repo, task_id="task-1", thread_id="thread-1", state_dir=state)
            (repo / "README.md").write_text("unfinished\n", encoding="utf-8")

            result = lifecycle.record_pending_closure(repo, task_id="task-1", state_dir=state)

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "pending_closure")
            self.assertEqual(lifecycle.load_binding(repo, state)["state"], "pending_closure")
            queue = lifecycle.list_queue(state)
            self.assertEqual(len(queue), 1)
            self.assertEqual(queue[0]["kind"], "owner_recall")
            self.assertEqual(queue[0]["thread_id"], "thread-1")
            self.assertEqual(git(repo, "status", "--porcelain"), "M README.md")

    def test_pending_owner_blocks_other_task_but_original_owner_can_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            repo = make_repo(tmp_path)
            state = tmp_path / "state"
            lifecycle.claim(repo, task_id="task-1", thread_id="thread-1", state_dir=state)
            (repo / "README.md").write_text("unfinished\n", encoding="utf-8")
            lifecycle.record_pending_closure(repo, task_id="task-1", state_dir=state)

            other = lifecycle.claim(repo, task_id="task-2", state_dir=state)
            resumed = lifecycle.claim(repo, task_id="task-1", thread_id="thread-1", state_dir=state)

            self.assertFalse(other["ok"])
            self.assertEqual(other["status"], "pending_owner")
            self.assertTrue(resumed["ok"])
            self.assertEqual(resumed["binding"]["state"], "active")

    def test_quarantine_preserves_history_without_assigning_an_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            repo = make_repo(tmp_path)
            state = tmp_path / "state"
            (repo / "README.md").write_text("legacy\n", encoding="utf-8")

            result = lifecycle.quarantine_legacy(
                repo,
                state_dir=state,
                candidate_task_id="legacy-task",
                candidate_thread_id="legacy-thread",
            )

            self.assertTrue(result["ok"])
            binding = lifecycle.load_binding(repo, state)
            self.assertEqual(binding["state"], "quarantined")
            self.assertEqual(binding["task_id"], "")
            self.assertEqual(binding["historical_thread_id"], "legacy-thread")
            self.assertEqual(lifecycle.list_queue(state)[0]["kind"], "legacy_quarantine")

    def test_adopt_requires_explicit_previous_owner_and_records_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            repo = make_repo(tmp_path)
            state = tmp_path / "state"
            lifecycle.claim(repo, task_id="old-task", thread_id="old-thread", state_dir=state)
            lifecycle.transition(repo, task_id="old-task", state="ready", state_dir=state)
            (repo / "README.md").write_text("late change\n", encoding="utf-8")

            adopted = lifecycle.adopt(
                repo,
                task_id="new-task",
                thread_id="new-thread",
                previous_task_id="old-task",
                reason="old task confirmed complete",
                state_dir=state,
            )

            self.assertTrue(adopted["ok"])
            binding = lifecycle.load_binding(repo, state)
            self.assertEqual(binding["task_id"], "new-task")
            self.assertEqual(binding["adopted_from"], "old-task")
            self.assertEqual(binding["previous_binding"]["thread_id"], "old-thread")


    def test_ready_requires_clean_and_handoff_records_artifact_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            repo = make_repo(tmp_path)
            state = tmp_path / "state"
            lifecycle.claim(repo, task_id="task-1", state_dir=state)
            (repo / "README.md").write_text("unfinished\n", encoding="utf-8")

            blocked = lifecycle.transition(repo, task_id="task-1", state="ready", state_dir=state)
            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked["status"], "dirty_ready")

            artifact = tmp_path / "handoff.patch"
            artifact.write_text("patch evidence\n", encoding="utf-8")
            handoff = lifecycle.transition(repo, task_id="task-1", state="handoff", artifact=str(artifact), state_dir=state)
            self.assertTrue(handoff["ok"])
            self.assertEqual(handoff["binding"]["handoff_artifact_sha256"], lifecycle._artifact_digest(artifact))

            git(repo, "add", "README.md")
            git(repo, "commit", "-qm", "finish")
            ready = lifecycle.transition(repo, task_id="task-1", state="ready", state_dir=state)
            self.assertTrue(ready["ok"])


    def test_terminal_binding_allows_explicit_new_task_only_after_clean_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            repo = make_repo(tmp_path)
            state = tmp_path / "state"
            lifecycle.claim(repo, task_id="task-1", state_dir=state)
            lifecycle.transition(repo, task_id="task-1", state="ready", state_dir=state)

            next_task = lifecycle.claim(repo, task_id="task-2", state_dir=state)

            self.assertTrue(next_task["ok"])
            self.assertEqual(next_task["binding"]["task_id"], "task-2")
            self.assertEqual(next_task["binding"]["history"][0]["task_id"], "task-1")
            self.assertEqual(next_task["binding"]["base_sha"], git(repo, "rev-parse", "HEAD"))


    def test_audit_classifies_unowned_dirty_and_reports_no_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            repo = make_repo(tmp_path)
            state = tmp_path / "state"
            (repo / "README.md").write_text("dirty\n", encoding="utf-8")

            report = lifecycle.audit(repo, state_dir=state)

            self.assertFalse(report["mutated"])
            self.assertEqual(report["worktree_count"], 1)
            self.assertEqual(report["counts"], {"unowned_dirty": 1})
            self.assertEqual(report["worktrees"][0]["changed_paths"], ["README.md"])


    def test_cli_audit_emits_machine_readable_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            repo = make_repo(tmp_path)
            state = tmp_path / "state"
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/workspace_lifecycle.py"), "audit", "--repo", str(repo), "--state-dir", str(state)],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(Path(payload["repo_root"]).resolve(), repo.resolve())
            self.assertFalse(payload["mutated"])
            self.assertFalse(state.exists(), "read-only audit must not create a state directory")

    def test_cli_queue_is_read_only_and_lists_pending_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            repo = make_repo(tmp_path)
            state = tmp_path / "state"
            lifecycle.claim(repo, task_id="task-1", state_dir=state)
            (repo / "README.md").write_text("dirty\n", encoding="utf-8")
            lifecycle.record_pending_closure(repo, task_id="task-1", state_dir=state)
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/workspace_lifecycle.py"), "queue", "--state-dir", str(state)],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)
            self.assertFalse(payload["mutated"])
            self.assertEqual(len(payload["items"]), 1)

    def test_audit_reports_registered_but_missing_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            repo = make_repo(tmp_path)
            missing = tmp_path / "missing-worktree"
            git(repo, "worktree", "add", "-q", "-b", "missing-fixture", str(missing))
            shutil.rmtree(missing)

            report = lifecycle.audit(repo, state_dir=tmp_path / "state")

            statuses = {item["status"] for item in report["worktrees"]}
            self.assertIn("missing_worktree", statuses)
            self.assertFalse(report["mutated"])

    def test_inspect_supports_detached_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory))
            git(repo, "checkout", "--detach", "-q", "HEAD")

            identity = lifecycle.discover_worktree(repo)

            self.assertEqual(identity["branch"], "detached")

    def test_missing_worktree_binding_is_reported_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            repo = make_repo(tmp_path)
            child = tmp_path / "child"
            state = tmp_path / "state"
            git(repo, "worktree", "add", "-q", "-b", "missing-binding-fixture", str(child))
            lifecycle.claim(child, task_id="task-child", state_dir=state)
            shutil.rmtree(child)

            report = lifecycle.audit(repo, state_dir=state)

            self.assertEqual(report["counts"].get("missing_worktree"), 1)
            self.assertEqual(len(report["stale_bindings"]), 1)
