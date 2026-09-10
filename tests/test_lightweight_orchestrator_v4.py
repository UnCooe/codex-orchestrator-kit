from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_SKILL = ROOT / "skills" / "orchestrator" / "SKILL.md"
FEATURE_WORKFLOW_SKILL = ROOT / "skills" / "feature-workflow" / "SKILL.md"
SUBAGENT_PROMPTS = ROOT / "skills" / "orchestrator" / "references" / "subagent-prompts.md"
GLOBAL_TEMPLATE = ROOT / "templates" / "global-agents.md"
WORKFLOW_PROFILE = ROOT / "hooks" / "workflow_profile.py"
HOOK_TEMPLATES = [
    ROOT / "templates" / "hooks.json.tpl",
    ROOT / "templates" / "config.toml_hooks.tpl",
]
RUNTIME_SMOKE = ROOT / "tests" / "runtime_skill_routing_smoke.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _feature_owner_text() -> str:
    return "\n".join((_text(FEATURE_WORKFLOW_SKILL), _text(SUBAGENT_PROMPTS)))


class LightweightOrchestratorV4PolicyContractTests(unittest.TestCase):
    def test_v43_context_focus_delegates_only_for_material_value(self) -> None:
        text = _text(ORCHESTRATOR_SKILL).lower()

        self.assertIn("outcome-based delegation", text)
        self.assertRegex(text, r"tiny_direct(?:\W+\w+){0,6}\W+0(?:\W+\w+){0,8}\W+active children")
        for reason in (
            "context isolation",
            "independent evidence",
            "specialist judgment",
            "separable boundary",
        ):
            with self.subTest(reason=reason):
                self.assertIn(reason, text)
        self.assertRegex(text, r"delegate only when|only delegate when|use a child only when")
        self.assertRegex(text, r"local bounded work(?:\W+\w+){0,12}\W+(?:root-only|stay(?:s)? root-only)")
        self.assertRegex(text, r"one noisy or specialist lane(?:\W+\w+){0,12}\W+one child")
        self.assertRegex(text, r"two independent lanes(?:\W+\w+){0,12}\W+two")
        self.assertNotRegex(text, r"required_roles\s*=\s*implementer,test_designer,test_runner,reviewer")
        self.assertNotRegex(text, r"required core roles(?:\W+\w+){0,16}\W+implementer")

    def test_v43_child_budget_is_wave_based_with_active_cap_not_task_total_cap(self) -> None:
        text = _text(ORCHESTRATOR_SKILL).lower()

        self.assertRegex(text, r"ordinary(?:\W+\w+){0,8}\W+one spawn wave")
        self.assertRegex(text, r"ordinary(?:\W+\w+){0,12}\W+reuse")
        self.assertRegex(text, r"broad read-only(?:\W+\w+){0,12}\W+first wave(?:\W+\w+){0,8}\W+1-3")
        self.assertRegex(text, r"named lanes")
        self.assertRegex(text, r"at most one(?:\W+\w+){0,8}\W+gap lane")
        self.assertRegex(text, r"after first-wave synthesis")
        self.assertRegex(text, r"max(?:imum)? 4 active|4 active")
        self.assertRegex(text, r"no task-wide total child cap|not a task-wide total child cap")
        self.assertNotRegex(text, r"broad read-only[^.\n]*starts? with 2")
        self.assertNotRegex(text, r"task-tree(?:\W+\w+){0,8}\W+hard cap(?:\W+\w+){0,8}\W+4")

    def test_v43_child_results_and_completion_gates_are_policy_explicit(self) -> None:
        text = _text(ORCHESTRATOR_SKILL).lower()

        self.assertRegex(text, r"no duplicate evidence-lane owners")
        self.assertRegex(text, r"every child result(?:\W+\w+){0,10}\W+(?:consumed|validated|explicitly rejected)")
        self.assertRegex(text, r"targeted source(?:\W+\w+){0,8}\W+acceptance")
        self.assertRegex(text, r"do not repeat(?:\W+\w+){0,8}\W+full scan")
        self.assertIn("fixed role roster", text)
        self.assertRegex(text, r"fixed role roster(?:\W+\w+){0,10}\W+not(?:\W+\w+){0,4}\W+completion gate")
        self.assertRegex(
            text,
            r"spawn(?:\W+\w+){0,4}\W+token(?:\W+\w+){0,4}\W+api(?:\W+\w+){0,4}"
            r"\W+reductions?(?:\W+\w+){0,6}\W+not(?:\W+\w+){0,4}\W+completion gates?",
        )

    def test_v4_delegation_depth_and_writer_boundaries_are_explicit(self) -> None:
        text = _text(ORCHESTRATOR_SKILL).lower()

        self.assertRegex(text, r"nested delegation(?:\W+\w+){0,10}\W+denied by default")
        self.assertRegex(text, r"root-authorized(?:\W+\w+){0,8}\W+depth 2(?:\W+\w+){0,8}\W+research")
        self.assertRegex(text, r"main agent(?:\W+\w+){0,8}\W+counts as(?:\W+\w+){0,4}\W+writer")
        self.assertRegex(text, r"one writer(?:\W+\w+){0,8}\W+named boundary")
        self.assertRegex(text, r"writer(?:\W+\w+){0,8}\W+boundary")

    def test_v4_work_package_and_retry_contracts_are_short_and_material(self) -> None:
        text = _text(ORCHESTRATOR_SKILL).lower()

        for field in ("evidence lane", "objective", "evidence scope", "write boundary", "artifact", "done", "stop"):
            with self.subTest(field=field):
                self.assertRegex(text, rf"\b{re.escape(field)}\b")
        self.assertRegex(text, r"max(?:imum)? two material revisions|max 2 material revisions")
        self.assertNotRegex(text, r"eta\s*\*\s*2")
        self.assertNotRegex(text, r"file-count(?:\W+\w+){0,6}\W+stop rule")
        self.assertRegex(text, r"idempotent reads(?:\W+\w+){0,8}\W+retry(?:\W+\w+){0,4}\W+max(?:imum)? 2")
        self.assertRegex(text, r"writes(?:\W+\w+){0,8}\W+state-check(?:\W+\w+){0,8}\W+retry")
        self.assertRegex(text, r"wait count(?:\W+\w+){0,6}\W+diagnostic only")

    def test_feature_workflow_defaults_are_outcome_based_without_fixed_four_roles(self) -> None:
        text = _text(FEATURE_WORKFLOW_SKILL).lower()

        self.assertIn("outcome-based", text)
        self.assertRegex(text, r"feature(?:\W+\w+){0,10}\W+required_roles(?:\W+\w+){0,8}\W+empty")
        self.assertRegex(text, r"bugfix(?:\W+\w+){0,10}\W+required_roles(?:\W+\w+){0,8}\W+empty")
        self.assertRegex(text, r"verification(?:\W+\w+){0,8}\W+proportional")
        self.assertRegex(text, r"review(?:\W+\w+){0,8}\W+proportional")
        self.assertIn("context isolation", text)
        self.assertIn("evidence lane", text)
        self.assertIn("root-only", text)
        self.assertRegex(text, r"branch(?:\W+\w+){0,4}\W+(discipline|safety)")
        self.assertRegex(text, r"(do not|must not|never)(?:\W+\w+){0,12}\W+(master|main|pre|test)")
        self.assertNotRegex(text, r"required_roles\s*=\s*implementer,test_designer,test_runner,reviewer")
        self.assertNotRegex(text, r"default required roles(?:\W+\w+){0,16}\W+implementer")
        self.assertNotIn("test_runner remains required", text)

    def test_workflow_profile_feature_and_bugfix_defaults_are_empty_and_evidence_gated(self) -> None:
        module = _load_module(WORKFLOW_PROFILE, "workflow_profile_v4_contract")

        for skill in ("feature", "bugfix"):
            with self.subTest(skill=skill):
                requirements = module.DEFAULT_SKILL_REQUIREMENTS[skill]
                self.assertEqual(requirements["required_roles"], [])
                self.assertEqual(requirements.get("delegation_policy"), "outcome_based")
                self.assertTrue(requirements["requires_verification_commands"])
                self.assertTrue(requirements["requires_reviewer_result"])

    def test_global_template_does_not_require_every_named_capability_as_a_separate_agent(self) -> None:
        text = _text(GLOBAL_TEMPLATE).lower()

        self.assertIn("outcome-based delegation", text)
        self.assertNotRegex(
            text,
            r"keep implementation, test design, test writing, test running, review, conflict resolution, and deployment responsibilities isolated",
        )
        self.assertNotRegex(text, r"every named role(?:\W+\w+){0,10}\W+separate agent")
        self.assertNotRegex(text, r"implementation, testing, review(?:\W+\w+){0,12}\W+separate agents")

    def test_critical_gate_templates_are_bash_only_and_drop_legacy_lifecycle_hooks(self) -> None:
        for template in HOOK_TEMPLATES:
            rendered = _text(template)
            with self.subTest(template=template.name):
                self.assertIn("critical_action_gate.py", rendered)
                self.assertRegex(rendered, r"(?i)\bbash\b")
                for legacy in (
                    "orchestrator_user_prompt.py",
                    "orchestrator_pre_tool_use.py",
                    "orchestrator_post_tool_use.py",
                    "orchestrator_stop.py",
                ):
                    self.assertNotIn(legacy, rendered)

    def test_runtime_skill_routing_smoke_fails_closed_on_codex_version_mismatch(self) -> None:
        module = _load_module(RUNTIME_SMOKE, "runtime_skill_routing_smoke_contract")

        with patch.object(module, "_codex_version", return_value="0.144.0"):
            self.assertNotEqual(module.main(), 0)

    def test_v41_generic_preserve_semantics_trigger_source_backed_invariant_matrix(self) -> None:
        text = _feature_owner_text()
        lowered = text.lower()

        self.assertRegex(
            lowered,
            r"explicit preserve, unchanged, no-expansion, or only-change acceptance requires "
            r"a smallest source-backed invariant matrix before implementation",
        )
        self.assertIn("hand the matrix to the sole writer at the named write boundary", lowered)
        self.assertIn("do not create a mandatory new role", lowered)

        generic_policy_section = re.search(
            r"(preserve|unchanged|no[- ]expansion|only[- ]change).{0,1200}",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        self.assertIsNotNone(generic_policy_section)
        self.assertNotRegex(generic_policy_section.group(0), r"\bC3\b|JP|Japanese|kana|MiniMax|Cartesia")

    def test_v41_allows_one_genuine_p0_p1_corrective_review_but_caps_open_loops(self) -> None:
        text = _feature_owner_text().lower()

        self.assertRegex(text, r"one genuine same-reviewer p0/p1 corrective re-review")
        self.assertIn("open review loops remain capped", text)
        self.assertIn("redundant or speculative re-review is blocked", text)


if __name__ == "__main__":
    unittest.main()
