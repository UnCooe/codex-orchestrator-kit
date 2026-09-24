"""Exercise the separately installed personal Git guard in disposable repositories."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


GUARD = Path(os.environ.get("GIT_GUARD_UNDER_TEST", str(Path.home() / ".codex/git-guard/bin/git_guard.py")))


@unittest.skipUnless(GUARD.is_file(), "personal Git guard is not installed")
class NonprodPushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        spec = importlib.util.spec_from_file_location("personal_guard", GUARD)
        self.guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.guard)
        self.run_git("init", "-q")
        self.run_git("config", "user.name", "Fixture")
        self.run_git("config", "user.email", "fixture@example.invalid")
        self.run_git("config", "core.hooksPath", str(self.repo / "no-hooks"))
        self.run_git("checkout", "-qb", "chore/fixture")
        (self.repo / "VERSION").write_text("one\n")
        self.run_git("add", "VERSION")
        self.run_git("commit", "-qm", "one")
        self.old = self.run_git("rev-parse", "HEAD")
        (self.repo / "VERSION").write_text("two\n")
        self.run_git("commit", "-qam", "two")
        self.new = self.run_git("rev-parse", "HEAD")

    def tearDown(self):
        self.temp.cleanup()

    def run_git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()

    def push(self, target="pre", new=None, old=None, source="refs/heads/chore/fixture", env=None):
        row = f"{source} {new or self.new} refs/heads/{target} {old or self.old}\n"
        cwd = os.getcwd()
        try:
            os.chdir(self.repo)
            with patch.dict(os.environ, env or {}, clear=True), patch("sys.stdin", io.StringIO(row)), contextlib.redirect_stderr(io.StringIO()):
                return self.guard.pre_push()
        finally:
            os.chdir(cwd)

    def test_pre_test_fast_forward_needs_no_machine_approval(self):
        for target in ("pre", "test"):
            with self.subTest(target=target):
                self.assertEqual(self.push(target), 0)

    def test_tracking_ref_source_is_valid(self):
        self.assertEqual(self.push("test", source="refs/remotes/origin/pre"), 0)

    def test_temporary_integration_name_does_not_override_nonprod_commit_boundary(self):
        for source in ("refs/heads/integration/pre-release-003", "refs/heads/local-validation-result"):
            for target in ("pre", "test"):
                self.assertEqual(self.push(target, source=source), 0)
                self.assertNotEqual(self.push(target, source=source, new=self.old, old=self.new), 0)
            self.assertNotEqual(self.push("master", source=source), 0)
            self.assertNotEqual(self.push("feat/example", source=source), 0)

    def test_exact_commit_source_preserves_fast_forward_boundary(self):
        self.assertEqual(self.push("pre", source=self.new), 0)
        self.assertNotEqual(self.push("pre", source=self.old, new=self.old, old=self.new), 0)

    def test_new_nonprod_branch_is_allowed(self):
        self.assertEqual(self.push(old="0" * 40), 0)

    def test_non_fast_forward_still_denied_even_with_legacy_marker(self):
        self.assertNotEqual(self.push(new=self.old, old=self.new, env={"AISERVER_PRE_DEPLOY_AUTH": "1"}), 0)

    def test_protected_deletion_denied(self):
        self.assertNotEqual(self.push(new="0" * 40), 0)

    def test_unknown_remote_commit_denied(self):
        self.assertNotEqual(self.push(old="1" * 40), 0)

    def test_master_not_authorized_by_nonprod_marker(self):
        for target in ("master", "main"):
            self.assertNotEqual(self.push(target, env={"AISERVER_PRE_DEPLOY_AUTH": "1"}), 0)

    def test_existing_promotion_contract_preserved(self):
        self.assertEqual(self.push("master", source="refs/heads/integration/master/fixture", env={
            "COMPANY_GIT_GUARD_MASTER_PROMOTION": "1",
            "COMPANY_GIT_GUARD_MASTER_TARGET": "master",
            "COMPANY_GIT_GUARD_MASTER_SOURCE": "fix/fixture",
        }), 0)

    def test_promotion_wrapper_can_create_commit_on_ephemeral_branch(self):
        self.run_git("checkout", "-qb", "integration/master/fixture")
        cwd = os.getcwd()
        try:
            os.chdir(self.repo)
            with patch.dict(os.environ, {
                "COMPANY_GIT_GUARD_MASTER_PROMOTION": "1",
                "COMPANY_GIT_GUARD_MASTER_TARGET": "master",
                "COMPANY_GIT_GUARD_MASTER_SOURCE": "fix/fixture",
            }, clear=True), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(self.guard.pre_commit(), 0)
        finally:
            os.chdir(cwd)

    def test_custom_integration_keeps_existing_policy(self):
        self.run_git("config", "companyGitGuard.integrationBranches", "pre test staging-custom")
        self.assertNotEqual(self.push("staging-custom"), 0)
        self.assertEqual(self.push("staging-custom", env={"AISERVER_PRE_DEPLOY_AUTH": "1"}), 0)

    def test_feature_push_unchanged(self):
        self.assertEqual(self.push("chore/fixture"), 0)

    def test_branch_naming_and_commit_rules_preserved(self):
        self.assertNotEqual(self.push(source="refs/heads/codex/not-allowed"), 0)
        self.run_git("checkout", "-qb", "pre")
        cwd = os.getcwd()
        try:
            os.chdir(self.repo)
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(self.guard.pre_commit(), 0)
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
