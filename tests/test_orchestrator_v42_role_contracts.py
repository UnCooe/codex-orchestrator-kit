from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / "agents"
ORCHESTRATOR_SKILL = ROOT / "skills" / "orchestrator" / "SKILL.md"
SUBAGENT_PROMPTS = ROOT / "skills" / "orchestrator" / "references" / "subagent-prompts.md"


def _agent_files() -> list[Path]:
    files = sorted(AGENTS_DIR.glob("*.toml"))
    if not files:
        raise AssertionError("expected managed agent toml files")
    return files


class OrchestratorV42RoleContractTests(unittest.TestCase):
    def test_agent_toml_is_optional_template_not_runtime_identity_guarantee(self) -> None:
        for path in _agent_files():
            with self.subTest(agent=path.name):
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                self.assertIn("name", data)
                self.assertIn("developer_instructions", data)
                self.assertIsInstance(data["developer_instructions"], str)

        orchestrator = ORCHESTRATOR_SKILL.read_text(encoding="utf-8").lower()
        self.assertIn("agents/*.toml", orchestrator)
        self.assertRegex(orchestrator, r"agents/\*\.toml[^.\n]*(?:optional|template|not.*runtime)")
        self.assertNotIn("parent selects the exact agent_type", orchestrator)
        self.assertNotIn("stable role contracts are owned", orchestrator)

    def test_subagent_prompts_are_runtime_role_contract_and_work_package_schema(self) -> None:
        text = SUBAGENT_PROMPTS.read_text(encoding="utf-8")
        for field in (
            "Role:",
            "Evidence lane:",
            "Objective:",
            "Evidence scope:",
            "Write boundary:",
            "Artifact:",
            "Done:",
            "Stop:",
            "No delegation:",
        ):
            self.assertIn(field, text)

        self.assertRegex(text.lower(), r"explicit.*work package")
        self.assertRegex(text.lower(), r"not.*guarantee.*runtime")
        self.assertRegex(text.lower(), r"consume, validate, or explicitly reject")
        self.assertNotIn("stable role contract lives", text.lower())

    def test_orchestrator_requires_explicit_role_contract_per_child_without_typed_injection_claim(self) -> None:
        text = ORCHESTRATOR_SKILL.read_text(encoding="utf-8")
        lower = text.lower()

        self.assertIn("explicit role contract", lower)
        self.assertIn("every work package", lower)
        self.assertIn("no delegation", lower)
        self.assertNotIn("agent_type", lower)
        self.assertNotIn("typed role", lower)
        self.assertRegex(
            lower,
            r"fork_turns=\"all\"[^.\n]*(?:must not|forbid|forbidden|never)|"
            r"(?:must not|forbid|forbidden|never)[^.\n]*fork_turns=\"all\"",
        )

    def test_v43_context_focus_budget_depth_and_reuse_policy_is_explicit(self) -> None:
        text = ORCHESTRATOR_SKILL.read_text(encoding="utf-8").lower()

        for value_reason in ("context isolation", "independent evidence", "specialist judgment", "separable boundary"):
            with self.subTest(value_reason=value_reason):
                self.assertIn(value_reason, text)
        self.assertRegex(text, r"local bounded work[^.\n]*(?:root-only|stay(?:s)? root-only)")
        self.assertRegex(text, r"one noisy or specialist lane[^.\n]*one child")
        self.assertRegex(text, r"two independent lanes[^.\n]*two")
        self.assertRegex(text, r"ordinary[^.\n]*one spawn wave")
        self.assertIn("one role+scope child per phase", text)
        self.assertIn("reuse", text)
        self.assertRegex(text, r"broad read-only[^.\n]*first wave[^.\n]*(?:1\s*-\s*3|1\s+to\s+3)")
        self.assertRegex(text, r"at most one[^.\n]*gap lane[^.\n]*after first-wave synthesis")
        self.assertIn("no duplicate evidence-lane owners", text)
        self.assertRegex(text, r"no task-wide total child cap|not a task-wide total child cap")
        self.assertRegex(text, r"(?:max(?:imum)?|at most) 4 active")
        self.assertRegex(text, r"default depth\s*1")
        self.assertRegex(text, r"nested delegation[^.\n]*(?:denied|off|disabled) by default")
        self.assertRegex(text, r"root-authorized[^.\n]*depth 2[^.\n]*exception")
        self.assertRegex(text, r"one writer[^.\n]*boundary")
        self.assertNotRegex(text, r"coordinator-only|spawn-relay")
        self.assertRegex(text, r"every child result[^.\n]*(?:consumed|validated|explicitly rejected)")
        self.assertRegex(text, r"fixed role roster[^.\n]*not[^.\n]*completion gate")
        self.assertRegex(text, r"spawn[^.\n]*token[^.\n]*api[^.\n]*reductions?[^.\n]*not[^.\n]*completion gates?")
        self.assertNotRegex(text, r"broad read-only[^.\n]*starts? with 2")
        self.assertNotRegex(text, r"task-tree[^.\n]*hard cap[^.\n]*4")


if __name__ == "__main__":
    unittest.main()
