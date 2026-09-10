from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"

MANAGED_SKILLS = [
    "orchestrator",
    "orchestrator-observability",
    "feature-workflow",
    "review-workflow",
    "merge-workflow",
    "predeploy-workflow",
    "merge-safety",
]

PRIMARY_ROUTES = {
    "feature": "../feature-workflow/SKILL.md",
    "review": "../review-workflow/SKILL.md",
    "merge": "../merge-workflow/SKILL.md",
    "predeploy": "../predeploy-workflow/SKILL.md",
}


def _skill_text(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def _allow_implicit_invocation(name: str) -> bool:
    path = SKILLS / name / "agents" / "openai.yaml"
    if not path.exists():
        raise AssertionError(f"{name} missing agents/openai.yaml")
    text = path.read_text(encoding="utf-8")
    match = re.search(r"(?m)^\s*allow_implicit_invocation:\s*(true|false)\s*$", text)
    if not match:
        raise AssertionError(f"{name} agents/openai.yaml missing policy.allow_implicit_invocation")
    return match.group(1) == "true"


class SkillRoutingContractTests(unittest.TestCase):
    def test_managed_skill_catalog_is_exactly_the_workflow_catalog(self) -> None:
        actual = sorted(path.name for path in SKILLS.iterdir() if path.is_dir())
        self.assertEqual(actual, sorted(MANAGED_SKILLS))

    def test_orchestrator_routes_each_phase_to_one_sibling_primary_skill(self) -> None:
        text = _skill_text("orchestrator")

        for phase, route in PRIMARY_ROUTES.items():
            with self.subTest(phase=phase):
                self.assertEqual(text.count(route), 1, f"{phase} must map exactly once to {route}")

        all_sibling_routes = re.findall(r"\.\./([a-z0-9-]+)/SKILL\.md", text)
        expected = [route.split("/")[1] for route in PRIMARY_ROUTES.values()]
        self.assertEqual(sorted(all_sibling_routes), sorted(expected))

    def test_orchestrator_routing_explicitly_prohibits_preload_or_bulk_reads(self) -> None:
        text = _skill_text("orchestrator").lower()
        self.assertRegex(
            text,
            r"(do not|never|must not)[^\n]{0,120}(pre-?read|pre-?load|bulk read|batch read|read all)",
        )

    def test_orchestrator_routing_fails_closed_when_a_sibling_skill_is_missing(self) -> None:
        text = _skill_text("orchestrator").lower()
        self.assertRegex(text, r"fail(?:s|ed)?(?:\W+\w+){0,8}\W+missing")
        self.assertRegex(text, r"missing(?:\W+\w+){0,8}\W+skill")

    def test_only_orchestrator_and_observability_are_policy_managed(self) -> None:
        for name in MANAGED_SKILLS:
            expected = name in {"orchestrator", "orchestrator-observability"}
            with self.subTest(skill=name):
                self.assertEqual(_allow_implicit_invocation(name), expected)

    def test_leaf_workflow_skills_do_not_route_to_sibling_primary_skills(self) -> None:
        leaf_skills = {"feature-workflow", "review-workflow", "predeploy-workflow", "merge-safety"}
        for name in sorted(leaf_skills):
            text = _skill_text(name)
            with self.subTest(skill=name):
                self.assertNotRegex(text, r"\.\./(feature-workflow|review-workflow|merge-workflow|predeploy-workflow)/SKILL\.md")

    def test_merge_workflow_loads_merge_safety_as_a_follow_on_guard(self) -> None:
        text = _skill_text("merge-workflow")
        self.assertEqual(text.count("../merge-safety/SKILL.md"), 1)
        self.assertRegex(text.lower(), r"load(?:s|ed)?(?:\W+\w+){0,8}\W+merge-safety")

    def test_global_agent_template_routes_nontrivial_work_only_through_orchestrator(self) -> None:
        template = ROOT / "templates" / "global-agents.md"
        self.assertTrue(template.is_file(), "expected templates/global-agents.md")
        template_text = template.read_text(encoding="utf-8")
        self.assertIn("$orchestrator", template_text)
        self.assertRegex(template_text.lower(), r"non-?trivial(?:\W+\w+){0,8}\W+\$orchestrator")
        for forbidden in ("$feature-workflow", "$review-workflow", "$merge-workflow", "$predeploy-workflow", "$merge-safety"):
            self.assertNotIn(forbidden, template_text)

    def test_orchestrator_soft_owner_allows_exact_project_skill_as_direct_owner(self) -> None:
        text = _skill_text("orchestrator").lower()

        self.assertRegex(text, r"project(?:-|\s)specific")
        self.assertRegex(text, r"project(?:\W+\w+){0,8}\W+skill")
        self.assertRegex(text, r"(direct|directly)(?:\W+\w+){0,8}\W+owner")
        self.assertRegex(text, r"(do not|must not|not)(?:\W+\w+){0,12}\W+force(?:\W+\w+){0,12}\W+project")

    def test_orchestrator_intervenes_only_for_ambiguous_cross_phase_or_coordination_cases(self) -> None:
        text = _skill_text("orchestrator").lower()

        for required in ("ambiguous", "cross-phase", "coordination"):
            with self.subTest(required=required):
                self.assertIn(required, text)
        self.assertRegex(text, r"(already selected|selected)(?:\W+\w+){0,10}\W+project(?:\W+\w+){0,8}\W+owner")
        self.assertRegex(text, r"(do not|must not|not)(?:\W+\w+){0,10}\W+(reload|load again|duplicate)")

    def test_routing_contract_has_single_mutation_owner_per_phase(self) -> None:
        text = _skill_text("orchestrator").lower()

        self.assertRegex(text, r"(single|one|exactly one)(?:\W+\w+){0,8}\W+(mutation\s+)?owner")
        self.assertRegex(text, r"(same|one|single)(?:\W+\w+){0,8}\W+phase")


if __name__ == "__main__":
    unittest.main()
