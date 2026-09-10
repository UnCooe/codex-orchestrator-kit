from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts" / "verify_skill_install.py"


class VerifySkillInstallContractTests(unittest.TestCase):
    def test_verify_skill_install_accepts_exact_skill_tree_and_ignores_cache_backup_files(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "skills"
            shutil.copytree(ROOT / "skills", target)
            (target / "orchestrator" / "__pycache__").mkdir()
            (target / "orchestrator" / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")
            (target / "orchestrator" / ".bak.20260722").mkdir()
            (target / "orchestrator" / ".bak.20260722" / "SKILL.md").write_text("ignored\n", encoding="utf-8")
            (target / "orchestrator" / "SKILL.md.bak.20260722").write_text("ignored\n", encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, str(VERIFY), "--source", str(ROOT / "skills"), "--target", str(target)],
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_verify_skill_install_fails_on_sha256_mismatch(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "skills"
            shutil.copytree(ROOT / "skills", target)
            skill = target / "orchestrator" / "SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, str(VERIFY), "--source", str(ROOT / "skills"), "--target", str(target)],
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("SHA-256", proc.stderr + proc.stdout)

    def test_verify_skill_install_fails_on_missing_or_extra_live_files(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "skills"
            shutil.copytree(ROOT / "skills", target)
            (target / "orchestrator" / "agents" / "openai.yaml").unlink()
            (target / "orchestrator" / "obsolete.txt").write_text("obsolete\n", encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, str(VERIFY), "--source", str(ROOT / "skills"), "--target", str(target)],
                text=True,
                capture_output=True,
            )

            output = proc.stderr + proc.stdout
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("missing target file: orchestrator/agents/openai.yaml", output)
            self.assertIn("extra target file: orchestrator/obsolete.txt", output)

    def test_verify_skill_install_fails_closed_on_target_symlink_root(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            real_target = Path(temp_dir) / "real-skills"
            shutil.copytree(ROOT / "skills", real_target)
            symlink_target = Path(temp_dir) / "skills-link"
            symlink_target.symlink_to(real_target, target_is_directory=True)

            proc = subprocess.run(
                [sys.executable, str(VERIFY), "--source", str(ROOT / "skills"), "--target", str(symlink_target)],
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("symlink", (proc.stderr + proc.stdout).lower())

    def test_verify_skill_install_fails_closed_on_target_managed_dir_symlink(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "skills"
            shutil.copytree(ROOT / "skills", target)
            external = Path(temp_dir) / "external-orchestrator"
            shutil.move(str(target / "orchestrator"), str(external))
            (target / "orchestrator").symlink_to(external, target_is_directory=True)

            proc = subprocess.run(
                [sys.executable, str(VERIFY), "--source", str(ROOT / "skills"), "--target", str(target)],
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("symlink", (proc.stderr + proc.stdout).lower())

    def test_verify_skill_install_fails_closed_on_target_live_file_symlink(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "skills"
            shutil.copytree(ROOT / "skills", target)
            live_file = target / "orchestrator" / "SKILL.md"
            external_file = Path(temp_dir) / "external-skill.md"
            external_file.write_text(live_file.read_text(encoding="utf-8"), encoding="utf-8")
            live_file.unlink()
            live_file.symlink_to(external_file)

            proc = subprocess.run(
                [sys.executable, str(VERIFY), "--source", str(ROOT / "skills"), "--target", str(target)],
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("symlink", (proc.stderr + proc.stdout).lower())


if __name__ == "__main__":
    unittest.main()
