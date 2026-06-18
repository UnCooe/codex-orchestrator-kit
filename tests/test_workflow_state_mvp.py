from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

from workflow_profile import (  # noqa: E402
    current_turn_workflow_state,
    infer_capability,
    role_satisfies,
    skill_requirements,
    workflow_state_status,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")


def _run_hook(script: str, payload: dict) -> dict | str:
    proc = subprocess.run(
        [sys.executable, str(HOOKS / script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    text = proc.stdout.strip()
    return json.loads(text) if text else ""


class WorkflowStateMvpTests(unittest.TestCase):
    def test_pre_tool_blocks_heavy_task_without_workflow_state_when_policy_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            profile = repo / ".codex" / "review-profile.toml"
            profile.parent.mkdir(parents=True)
            profile.write_text(
                "[workflow_policy]\nheavy_task_requires_state = true\n",
                encoding="utf-8",
            )
            transcript = repo / ".codex" / "sessions" / "pre.jsonl"
            _write_jsonl(transcript, [{"payload": {"type": "message", "role": "user", "content": "implement this feature"}}])

            output = _run_hook(
                "orchestrator_pre_tool_use.py",
                {
                    "cwd": str(repo),
                    "transcript_path": str(transcript),
                    "tool_name": "shell_command",
                    "tool_input": {"command": "rg --files ."},
                },
            )

            self.assertEqual(output["decision"], "block")
            self.assertIn("workflow_state.current_mode", output["reason"])

    def test_pre_tool_allows_workflow_state_initialization_without_existing_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            profile = repo / ".codex" / "review-profile.toml"
            profile.parent.mkdir(parents=True)
            profile.write_text("[workflow_policy]\nheavy_task_requires_state = true\n", encoding="utf-8")
            patch = """*** Begin Patch
*** Add File: .codex/workflows/task-1/workflow_state.json
+{"state_version":1,"current_mode":"split_workflow"}
*** Add File: .codex/workflows/task-1/ledger.md
+workflow_state.current_mode=split_workflow
*** End Patch
"""

            output = _run_hook(
                "orchestrator_pre_tool_use.py",
                {
                    "cwd": str(repo),
                    "tool_name": "apply_patch",
                    "tool_input": patch,
                },
            )

            self.assertEqual(output, "")

    def test_pre_tool_allows_tiny_direct_heavy_task_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            profile = repo / ".codex" / "review-profile.toml"
            profile.parent.mkdir(parents=True)
            profile.write_text(
                "[workflow_policy]\nheavy_task_requires_state = true\ntiny_direct_requires_reason = true\n",
                encoding="utf-8",
            )
            transcript = repo / ".codex" / "sessions" / "pre.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "inspect this tiny repo"}},
                    {
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": (
                                "workflow_state.current_mode=tiny_direct\n"
                                "workflow_state.mode_reason=single bounded repository inventory before choosing exact file"
                            ),
                        }
                    },
                ],
            )

            output = _run_hook(
                "orchestrator_pre_tool_use.py",
                {
                    "cwd": str(repo),
                    "transcript_path": str(transcript),
                    "tool_name": "shell_command",
                    "tool_input": {"command": "rg --files ."},
                },
            )

            self.assertEqual(output, "")

    def test_stop_emits_continuation_when_split_workflow_missing_required_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            transcript = repo / ".codex" / "sessions" / "stop.jsonl"
            _write_jsonl(transcript, [{"payload": {"type": "message", "role": "user", "content": "implement feature"}}])

            output = _run_hook(
                "orchestrator_stop.py",
                {
                    "cwd": str(repo),
                    "transcript_path": str(transcript),
                    "last_assistant_message": (
                        "Implemented the feature and ran tests.\n"
                        "workflow_state.current_mode=split_workflow\n"
                        "workflow_state.selected_skill=feature\n"
                        "workflow_state.required_roles=implementer,test_designer,test_runner,reviewer\n"
                        "workflow_state.spawned_roles=implementer,test_designer\n"
                        "workflow_state.waited_roles=implementer,test_designer\n"
                        "workflow_state.closed_roles=implementer,test_designer\n"
                        "workflow_state.verification_commands=pytest tests/test_feature.py\n"
                        "workflow_state.reviewer_result=pass\n"
                    ),
                },
            )

            self.assertEqual(output["decision"], "block")
            self.assertTrue(output.get("suppressOutput"))
            self.assertEqual(output["reason"], "Split workflow is missing required role lifecycle evidence.")

    def test_stop_emits_continuation_when_missing_review_and_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            transcript = repo / ".codex" / "sessions" / "stop.jsonl"
            _write_jsonl(transcript, [{"payload": {"type": "message", "role": "user", "content": "implement feature"}}])

            output = _run_hook(
                "orchestrator_stop.py",
                {
                    "cwd": str(repo),
                    "transcript_path": str(transcript),
                    "last_assistant_message": (
                        "Implemented the feature.\n"
                        "workflow_state.current_mode=split_workflow\n"
                        "workflow_state.selected_skill=feature\n"
                        "workflow_state.required_roles=implementer,test_designer,test_runner,reviewer\n"
                        "workflow_state.spawned_roles=implementer,test_designer,test_runner,reviewer\n"
                        "workflow_state.waited_roles=implementer,test_designer,test_runner,reviewer\n"
                        "workflow_state.closed_roles=implementer,test_designer,test_runner,reviewer\n"
                    ),
                },
            )

            self.assertEqual(output["decision"], "block")
            self.assertTrue(output.get("suppressOutput"))
            self.assertIn(output["reason"], {
                "Split workflow is missing verification_commands in workflow_state.",
                "Split workflow is missing reviewer_result in workflow_state.",
            })

    def test_stop_blocks_spawned_roles_without_close_or_retained_reason(self) -> None:
        state = {
            "current_mode": "split_workflow",
            "selected_skill": "review",
            "required_roles": ["reviewer"],
            "spawned_roles": ["reviewer"],
            "waited_roles": ["reviewer"],
            "closed_roles": [],
            "reviewer_result": "pass",
        }

        status = workflow_state_status(state)

        self.assertIn("spawned_roles_not_closed", status["errors"])

    def test_feature_matrix_requires_test_runner_not_test_writer_alternative(self) -> None:
        requirements = skill_requirements("feature")
        self.assertEqual(requirements["required_roles"], ["implementer", "test_designer", "test_runner", "reviewer"])
        self.assertEqual(requirements["one_of_roles"], [])
        self.assertIn("test_writer", requirements["conditional_roles"])

        state = {
            "current_mode": "split_workflow",
            "selected_skill": "feature",
            "spawned_roles": ["implementer", "test_designer", "test_writer", "reviewer"],
            "waited_roles": ["implementer", "test_designer", "test_writer", "reviewer"],
            "closed_roles": ["implementer", "test_designer", "test_writer", "reviewer"],
            "verification_commands": ["pytest tests/test_feature.py"],
            "reviewer_result": "pass",
        }
        status = workflow_state_status(state)
        self.assertIn("test_runner", status["missing_by_phase"]["spawned_roles"])

    def test_predeploy_fast_lanes_do_not_require_deployer_by_default(self) -> None:
        requirements = skill_requirements("predeploy")
        self.assertEqual(requirements["required_roles"], ["deployer"])
        self.assertEqual(requirements["fast_lanes"], ["fast_pre", "fast_hotfix"])
        self.assertFalse(requirements["fast_lanes_require_roles"])

        state = {
            "current_mode": "split_workflow",
            "selected_skill": "predeploy",
            "lane": "fast-pre",
            "authorization": "current-turn authorized fast-pre checkout and restart",
            "verification_commands": ["pre_plan.sh --quick fix/x fast-pre"],
        }
        status = workflow_state_status(state)

        self.assertNotIn("missing_required_roles", status["errors"])
        self.assertEqual(status["missing_by_phase"], {})
        self.assertNotIn("missing_verification_commands", status["errors"])

    def test_predeploy_shared_lane_still_requires_deployer(self) -> None:
        state = {
            "current_mode": "split_workflow",
            "selected_skill": "predeploy",
            "lane": "shared-lane",
            "authorization": "current-turn authorized merge to pre",
            "rollback_plan": "restore origin/pre previous sha",
            "verification_commands": ["pre_plan.sh fix/x shared-lane"],
        }

        status = workflow_state_status(state)

        self.assertIn("missing_required_roles", status["errors"])
        self.assertIn("deployer", status["missing_by_phase"]["spawned_roles"])

    def test_predeploy_missing_authorization_is_blocking_even_for_fast_pre(self) -> None:
        state = {
            "current_mode": "split_workflow",
            "selected_skill": "predeploy",
            "lane": "fast-pre",
            "verification_commands": ["pre_plan.sh --quick fix/x fast-pre"],
        }

        status = workflow_state_status(state)

        self.assertIn("missing_current_turn_authorization", status["errors"])

    def test_predeploy_fast_hotfix_requires_exact_ref_and_rollback_plan(self) -> None:
        unsafe_state = {
            "current_mode": "split_workflow",
            "selected_skill": "predeploy",
            "lane": "fast-hotfix",
            "authorization": "current-turn authorized urgent master hotfix",
            "writes_shared_branch": True,
            "target_ref": "fix/urgent",
            "verification_commands": ["pre_plan.sh --quick fix/urgent fast-hotfix"],
        }

        unsafe_status = workflow_state_status(unsafe_state)

        self.assertIn("missing_rollback_plan", unsafe_status["errors"])
        self.assertIn("fast_hotfix_missing_exact_ref", unsafe_status["errors"])

        safe_state = {
            **unsafe_state,
            "target_ref": "abc123def456",
            "rollback_plan": "restore master to 1111111",
        }
        safe_status = workflow_state_status(safe_state)

        self.assertNotIn("missing_rollback_plan", safe_status["errors"])
        self.assertNotIn("fast_hotfix_missing_exact_ref", safe_status["errors"])
        self.assertNotIn("missing_current_turn_authorization", safe_status["errors"])

    def test_workflow_state_parses_predeploy_lane_observability_fields(self) -> None:
        state = current_turn_workflow_state({
            "last_assistant_message": (
                "workflow_state.current_mode=split_workflow\n"
                "workflow_state.selected_skill=predeploy\n"
                "workflow_state.lane=fast-hotfix\n"
                "workflow_state.phase=authorized_apply\n"
                "workflow_state.target_env=aiserver-pre\n"
                "workflow_state.target_ref=abc123\n"
                "workflow_state.writes_shared_branch=true\n"
                "workflow_state.risk_flags=config,external-service\n"
                "workflow_state.asked_user_count=1\n"
                "workflow_state.subagent_count=0\n"
            )
        })

        self.assertEqual(state["lane"], "fast_hotfix")
        self.assertEqual(state["phase"], "authorized_apply")
        self.assertEqual(state["target_env"], "aiserver_pre")
        self.assertEqual(state["target_ref"], "abc123")
        self.assertEqual(state["writes_shared_branch"], True)
        self.assertEqual(state["risk_flags"], ["config", "external-service"])
        self.assertEqual(state["asked_user_count"], 1)
        self.assertEqual(state["subagent_count"], 0)

    def test_merge_reviewer_is_conditional_and_implementer_forbidden(self) -> None:
        requirements = skill_requirements("merge")
        self.assertEqual(requirements["required_roles"], ["conflict_resolver", "test_runner"])
        self.assertIn("reviewer", requirements["conditional_roles"])
        self.assertIn("implementer", requirements["forbidden_roles"])

        state = {
            "current_mode": "split_workflow",
            "selected_skill": "merge",
            "spawned_roles": ["conflict_resolver", "test_runner", "implementer"],
            "waited_roles": ["conflict_resolver", "test_runner", "implementer"],
            "closed_roles": ["conflict_resolver", "test_runner", "implementer"],
            "verification_commands": ["git diff --check"],
        }
        status = workflow_state_status(state)
        self.assertIn("forbidden_roles_present", status["errors"])

    def test_role_capability_mapping_includes_new_roles_and_read_only_boundaries(self) -> None:
        self.assertEqual(infer_capability("requirements_interpreter"), "requirements")
        self.assertEqual(infer_capability("test_writer"), "test_implementation")
        self.assertEqual(infer_capability("test_runner"), "test_execution")
        self.assertEqual(infer_capability("conflict_resolver"), "conflict_resolution")
        self.assertEqual(infer_capability("deployer"), "deployment")
        self.assertEqual(infer_capability("reviewer"), "review")
        self.assertEqual(infer_capability("test_designer"), "test_design")
        self.assertFalse(role_satisfies("implementer", "conflict_resolver"))

        reviewer_toml = (ROOT / "agents" / "reviewer.toml").read_text(encoding="utf-8")
        test_designer_toml = (ROOT / "agents" / "test-designer.toml").read_text(encoding="utf-8")
        deployer_toml = (ROOT / "agents" / "deployer.toml").read_text(encoding="utf-8")
        self.assertIn('sandbox_mode = "read-only"', reviewer_toml)
        self.assertIn("Do not write code", reviewer_toml)
        self.assertIn('sandbox_mode = "read-only"', test_designer_toml)
        self.assertIn("Do not read or rely on implementer diffs", test_designer_toml)
        self.assertIn("implementation_diff_visible=false", test_designer_toml)
        self.assertNotIn("workspace-write", deployer_toml)
        self.assertIn("Inherit the parent session sandbox", deployer_toml)

    def test_subagent_role_v2_models_and_prompt_hygiene(self) -> None:
        expected_reasoning = {
            "implementer.toml": "high",
            "reviewer.toml": "high",
            "critic.toml": "high",
            "conflict-resolver.toml": "high",
            "test-designer.toml": "medium",
            "test-writer.toml": "medium",
            "test-runner.toml": "medium",
            "requirements-interpreter.toml": "medium",
            "researcher.toml": "medium",
            "deployer.toml": "medium",
        }
        agent_files = sorted((ROOT / "agents").glob("*.toml"))
        self.assertNotEqual(agent_files, [])
        self.assertFalse((ROOT / "agents" / "codebase-research-explorer.toml").exists())

        for path in agent_files:
            with path.open("rb") as stream:
                data = tomllib.load(stream)
            self.assertEqual(data.get("model"), "gpt-5.5", path.name)
            self.assertEqual(data.get("model_reasoning_effort"), expected_reasoning[path.name], path.name)

        implementer = (ROOT / "agents" / "implementer.toml").read_text(encoding="utf-8")
        for phrase in ("Data Flow", "Input -> Transform -> Output", "Contract Verification", "Type Discipline", "Minimal Change"):
            self.assertIn(phrase, implementer)

        reviewer = (ROOT / "agents" / "reviewer.toml").read_text(encoding="utf-8")
        for phrase in ("Evidence & Contract Verification", "P0", "P1", "P2", "Needs Context", "Do not write code"):
            self.assertIn(phrase, reviewer)

        test_runner = (ROOT / "agents" / "test-runner.toml").read_text(encoding="utf-8")
        for phrase in ("commands", "result", "failure_summary", "not_run_reason", "next_action"):
            self.assertIn(phrase, test_runner)

        researcher = (ROOT / "agents" / "researcher.toml").read_text(encoding="utf-8")
        self.assertIn("do not require a separate global codebase-research-explorer role", researcher)

    def test_review_only_does_not_require_implementer(self) -> None:
        state = {
            "current_mode": "split_workflow",
            "selected_skill": "review",
            "required_roles": ["reviewer"],
            "spawned_roles": ["reviewer"],
            "waited_roles": ["reviewer"],
            "closed_roles": ["reviewer"],
            "reviewer_result": "pass",
        }

        status = workflow_state_status(state)

        self.assertEqual(status["errors"], [])

        bad_state = {
            **state,
            "spawned_roles": ["reviewer", "implementer"],
            "waited_roles": ["reviewer", "implementer"],
            "closed_roles": ["reviewer", "implementer"],
        }
        bad_status = workflow_state_status(bad_state)
        self.assertIn("forbidden_roles_present", bad_status["errors"])

    def test_current_turn_workflow_state_parses_line_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / ".codex" / "sessions" / "ledger.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "implement"}},
                    {
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": (
                                "workflow_state.current_mode=split_workflow\n"
                                "workflow_state.state_version=1\n"
                                "workflow_state.selected_skill=merge\n"
                                "workflow_state.stage=planning\n"
                                "workflow_state.role_evidence={\"test_runner\":{\"status\":\"not_run\"}}\n"
                                "workflow_state.required_roles=conflict_resolver,test_runner,reviewer"
                            ),
                        }
                    },
                ],
            )

            state = current_turn_workflow_state({"transcript_path": str(transcript)})

            self.assertEqual(state["current_mode"], "split_workflow")
            self.assertEqual(state["state_version"], 1)
            self.assertEqual(state["selected_skill"], "merge")
            self.assertEqual(state["stage"], "planning")
            self.assertEqual(state["role_evidence"], {"test_runner": {"status": "not_run"}})
            self.assertEqual(state["required_roles"], ["conflict_resolver", "test_runner", "reviewer"])

    def test_dry_run_reports_planned_files_and_predeploy_authorization_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            output = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "workflow_mvp_dry_run.py"),
                    "--codex-home",
                    str(codex_home),
                    "--scenario",
                    "predeploy",
                ],
                text=True,
                capture_output=True,
                check=True,
            )

            report = json.loads(output.stdout)
            self.assertFalse(report["global_scan"]["mutates_global_codex"])
            self.assertEqual(report["workflow_state_version"], 1)
            self.assertEqual(report["subagent_role_version"], "subagent-role-v2.0")
            predeploy = report["scenario_ledgers"]["predeploy"]
            self.assertEqual(predeploy["lane"], "fast_pre")
            self.assertEqual(predeploy["required_roles"], [])
            self.assertEqual(predeploy["subagent_count"], 0)
            self.assertFalse(predeploy["writes_shared_branch"])
            self.assertEqual(predeploy["lanes"]["shared_lane"]["required_roles"], ["deployer"])
            self.assertEqual(predeploy["lanes"]["controlled_ops"]["required_roles"], ["deployer"])
            self.assertFalse(predeploy["deployer_allowed_without_current_turn_authorization"])
            planned_agents = {item["path"] for item in report["planned_install"]["agents"]}
            self.assertIn("agents/conflict-resolver.toml", planned_agents)
            self.assertNotIn("agents/codebase-research-explorer.toml", planned_agents)
            deprecated = {item["path"] for item in report["planned_install"]["deprecated_global_agents_removed_on_apply"]}
            self.assertIn("agents/codebase-research-explorer.toml", deprecated)

    def test_dry_run_scenarios_cover_merge_and_review_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "workflow_mvp_dry_run.py"),
                    "--codex-home",
                    str(Path(temp_dir) / ".codex"),
                    "--scenario",
                    "all",
                ],
                text=True,
                capture_output=True,
                check=True,
            )

            report = json.loads(output.stdout)
            self.assertEqual(report["scenario_ledgers"]["merge"]["required_roles"], ["conflict_resolver", "test_runner"])
            self.assertIn("merge-safety", report["scenario_ledgers"]["merge"]["must_use"])
            self.assertEqual(report["scenario_ledgers"]["review"]["required_roles"], ["reviewer"])
            self.assertIn("deployer", report["scenario_ledgers"]["review"]["forbidden_roles"])
            planned_agents = {item["path"] for item in report["planned_install"]["agents"]}
            self.assertNotIn("agents/codebase-research-explorer.toml", planned_agents)


if __name__ == "__main__":
    unittest.main()
