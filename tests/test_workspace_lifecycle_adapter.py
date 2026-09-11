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
import workspace_lifecycle_adapter as adapter


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


def payload(repo: Path, event: str, task: str = "task-1", **extra: object) -> dict[str, object]:
    value: dict[str, object] = {"hook_event_name": event, "cwd": str(repo), "thread_id": task}
    value.update(extra)
    return value


class WorkspaceLifecycleAdapterTests(unittest.TestCase):
    def test_task_id_uses_explicit_fields_or_nested_session_meta_only(self) -> None:
        self.assertEqual(adapter.task_id_from_payload({"thread_id": "thread-1"}), "thread-1")
        self.assertEqual(adapter.task_id_from_payload({"session_meta": {"payload": {"id": "session-1"}}}, environ={}), "session-1")
        self.assertEqual(adapter.task_id_from_payload({"id": "untrusted"}, environ={}), "")
        self.assertEqual(adapter.task_id_from_payload({}, environ={"CODEX_THREAD_ID": "env-thread"}), "env-thread")

    def test_missing_task_id_is_warning_and_does_not_create_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory))
            state = Path(directory) / "state"
            result = adapter.handle_payload({"hook_event_name": "SessionStart", "cwd": str(repo)}, state_dir=state, environ={})

            self.assertEqual(result["status"], "unknown_task")
            self.assertEqual(result["decision"], "shadow_warn")
            self.assertFalse(result["mutated"])
            self.assertFalse(state.exists())

    def test_context_log_shape_without_identity_stays_unattributed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory))
            result = adapter.handle_payload({
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(repo),
                "root": str(repo),
                "branch": "feature/example",
            }, environ={})

            self.assertEqual(result["status"], "unknown_task")
            self.assertEqual(result["decision"], "shadow_warn")
            self.assertEqual(result["changed_paths"], [])

    def test_session_start_warns_on_unowned_dirty_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory))
            (repo / "README.md").write_text("unowned\n", encoding="utf-8")

            result = adapter.handle_payload(payload(repo, "SessionStart"))

            self.assertEqual(result["status"], "unowned_dirty")
            self.assertEqual(result["decision"], "shadow_warn")
            self.assertEqual(result["changed_paths"], ["README.md"])

    def test_read_only_shell_command_is_not_treated_as_a_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory))
            result = adapter.handle_payload(
                payload(repo, "PreToolUse", tool_name="Bash", tool_input={"cmd": "git status --short"})
            )

            self.assertFalse(result["write_candidate"])
            self.assertEqual(result["decision"], "allow")

    def test_pretool_shadow_and_enforce_distinguish_observation_from_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory))
            (repo / "README.md").write_text("unowned\n", encoding="utf-8")
            command = {"tool_name": "Bash", "tool_input": {"cmd": "printf x > README.md"}}

            shadow = adapter.handle_payload(payload(repo, "PreToolUse", **command), mode="shadow")
            enforce = adapter.handle_payload(payload(repo, "PreToolUse", **command), mode="enforce")

            self.assertTrue(shadow["write_candidate"])
            self.assertTrue(shadow["would_block"])
            self.assertEqual(shadow["decision"], "shadow_warn")
            self.assertEqual(enforce["decision"], "deny")
            self.assertFalse((Path(directory) / "state").exists())

    def test_same_active_task_is_allowed_and_other_task_is_block_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root)
            state = root / "state"
            lifecycle.claim(repo, task_id="task-1", state_dir=state)
            (repo / "README.md").write_text("owned\n", encoding="utf-8")
            command = {"tool_name": "Bash", "tool_input": {"cmd": "printf x > README.md"}}

            same = adapter.handle_payload(payload(repo, "PreToolUse", "task-1", **command), state_dir=state)
            other = adapter.handle_payload(payload(repo, "PreToolUse", "task-2", **command), state_dir=state)

            self.assertEqual(same["decision"], "allow")
            self.assertNotIn("would_block", same)
            self.assertTrue(other["would_block"])
            self.assertEqual(other["decision"], "shadow_warn")

    def test_stop_requires_explicit_transition_for_dirty_or_clean_owned_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root)
            state = root / "state"
            lifecycle.claim(repo, task_id="task-1", state_dir=state)
            (repo / "README.md").write_text("dirty\n", encoding="utf-8")
            dirty = adapter.handle_payload(payload(repo, "Stop"), state_dir=state)
            self.assertEqual(dirty["status"], "active_dirty")
            self.assertIn("handoff", dirty["message"])

            git(repo, "add", "README.md")
            git(repo, "commit", "-qm", "finish")
            clean = adapter.handle_payload(payload(repo, "Stop"), state_dir=state)
            self.assertEqual(clean["status"], "active_clean")
            self.assertIn("ready", clean["message"])

    def test_handoff_binding_requires_claim_before_same_task_can_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root)
            state = root / "state"
            lifecycle.claim(repo, task_id="task-1", state_dir=state)
            artifact = root / "handoff.txt"
            artifact.write_text("preserved\n", encoding="utf-8")
            lifecycle.transition(repo, task_id="task-1", state="handoff", artifact=str(artifact), state_dir=state)
            result = adapter.handle_payload(
                payload(repo, "PreToolUse", "task-1", tool_name="Bash", tool_input={"cmd": "printf x > README.md"}),
                state_dir=state,
            )

            self.assertEqual(result["status"], "handoff_clean")
            self.assertTrue(result["would_block"])
            self.assertTrue(result["requires_claim"])
            self.assertEqual(result["decision"], "shadow_warn")

    def test_unknown_event_and_non_git_path_fail_closed_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = adapter.handle_payload(payload(root, "Unexpected"))
            self.assertEqual(result["status"], "out_of_scope")
            self.assertEqual(result["decision"], "shadow_warn")
            self.assertFalse(result["mutated"])

    def test_cli_emits_json_without_state_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root)
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/workspace_lifecycle_adapter.py"), "--state-dir", str(root / "state")],
                input=json.dumps(payload(repo, "SessionStart")),
                text=True,
                capture_output=True,
                check=True,
            )
            result = json.loads(proc.stdout)
            self.assertEqual(result["status"], "clean")
            self.assertFalse(result["mutated"])
            self.assertFalse((root / "state").exists())


if __name__ == "__main__":
    unittest.main()
