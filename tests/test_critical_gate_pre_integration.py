from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "hooks" / "critical_action_gate.py"
MERGE_GUARD = ROOT / "skills" / "merge-safety" / "scripts" / "merge_guard.py"


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise AssertionError(f"command failed: {shlex.join(cmd)}; stdout={proc.stdout}; stderr={proc.stderr}")
    return proc.stdout.strip()


def _run_gate(command: str, cwd: Path) -> dict[str, object]:
    payload = {
        "cwd": str(cwd),
        "tool_name": "Bash",
        "tool_input": {"cmd": command},
    }
    proc = subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps(payload),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise AssertionError(f"gate exited non-zero: {proc.returncode}; stdout={proc.stdout}; stderr={proc.stderr}")
    output = proc.stdout.strip()
    if not output:
        return {"decision": "allow", "silent": True}
    parsed = json.loads(output)
    hook_output = parsed.get("hookSpecificOutput")
    if isinstance(hook_output, dict) and hook_output.get("permissionDecision") == "deny":
        return {"decision": "block", "reason": str(hook_output.get("permissionDecisionReason") or ""), "raw": parsed}
    return parsed


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _prepare_checked_pre_result(root: Path) -> Path:
    remote = root / "origin.git"
    repo = root / "repo"
    _run(["git", "init", "--bare", str(remote)])
    _run(["git", "init", str(repo)])
    _run(["git", "config", "user.email", "tests@example.invalid"], repo)
    _run(["git", "config", "user.name", "Tests"], repo)
    _run(["git", "remote", "add", "origin", str(remote)], repo)

    _write(repo / "VERSION", "workflow-lab:release-002\n")
    _run(["git", "add", "VERSION"], repo)
    _run(["git", "commit", "-m", "seed release-002"], repo)
    _run(["git", "branch", "-m", "master"], repo)
    _run(["git", "push", "origin", "master"], repo)
    _run(["git", "checkout", "-b", "pre"], repo)
    _run(["git", "push", "origin", "pre"], repo)

    _run(["git", "checkout", "-b", "feature/release-003", "master"], repo)
    _write(repo / "VERSION", "workflow-lab:release-003\n")
    _run(["git", "commit", "-am", "set release-003"], repo)

    _run(["git", "checkout", "-b", "integration/pre-release-003", "origin/pre"], repo)
    _run(
        [
            sys.executable,
            str(MERGE_GUARD),
            "init",
            "--cwd",
            str(repo),
            "--goal",
            "Only VERSION release-003 to shared pre",
            "--target-ref",
            "origin/pre",
            "--source-ref",
            "feature/release-003",
            "--merge-mode",
            "merge --no-ff",
            "--allowed-paths",
            "VERSION",
            "--confirm-unknowns-clear",
        ]
    )
    _run([sys.executable, str(MERGE_GUARD), "check-pre", "--cwd", str(repo)])
    _run(["git", "merge", "--no-ff", "feature/release-003", "-m", "integrate release-003"], repo)
    _run(
        [
            sys.executable,
            str(MERGE_GUARD),
            "record-evidence",
            "--cwd",
            str(repo),
            "--test",
            "version test passed",
            "--review",
            "Approved. Independent review found no blocking issues.",
            "--review-status",
            "approved",
            "--p1-now",
            " ",
            "--subagents-closed",
            "--subagents-summary",
            "closed",
        ]
    )
    _run([sys.executable, str(MERGE_GUARD), "check-post", "--cwd", str(repo)])
    return repo


def _merge_state_path(repo: Path) -> Path:
    return repo / ".codex-local" / "merge-safety" / "current.json"


class CriticalGatePreIntegrationTests(unittest.TestCase):
    def test_lineage_result_label_is_not_a_command_spelling_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = _prepare_checked_pre_result(Path(temp_dir))
            _run(["git", "branch", "checked-result", "HEAD"], repo)
            path = repo / ".codex-local" / "merge-safety" / "current.json"
            state = json.loads(path.read_text())
            state["last_post_check"]["lineage"]["result_ref"] = "checked-result"
            path.write_text(json.dumps(state))
            result = _run_gate("git push origin checked-result:refs/heads/pre", repo)
            self.assertEqual(result.get("decision"), "allow", result)

    def test_pre_push_allows_checked_integration_head_from_real_merge_guard_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = _prepare_checked_pre_result(Path(temp_dir))

            result = _run_gate(f"git -C {shlex.quote(str(repo))} push origin HEAD:refs/heads/pre", repo)

            self.assertEqual(result.get("decision"), "allow", result)
            self.assertTrue(result.get("silent"), result)

    def test_pre_push_allows_any_source_expression_resolving_to_checked_integration_head(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = _prepare_checked_pre_result(Path(temp_dir))
            checked_head = _run(["git", "rev-parse", "HEAD"], repo)
            _run(["git", "branch", "checked-result", checked_head], repo)

            for source in ("HEAD", checked_head, "checked-result"):
                with self.subTest(source=source):
                    result = _run_gate(f"git -C {shlex.quote(str(repo))} push origin {source}:refs/heads/pre", repo)
                    self.assertEqual(result.get("decision"), "allow", result)
                    self.assertTrue(result.get("silent"), result)

    def test_pre_push_rejects_expired_head_after_check_post(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = _prepare_checked_pre_result(Path(temp_dir))
            _write(repo / "VERSION", "workflow-lab:release-004\n")
            _run(["git", "commit", "-am", "move head after postcheck"], repo)

            result = _run_gate(f"git -C {shlex.quote(str(repo))} push origin HEAD:refs/heads/pre", repo)

            self.assertEqual(result.get("decision"), "block", result)

    def test_pre_push_rejects_source_expression_resolving_to_different_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = _prepare_checked_pre_result(Path(temp_dir))

            result = _run_gate(f"git -C {shlex.quote(str(repo))} push origin feature/release-003:refs/heads/pre", repo)

            self.assertEqual(result.get("decision"), "block", result)

    def test_pre_push_rejects_missing_required_lineage_fields(self) -> None:
        cases = ("result_oid", "required_ref")
        for missing_key in cases:
            with self.subTest(missing_key=missing_key):
                with tempfile.TemporaryDirectory() as temp_dir:
                    repo = _prepare_checked_pre_result(Path(temp_dir))
                    state_path = _merge_state_path(repo)
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    source_in_result = state["last_post_check"]["lineage"]["source_in_result"]
                    source_in_result.pop(missing_key, None)
                    state_path.write_text(json.dumps(state), encoding="utf-8")

                    result = _run_gate(f"git -C {shlex.quote(str(repo))} push origin HEAD:refs/heads/pre", repo)

                    self.assertEqual(result.get("decision"), "block", result)

    def test_master_push_does_not_use_pretest_checked_head_shortcut(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = _prepare_checked_pre_result(Path(temp_dir))

            result = _run_gate(f"git -C {shlex.quote(str(repo))} push origin HEAD:refs/heads/master", repo)

            self.assertEqual(result.get("decision"), "block", result)


if __name__ == "__main__":
    unittest.main()
