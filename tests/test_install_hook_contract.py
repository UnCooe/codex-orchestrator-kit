from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KIT_MANAGED_HOOK_SCRIPTS = (
    "orchestrator_user_prompt.py",
    "orchestrator_pre_tool_use.py",
    "orchestrator_post_tool_use.py",
    "orchestrator_stop.py",
    "orchestrator_observability.py",
    "workflow_profile.py",
    "first_write_gate.py",
    "critical_action_gate.py",
    "pilot_receipt_gate.py",
)
PROJECT_HOOK_SCRIPT = "orchestrator_project_policy.py"


def _template_json() -> dict:
    return json.loads((ROOT / "templates" / "hooks.json.tpl").read_text(encoding="utf-8"))


def _commands_from_hooks_json(template: dict) -> list[tuple[str, str, str]]:
    commands: list[tuple[str, str, str]] = []
    for event, entries in template.get("hooks", {}).items():
        for entry in entries:
            matcher = str(entry.get("matcher", ""))
            for hook in entry.get("hooks", []):
                commands.append((event, matcher, str(hook.get("command", ""))))
    return commands


def _hook_script_names(entry: dict[str, object]) -> list[str]:
    names: list[str] = []
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return names
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        command = str(hook.get("command", ""))
        if "critical_action_gate.py" in command:
            names.append("critical_action_gate.py")
        if "pilot_receipt_gate.py" in command:
            names.append("pilot_receipt_gate.py")
    return names


def _assert_default_critical_gate_only(testcase: unittest.TestCase, entries: list[dict[str, object]]) -> None:
    by_matcher = {str(entry.get("matcher")): _hook_script_names(entry) for entry in entries}
    testcase.assertEqual(set(by_matcher), {"^(Bash|exec_command)$"})
    testcase.assertEqual(by_matcher["^(Bash|exec_command)$"], ["critical_action_gate.py"])
    testcase.assertNotIn("*", by_matcher)


def _hook_command(script: str) -> str:
    return f'python "/opt/project/hooks/{script}"'


def _hooks_json_fixture() -> dict[str, object]:
    hooks = [
        {"type": "command", "command": _hook_command(script)}
        for script in (*KIT_MANAGED_HOOK_SCRIPTS, PROJECT_HOOK_SCRIPT)
    ]
    return {"hooks": {"PreToolUse": [{"matcher": "^Bash$", "hooks": hooks}]}}


def _config_toml_fixture() -> str:
    blocks = []
    for script in (*KIT_MANAGED_HOOK_SCRIPTS, PROJECT_HOOK_SCRIPT):
        blocks.append(
            "\n".join(
                (
                    "[[hooks.PreToolUse.hooks]]",
                    'type = "command"',
                    f"command = '{_hook_command(script)}'",
                )
            )
        )
    return "\n".join(("[[hooks.PreToolUse]]", 'matcher = "^Bash$"', "", "\n\n".join(blocks), ""))


def _run_bash(script: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / script), *args],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        env=env,
    )


def _install_args(root: Path, hook_mode: str) -> tuple[str, ...]:
    return (
        "--apply",
        "--hook-mode",
        hook_mode,
        "--skills-mode",
        "codex",
        "--codex-home",
        str(root / "codex"),
        "--agents-home",
        str(root / "agents"),
    )


def _uninstall_args(root: Path, hook_mode: str) -> tuple[str, ...]:
    return (
        "--hook-mode",
        hook_mode,
        "--skills-mode",
        "codex",
        "--codex-home",
        str(root / "codex"),
        "--agents-home",
        str(root / "agents"),
    )


def _file_tree_snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


class InstallHookContractTests(unittest.TestCase):
    def test_hooks_json_template_installs_only_critical_gate_by_default(self) -> None:
        template = _template_json()

        _assert_default_critical_gate_only(self, template["hooks"]["PreToolUse"])
        rendered = json.dumps(template)
        self.assertEqual(rendered.count("critical_action_gate.py"), 1)
        self.assertNotIn("pilot_receipt_gate.py", rendered)

        for forbidden in (
            "orchestrator_pre_tool_use.py",
            "orchestrator_post_tool_use.py",
            "orchestrator_stop.py",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_config_toml_template_installs_only_critical_gate_by_default(self) -> None:
        text = (ROOT / "templates" / "config.toml_hooks.tpl").read_text(encoding="utf-8")
        parsed = tomllib.loads(text)

        self.assertEqual(text.count("[[hooks.PreToolUse]]"), 1)
        self.assertRegex(
            text,
            r"(?s)\[\[hooks\.PreToolUse\]\].*?matcher\s*=\s*\"\^\(Bash\|exec_command\)\$\"",
        )
        self.assertNotIn('matcher = "^apply_patch$"', text)
        _assert_default_critical_gate_only(self, parsed["hooks"]["PreToolUse"])
        self.assertEqual(text.count("critical_action_gate.py"), 1)
        self.assertNotIn("pilot_receipt_gate.py", text)
        for forbidden in (
            "orchestrator_pre_tool_use.py",
            "orchestrator_post_tool_use.py",
            "orchestrator_stop.py",
        ):
            self.assertNotIn(forbidden, text)

    def test_bash_installer_dry_run_skills_only_does_not_plan_hooks_or_agents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            proc = subprocess.run(
                [
                    "bash",
                    str(ROOT / "install.sh"),
                    "--dry-run",
                    "--skills-only",
                    "--codex-home",
                    str(root / "codex"),
                    "--agents-home",
                    str(root / "agents"),
                ],
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertIn("skills_only=true", proc.stdout)
            self.assertNotIn("/hooks/", proc.stdout)
            self.assertNotIn("hooks:", proc.stdout)
            self.assertNotIn(" -> " + str(root / "codex" / "agents"), proc.stdout)
            self.assertNotIn("critical_action_gate.py", proc.stdout)

    def test_installers_name_only_critical_as_default_pretool_script(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bash = _run_bash(
                "install.sh",
                "--dry-run",
                "--hook-mode",
                "hooks_json",
                "--skills-mode",
                "codex",
                "--codex-home",
                str(root / "bash-codex"),
                "--agents-home",
                str(root / "bash-agents"),
            )
            self.assertEqual(bash.returncode, 0, bash.stderr + bash.stdout)
            bash_pretool = sorted(
                set(
                    re.findall(
                        r"([A-Za-z0-9_]*pre_tool_use\.py|critical_action_gate\.py|pilot_receipt_gate\.py)",
                        bash.stdout,
                    )
                )
            )
            self.assertEqual(bash_pretool, ["critical_action_gate.py"])

            powershell = shutil.which("pwsh") or shutil.which("powershell")
            if powershell:
                ps = subprocess.run(
                    [
                        powershell,
                        "-NoProfile",
                        "-File",
                        str(ROOT / "install.ps1"),
                        "-DryRun",
                        "-HookMode",
                        "hooks_json",
                        "-SkillsMode",
                        "codex",
                        "-CodexHome",
                        str(root / "ps-codex"),
                        "-AgentsHome",
                        str(root / "ps-agents"),
                    ],
                    cwd=str(ROOT),
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(ps.returncode, 0, ps.stderr + ps.stdout)
                ps_pretool = sorted(
                    set(
                        re.findall(
                            r"([A-Za-z0-9_]*pre_tool_use\.py|critical_action_gate\.py|pilot_receipt_gate\.py)",
                            ps.stdout,
                        )
                    )
                )
                self.assertEqual(ps_pretool, ["critical_action_gate.py"])
            else:
                ps_text = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")
                assignment = re.search(r"\$managedPreToolScripts\s*=\s*@\(([^)]+)\)", ps_text)
                self.assertIsNotNone(assignment)
                self.assertIn("critical_action_gate.py", assignment.group(1))
                self.assertNotIn("pilot_receipt_gate.py", assignment.group(1))
                main = ps_text[ps_text.index("$skillTargets = @(") :]
                self.assertIn("$managedPreToolScripts", main)
                self.assertNotIn("orchestrator_pre_tool_use.py", main)

    def test_managed_hook_detection_uses_exact_kit_owned_names(self) -> None:
        for script in ("install.sh", "install.ps1", "uninstall.sh", "uninstall.ps1"):
            with self.subTest(script=script):
                text = (ROOT / script).read_text(encoding="utf-8-sig")
                self.assertNotIn(
                    "orchestrator_[^",
                    text,
                    f"{script} must not treat every orchestrator_*.py file as kit-owned",
                )
                self.assertIn("critical_action_gate.py", text)
                self.assertIn("pilot_receipt_gate.py", text)

    def test_bash_install_preserves_project_hook_on_both_hook_surfaces(self) -> None:
        for hook_mode, surface_name in (("hooks_json", "hooks.json"), ("config_toml", "config.toml")):
            with self.subTest(hook_mode=hook_mode), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                codex_home = root / "codex"
                codex_home.mkdir(parents=True)
                surface = codex_home / surface_name
                if hook_mode == "hooks_json":
                    surface.write_text(json.dumps(_hooks_json_fixture(), indent=2), encoding="utf-8")
                else:
                    surface.write_text(_config_toml_fixture(), encoding="utf-8")

                proc = _run_bash("install.sh", *_install_args(root, hook_mode))

                self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
                installed = surface.read_text(encoding="utf-8")
                self.assertIn(PROJECT_HOOK_SCRIPT, installed)
                for legacy in KIT_MANAGED_HOOK_SCRIPTS[:-2]:
                    self.assertNotIn(legacy, installed)
                self.assertEqual(installed.count("critical_action_gate.py"), 1)
                self.assertNotIn("pilot_receipt_gate.py", installed)

    def test_bash_uninstall_preserves_project_hook_on_both_hook_surfaces(self) -> None:
        for hook_mode, surface_name in (("hooks_json", "hooks.json"), ("config_toml", "config.toml")):
            with self.subTest(hook_mode=hook_mode), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                codex_home = root / "codex"
                codex_home.mkdir(parents=True)
                surface = codex_home / surface_name
                if hook_mode == "hooks_json":
                    surface.write_text(json.dumps(_hooks_json_fixture(), indent=2), encoding="utf-8")
                else:
                    surface.write_text(_config_toml_fixture(), encoding="utf-8")

                proc = _run_bash("uninstall.sh", *_uninstall_args(root, hook_mode))

                self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
                self.assertTrue(surface.is_file(), proc.stdout)
                installed = surface.read_text(encoding="utf-8")
                self.assertIn(PROJECT_HOOK_SCRIPT, installed)
                for managed in KIT_MANAGED_HOOK_SCRIPTS:
                    self.assertNotIn(managed, installed)

    def test_bash_full_install_copies_only_critical_hook_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            proc = _run_bash("install.sh", *_install_args(root, "hooks_json"))

            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            installed_hooks = {
                path.name
                for path in (root / "codex" / "hooks").glob("*.py")
                if path.is_file()
            }
            self.assertEqual(installed_hooks, {"critical_action_gate.py"})

    def test_bash_skill_verification_failure_happens_before_runtime_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_python = root / "python-with-failing-skill-verifier"
            fake_python.write_text(
                "\n".join(
                    (
                        "#!/usr/bin/env bash",
                        'for arg in "$@"; do',
                        '  if [[ "$arg" == *verify_skill_install.py ]]; then exit 42; fi',
                        "done",
                        f'exec {shlex.quote(sys.executable)} "$@"',
                        "",
                    )
                ),
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            env = os.environ.copy()
            env["CODEX_ORCHESTRATOR_PYTHON"] = str(fake_python)

            proc = _run_bash("install.sh", *_install_args(root, "hooks_json"), env=env)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            hooks = root / "codex" / "hooks"
            agents = root / "codex" / "agents"
            self.assertFalse(hooks.exists() and any(path.is_file() for path in hooks.rglob("*")))
            self.assertFalse(agents.exists() and any(path.is_file() for path in agents.rglob("*")))
            self.assertFalse((root / "codex" / "hooks.json").exists())

    def test_powershell_full_install_verifies_agent_parity_after_agent_staging_before_hooks(self) -> None:
        text = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")
        main = text[text.index("$skillTargets = @(") :]
        skills_only_exit = main.index("if ($SkillsOnly.IsPresent)")
        agent_stage = main.index("Stage-Files -Source (Join-Path $scriptRoot 'agents')")
        deprecated_removal = main.index("Remove-DeprecatedAgent -Name 'codebase-research-explorer.toml'")
        verifier = main.index("Invoke-SkillInstallVerifier -PythonCommand")
        hook_stage = main.index("-Destination (Join-Path (Join-Path $CodexHome 'hooks')")
        hook_merge = min(main.index("Merge-Hooks"), main.index("Merge-ConfigTomlHooks"))

        self.assertRegex(
            main[:skills_only_exit],
            r"(?s)foreach \(\$target in \$skillTargets\)\s*\{[^{}]*Stage-Files",
            "PowerShell skills-only path must remain isolated to managed skill staging",
        )
        self.assertLess(skills_only_exit, agent_stage)
        self.assertLess(agent_stage, deprecated_removal)
        self.assertLess(deprecated_removal, verifier)
        self.assertLess(verifier, hook_stage)
        self.assertLess(verifier, hook_merge)

    def test_bash_apply_fails_closed_on_skill_symlink_targets_before_runtime_writes(self) -> None:
        scenarios = ("skills-root", "managed-dir", "managed-file")
        modes = ("skills-only", "full")
        for scenario in scenarios:
            for mode in modes:
                with self.subTest(scenario=scenario, mode=mode), tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    codex_home = root / "codex"
                    agents_home = root / "agents"
                    external = root / "external-target"
                    external.mkdir(parents=True)
                    sentinel = external / "sentinel.txt"
                    sentinel.write_text("external sentinel\n", encoding="utf-8")

                    skills_root = codex_home / "skills"
                    if scenario == "skills-root":
                        codex_home.mkdir(parents=True)
                        skills_root.symlink_to(external, target_is_directory=True)
                    else:
                        skills_root.mkdir(parents=True)
                        if scenario == "managed-dir":
                            (skills_root / "orchestrator").symlink_to(external, target_is_directory=True)
                        else:
                            managed = skills_root / "orchestrator"
                            managed.mkdir()
                            external_file = external / "SKILL.md"
                            external_file.write_text("external skill body\n", encoding="utf-8")
                            (managed / "SKILL.md").symlink_to(external_file)

                    args: tuple[str, ...]
                    if mode == "skills-only":
                        args = (
                            "--apply",
                            "--skills-only",
                            "--skills-mode",
                            "codex",
                            "--codex-home",
                            str(codex_home),
                            "--agents-home",
                            str(agents_home),
                        )
                    else:
                        args = (
                            "--apply",
                            "--hook-mode",
                            "hooks_json",
                            "--skills-mode",
                            "codex",
                            "--codex-home",
                            str(codex_home),
                            "--agents-home",
                            str(agents_home),
                        )

                    proc = _run_bash("install.sh", *args)

                    self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertEqual(sentinel.read_text(encoding="utf-8"), "external sentinel\n")
                    external_files = sorted(
                        path.relative_to(external).as_posix()
                        for path in external.rglob("*")
                        if path.is_file() or path.is_symlink()
                    )
                    if scenario == "managed-file":
                        self.assertEqual(external_files, ["SKILL.md", "sentinel.txt"])
                        self.assertEqual((external / "SKILL.md").read_text(encoding="utf-8"), "external skill body\n")
                    else:
                        self.assertEqual(external_files, ["sentinel.txt"])
                    self.assertFalse((codex_home / "hooks").exists(), proc.stdout + proc.stderr)
                    self.assertFalse((codex_home / "agents").exists(), proc.stdout + proc.stderr)
                    self.assertFalse((codex_home / "hooks.json").exists(), proc.stdout + proc.stderr)
                    self.assertFalse((codex_home / "config.toml").exists(), proc.stdout + proc.stderr)

    def test_bash_uninstall_fails_closed_on_managed_skill_symlink_targets(self) -> None:
        for scenario in ("managed-dir", "managed-file"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                codex_home = root / "codex"
                agents_home = root / "agents"
                skills_root = codex_home / "skills"
                skills_root.mkdir(parents=True)
                external = root / "external-target"
                external.mkdir()
                sentinel = external / "sentinel.txt"
                sentinel.write_text("external sentinel\n", encoding="utf-8")

                if scenario == "managed-dir":
                    (skills_root / "orchestrator").symlink_to(external, target_is_directory=True)
                else:
                    managed = skills_root / "orchestrator"
                    managed.mkdir()
                    external_file = external / "SKILL.md"
                    external_file.write_text("external skill body\n", encoding="utf-8")
                    (managed / "SKILL.md").symlink_to(external_file)

                proc = _run_bash(
                    "uninstall.sh",
                    "--skills-only",
                    "--skills-mode",
                    "codex",
                    "--codex-home",
                    str(codex_home),
                    "--agents-home",
                    str(agents_home),
                )

                self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "external sentinel\n")
                if scenario == "managed-dir":
                    self.assertTrue((skills_root / "orchestrator").is_symlink())
                else:
                    self.assertTrue((skills_root / "orchestrator" / "SKILL.md").is_symlink())
                    self.assertEqual((external / "SKILL.md").read_text(encoding="utf-8"), "external skill body\n")

    def test_bash_full_install_fails_closed_on_hook_and_surface_symlinks(self) -> None:
        scenarios = (
            ("hooks-parent", "hooks_json", None),
            ("hooks-json", "hooks_json", "hooks.json"),
            ("config-toml", "config_toml", "config.toml"),
        )
        for scenario, hook_mode, surface_name in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                codex_home = root / "codex"
                codex_home.mkdir(parents=True)
                external = root / "external-target"
                external.mkdir()
                (external / "sentinel.txt").write_text("external sentinel\n", encoding="utf-8")

                if scenario == "hooks-parent":
                    external_hook = external / "critical_action_gate.py"
                    external_hook.write_text("external hook body\n", encoding="utf-8")
                    protected_link = codex_home / "hooks"
                    protected_link.symlink_to(external, target_is_directory=True)
                else:
                    external_surface = external / str(surface_name)
                    if hook_mode == "hooks_json":
                        external_surface.write_text(json.dumps(_hooks_json_fixture(), indent=2), encoding="utf-8")
                    else:
                        external_surface.write_text(_config_toml_fixture(), encoding="utf-8")
                    protected_link = codex_home / str(surface_name)
                    protected_link.symlink_to(external_surface)

                before = _file_tree_snapshot(external)
                proc = _run_bash("install.sh", *_install_args(root, hook_mode))

                self.assertEqual(
                    _file_tree_snapshot(external),
                    before,
                    f"install followed {scenario} symlink (returncode={proc.returncode})",
                )
                self.assertTrue(protected_link.is_symlink(), proc.stdout + proc.stderr)
                self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_bash_full_uninstall_fails_closed_on_hook_and_surface_symlinks(self) -> None:
        scenarios = (
            ("hooks-parent", "hooks_json", None),
            ("hooks-json", "hooks_json", "hooks.json"),
            ("config-toml", "config_toml", "config.toml"),
        )
        for scenario, hook_mode, surface_name in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                codex_home = root / "codex"
                codex_home.mkdir(parents=True)
                external = root / "external-target"
                external.mkdir()
                (external / "sentinel.txt").write_text("external sentinel\n", encoding="utf-8")

                if scenario == "hooks-parent":
                    external_hook = external / "orchestrator_pre_tool_use.py"
                    external_hook.write_text("external hook body\n", encoding="utf-8")
                    protected_link = codex_home / "hooks"
                    protected_link.symlink_to(external, target_is_directory=True)
                else:
                    external_surface = external / str(surface_name)
                    if hook_mode == "hooks_json":
                        external_surface.write_text(json.dumps(_hooks_json_fixture(), indent=2), encoding="utf-8")
                    else:
                        external_surface.write_text(_config_toml_fixture(), encoding="utf-8")
                    protected_link = codex_home / str(surface_name)
                    protected_link.symlink_to(external_surface)

                before = _file_tree_snapshot(external)
                proc = _run_bash("uninstall.sh", *_uninstall_args(root, hook_mode))

                self.assertEqual(
                    _file_tree_snapshot(external),
                    before,
                    f"uninstall followed {scenario} symlink (returncode={proc.returncode})",
                )
                self.assertTrue(protected_link.is_symlink(), proc.stdout + proc.stderr)
                self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_powershell_install_guards_exact_skill_and_stage_parent_paths_before_writes(self) -> None:
        text = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")
        main = text[text.index("$skillTargets = @(") :]
        guard_name = r"Assert-[A-Za-z0-9-]*Reparse[A-Za-z0-9-]*"

        skills_parent_guard = re.search(
            guard_name + r"\s+-Path\s+\(Join-Path\s+\$SkillsRoot\s+['\"]skills['\"]\)",
            main,
        )
        self.assertIsNotNone(skills_parent_guard, "PowerShell must guard the exact skills parent root")
        skill_stage_loop = main.index("foreach ($target in $skillTargets)")
        self.assertLess(skills_parent_guard.start(), skill_stage_loop)

        stage_files = text[
            text.index("function Stage-Files") : text.index("function Test-IgnoredSkillRelativePath")
        ]
        dest_parent_guard = re.search(guard_name + r"\s+-Path\s+\$destDir\b", stage_files)
        recursive_parent_guard = re.search(guard_name + r"\s+-Path\s+\$parent\b", stage_files)
        self.assertIsNotNone(dest_parent_guard, "single-file staging must guard Destination's parent chain")
        self.assertIsNotNone(recursive_parent_guard, "recursive staging must guard each Destination parent chain")
        single_parent_write = stage_files.index("New-Item -ItemType Directory -Path $destDir")
        recursive_parent_write = stage_files.index("New-Item -ItemType Directory -Path $parent")
        self.assertLess(dest_parent_guard.start(), single_parent_write)
        self.assertLess(recursive_parent_guard.start(), recursive_parent_write)

    def test_powershell_install_and_uninstall_guard_exact_hook_paths_before_writes(self) -> None:
        guard_name = r"Assert-[A-Za-z0-9-]*Reparse[A-Za-z0-9-]*"
        install_text = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")
        install_main = install_text[install_text.index("$skillTargets = @(") :]

        install_hooks_parent_guard = re.search(
            guard_name + r"\s+-Path\s+\(Join-Path\s+\$CodexHome\s+['\"]hooks['\"]\)",
            install_main,
        )
        self.assertIsNotNone(install_hooks_parent_guard, "install must guard the exact hooks parent")
        hook_stage = install_main.index("-Destination (Join-Path (Join-Path $CodexHome 'hooks')")
        self.assertLess(install_hooks_parent_guard.start(), hook_stage)

        hooks_assignment = install_main.index("$hooksPath = Join-Path $CodexHome 'hooks.json'")
        hooks_surface_guard = re.search(guard_name + r"\s+-Path\s+\$hooksPath\b", install_main[hooks_assignment:])
        self.assertIsNotNone(hooks_surface_guard, "install must guard hooks.json explicitly")
        hooks_backup = install_main.index("Backup-Path -Path $hooksPath", hooks_assignment)
        self.assertLess(hooks_assignment + hooks_surface_guard.start(), hooks_backup)

        config_assignment = install_main.index("$configPath = Join-Path $CodexHome 'config.toml'")
        config_surface_guard = re.search(guard_name + r"\s+-Path\s+\$configPath\b", install_main[config_assignment:])
        self.assertIsNotNone(config_surface_guard, "install must guard config.toml explicitly")
        config_backup = install_main.index("Backup-Path -Path $configPath", config_assignment)
        self.assertLess(config_assignment + config_surface_guard.start(), config_backup)

        uninstall_text = (ROOT / "uninstall.ps1").read_text(encoding="utf-8-sig")
        uninstall_main = uninstall_text[uninstall_text.index("$CodexHome = Get-CodexHome") :]
        uninstall_hooks_parent_guard = re.search(
            guard_name + r"\s+-Path\s+\(Join-Path\s+\$CodexHome\s+['\"]hooks['\"]\)",
            uninstall_main,
        )
        self.assertIsNotNone(uninstall_hooks_parent_guard, "uninstall must guard the exact hooks parent")
        remove_loop = uninstall_main.index("foreach ($target in $removeTargets)")
        self.assertLess(uninstall_hooks_parent_guard.start(), remove_loop)

        uninstall_hooks_assignment = uninstall_main.index("$hooksPath = Join-Path $CodexHome 'hooks.json'")
        uninstall_hooks_guard = re.search(
            guard_name + r"\s+-Path\s+\$hooksPath\b",
            uninstall_main[uninstall_hooks_assignment:],
        )
        self.assertIsNotNone(uninstall_hooks_guard, "uninstall must guard hooks.json explicitly")
        uninstall_hooks_backup = uninstall_main.index("Backup-Path -Path $hooksPath", uninstall_hooks_assignment)
        self.assertLess(uninstall_hooks_assignment + uninstall_hooks_guard.start(), uninstall_hooks_backup)

        uninstall_config_assignment = uninstall_main.index("$configPath = Join-Path $CodexHome 'config.toml'")
        uninstall_config_guard = re.search(
            guard_name + r"\s+-Path\s+\$configPath\b",
            uninstall_main[uninstall_config_assignment:],
        )
        self.assertIsNotNone(uninstall_config_guard, "uninstall must guard config.toml explicitly")
        uninstall_config_backup = uninstall_main.index(
            "Backup-Path -Path $configPath",
            uninstall_config_assignment,
        )
        self.assertLess(uninstall_config_assignment + uninstall_config_guard.start(), uninstall_config_backup)

    def test_powershell_install_and_uninstall_guard_reparse_points_before_copy_or_remove(self) -> None:
        install_text = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")
        uninstall_text = (ROOT / "uninstall.ps1").read_text(encoding="utf-8-sig")

        self.assertRegex(install_text, r"(?i)ReparsePoint|SymbolicLink")
        first_copy = install_text.index("Copy-Item")
        first_remove = install_text.index("Remove-Item")
        first_mutation = min(first_copy, first_remove)
        first_guard_match = re.search(r"(?i)ReparsePoint|SymbolicLink", install_text)
        self.assertIsNotNone(first_guard_match)
        self.assertLess(first_guard_match.start(), first_mutation)

        self.assertRegex(uninstall_text, r"(?i)ReparsePoint|SymbolicLink")
        uninstall_first_remove = uninstall_text.index("Remove-Item")
        uninstall_guard_match = re.search(r"(?i)ReparsePoint|SymbolicLink", uninstall_text)
        self.assertIsNotNone(uninstall_guard_match)
        self.assertLess(uninstall_guard_match.start(), uninstall_first_remove)

    def test_powershell_does_not_recursively_copy_the_source_hooks_directory(self) -> None:
        text = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")

        self.assertNotRegex(
            text,
            r"Source\s*=\s*Join-Path\s+\$scriptRoot\s+['\"]hooks['\"]",
            "PowerShell full install must stage critical_action_gate.py, not the whole legacy hooks tree",
        )
        self.assertIn("critical_action_gate.py", text)


if __name__ == "__main__":
    unittest.main()
