from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts" / "verify_skill_install.py"


class VerifySkillInstallAgentsContractTests(unittest.TestCase):
    def test_verify_skill_install_compares_exact_active_agent_toml_set_and_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            target_skills = temp_root / "skills"
            target_agents = temp_root / "agents"
            shutil.copytree(ROOT / "skills", target_skills)
            shutil.copytree(ROOT / "agents", target_agents)

            (target_agents / "researcher.toml").write_text(
                (target_agents / "researcher.toml").read_text(encoding="utf-8") + "\n# tampered\n",
                encoding="utf-8",
            )
            (target_agents / "reviewer.toml").unlink()
            (target_agents / "user-owned.toml").write_text("name = \"user_owned\"\n", encoding="utf-8")
            (target_agents / "ignored.toml.bak.20260727").write_text("name = \"ignored\"\n", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(VERIFY),
                    "--source",
                    str(ROOT / "skills"),
                    "--target",
                    str(target_skills),
                    "--source-agents",
                    str(ROOT / "agents"),
                    "--target-agents",
                    str(target_agents),
                ],
                text=True,
                capture_output=True,
            )

            output = proc.stderr + proc.stdout
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("missing target agent: reviewer.toml", output)
            self.assertIn("SHA-256 mismatch: researcher.toml", output)
            self.assertNotIn("user-owned.toml", output)
            self.assertNotIn("ignored.toml.bak.20260727", output)

    def test_installers_run_agent_parity_only_after_agents_are_installed(self) -> None:
        shell_text = (ROOT / "install.sh").read_text(encoding="utf-8")
        ps_text = (ROOT / "install.ps1").read_text(encoding="utf-8")

        shell_verify = shell_text.index("\nverify_skill_install\n")
        shell_skills_only_exit = shell_text.index('if [[ "$SKILLS_ONLY" == "1" ]]')
        shell_agents_copy = shell_text.index('copy_sources "$SCRIPT_DIR/agents" "$CODEX_TARGET/agents"')
        self.assertGreater(shell_verify, shell_agents_copy)
        self.assertGreater(shell_verify, shell_skills_only_exit)
        self.assertIn("--source-agents", shell_text)
        self.assertIn("--target-agents", shell_text)

        ps_verify = ps_text.index("\nInvoke-SkillInstallVerifier ")
        ps_skills_only_exit = ps_text.index("if ($SkillsOnly.IsPresent)")
        ps_agents_copy = ps_text.index("Stage-Files -Source (Join-Path $scriptRoot 'agents')")
        self.assertGreater(ps_verify, ps_agents_copy)
        self.assertGreater(ps_verify, ps_skills_only_exit)
        self.assertIn("--source-agents", ps_text)
        self.assertIn("--target-agents", ps_text)


if __name__ == "__main__":
    unittest.main()
