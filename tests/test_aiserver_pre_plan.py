from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path("/Users/a123/Projects/company/aiserver/.codex/skills/aiserver-pre-verify/scripts/pre_plan.sh")


def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


class AiserverPrePlanTests(unittest.TestCase):
    def _make_repo(self, root: Path) -> Path:
        repo = root / "repo"
        remote = root / "remote.git"
        repo.mkdir()
        _run(["git", "init", "--bare", str(remote)], root)
        _run(["git", "init", "-b", "pre"], repo)
        _run(["git", "config", "user.email", "test@example.com"], repo)
        _run(["git", "config", "user.name", "Test User"], repo)
        (repo / "app.py").write_text("base\n", encoding="utf-8")
        _run(["git", "add", "app.py"], repo)
        _run(["git", "commit", "-m", "base"], repo)
        _run(["git", "remote", "add", "origin", str(remote)], repo)
        _run(["git", "push", "-u", "origin", "pre"], repo)
        _run(["git", "switch", "-c", "fix/demo"], repo)
        (repo / "app.py").write_text("base\nfix\n", encoding="utf-8")
        _run(["git", "commit", "-am", "fix demo"], repo)
        _run(["git", "push", "-u", "origin", "fix/demo"], repo)
        _run(["git", "fetch", "origin"], repo)
        return repo

    def test_quick_fast_pre_outputs_single_server_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._make_repo(Path(temp_dir))
            env = {**os.environ, "AISERVER_REPO": str(repo)}

            proc = _run(["bash", str(SCRIPT), "--quick", "fix/demo", "fast-pre"], repo, env=env)

            self.assertIn("lane=fast-pre", proc.stdout)
            self.assertIn("quick=1", proc.stdout)
            self.assertIn("writes_shared_branch=false", proc.stdout)
            self.assertIn("workflow_state.lane=fast_pre", proc.stdout)
            self.assertIn("authorized server command bundle:", proc.stdout)
            self.assertIn("rollback shape:", proc.stdout)
            self.assertNotIn("git push origin HEAD:pre", proc.stdout)

    def test_shared_lane_preserves_pre_merge_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._make_repo(Path(temp_dir))
            env = {**os.environ, "AISERVER_REPO": str(repo)}

            proc = _run(["bash", str(SCRIPT), "--quick", "fix/demo", "shared-lane"], repo, env=env)

            self.assertIn("lane=shared-lane", proc.stdout)
            self.assertIn("writes_shared_branch=true", proc.stdout)
            self.assertIn("git merge --no-ff", proc.stdout)
            self.assertIn("git push origin HEAD:pre", proc.stdout)

    def test_fast_hotfix_marks_shared_write_and_keeps_rollback_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._make_repo(Path(temp_dir))
            env = {**os.environ, "AISERVER_REPO": str(repo)}
            target_sha = _run(["git", "rev-parse", "HEAD"], repo).stdout.strip()

            proc = _run(["bash", str(SCRIPT), "--quick", target_sha, "fast-hotfix"], repo, env=env)

            self.assertIn("lane=fast-hotfix", proc.stdout)
            self.assertIn("writes_shared_branch=true", proc.stdout)
            self.assertIn(f"target_sha={target_sha}", proc.stdout)
            self.assertIn("authorized server command bundle:", proc.stdout)
            self.assertIn("rollback shape:", proc.stdout)

    def test_controlled_ops_requires_deploy_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._make_repo(Path(temp_dir))
            env = {**os.environ, "AISERVER_REPO": str(repo)}

            proc = _run(["bash", str(SCRIPT), "--quick", "fix/demo", "controlled-ops"], repo, env=env)

            self.assertIn("lane=controlled-ops", proc.stdout)
            self.assertIn("required: controlled-ops/shared-risk lane needs doc/deploy checklist", proc.stdout)

    def test_config_like_fast_path_requires_checklist_or_lane_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._make_repo(Path(temp_dir))
            env = {**os.environ, "AISERVER_REPO": str(repo)}
            (repo / "config.env.example").write_text("DEMO=1\n", encoding="utf-8")
            _run(["git", "add", "config.env.example"], repo)
            _run(["git", "commit", "-m", "touch config example"], repo)

            proc = _run(["bash", str(SCRIPT), "--quick", "HEAD", "fast-pre"], repo, env=env)

            self.assertIn("config.env.example", proc.stdout)
            self.assertIn("required: config-like diff detected; upgrade to controlled-ops", proc.stdout)

    def test_legacy_merge_alias_maps_to_shared_lane(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._make_repo(Path(temp_dir))
            env = {**os.environ, "AISERVER_REPO": str(repo)}

            proc = _run(["bash", str(SCRIPT), "--quick", "fix/demo", "merge-to-pre"], repo, env=env)

            self.assertIn("lane=shared-lane", proc.stdout)

    def test_invalid_lane_lists_allowed_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._make_repo(Path(temp_dir))
            env = {**os.environ, "AISERVER_REPO": str(repo)}

            proc = _run(["bash", str(SCRIPT), "--quick", "fix/demo", "bad-lane"], repo, env=env, check=False)

            self.assertEqual(proc.returncode, 2)
            self.assertIn("allowed: fast-pre, fast-hotfix, shared-lane, controlled-ops", proc.stderr)


if __name__ == "__main__":
    unittest.main()
