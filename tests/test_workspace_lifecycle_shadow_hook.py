from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "hooks"))

import workspace_lifecycle as lifecycle
import workspace_lifecycle_shadow as shadow


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "codex@example.test")
    git(repo, "config", "user.name", "Codex Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "base")
    return repo


def payload(repo: Path, event: str, task: str = "task-1") -> dict[str, str]:
    return {"hook_event_name": event, "cwd": str(repo), "session_id": task}


class WorkspaceLifecycleShadowHookTests(unittest.TestCase):
    def test_clean_session_claims_without_touching_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root)
            state = root / "state"

            result = shadow.handle(payload(repo, "SessionStart"), state_dir=state)

            self.assertEqual(result["action"], "claim")
            self.assertTrue(result["mutated"])
            self.assertEqual(result["claim"]["status"], "active")
            binding = lifecycle.load_binding(repo, state)
            self.assertIsNotNone(binding)
            self.assertEqual(binding["task_id"], "task-1")
            self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_dirty_session_only_warns_and_does_not_create_or_replace_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root)
            state = root / "state"
            (repo / "README.md").write_text("unowned change\n", encoding="utf-8")

            result = shadow.handle(payload(repo, "SessionStart"), state_dir=state)

            self.assertEqual(result["action"], "warn")
            self.assertFalse(result["mutated"])
            self.assertFalse(state.exists())

    def test_stop_clean_owned_task_records_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root)
            state = root / "state"
            shadow.handle(payload(repo, "SessionStart"), state_dir=state)

            (repo / "README.md").write_text("finished\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-qm", "finish")
            result = shadow.handle(payload(repo, "Stop"), state_dir=state)

            self.assertEqual(result["action"], "ready")
            self.assertTrue(result["mutated"])
            self.assertEqual(lifecycle.load_binding(repo, state)["state"], "ready")

    def test_terminal_clean_binding_can_be_replaced_by_next_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root)
            state = root / "state"
            shadow.handle(payload(repo, "SessionStart", "task-1"), state_dir=state)
            lifecycle.transition(repo, task_id="task-1", state="ready", state_dir=state)

            result = shadow.handle(payload(repo, "SessionStart", "task-2"), state_dir=state)

            self.assertEqual(result["action"], "claim")
            self.assertTrue(result["mutated"])
            binding = lifecycle.load_binding(repo, state)
            self.assertEqual(binding["task_id"], "task-2")
            self.assertEqual(binding["history"][0]["task_id"], "task-1")

    def test_active_other_task_is_warned_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root)
            state = root / "state"
            shadow.handle(payload(repo, "SessionStart", "task-1"), state_dir=state)
            (repo / "README.md").write_text("task one\n", encoding="utf-8")

            result = shadow.handle(payload(repo, "SessionStart", "task-2"), state_dir=state)

            self.assertEqual(result["action"], "warn")
            self.assertFalse(result["mutated"])
            self.assertEqual(lifecycle.load_binding(repo, state)["task_id"], "task-1")

    def test_cli_warning_emits_context_but_success_stays_silent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root)
            state = root / "state"
            (repo / "README.md").write_text("dirty\n", encoding="utf-8")
            command = [
                sys.executable,
                str(ROOT / "hooks" / "workspace_lifecycle_shadow.py"),
                "--state-dir",
                str(state),
            ]
            warning = subprocess.run(
                command,
                input=json.dumps(payload(repo, "SessionStart")),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Workspace lifecycle (shadow)", warning.stdout)

            clean_repo = make_repo(root / "clean")
            success = subprocess.run(
                command,
                input=json.dumps(payload(clean_repo, "SessionStart", "task-clean")),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(success.stdout, "")


if __name__ == "__main__":
    unittest.main()
