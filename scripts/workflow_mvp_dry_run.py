#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_STATE_VERSION = 1
SUBAGENT_ROLE_VERSION = "subagent-role-v2.0"

PLANNED_SKILLS = [
    "skills/orchestrator",
    "skills/feature-workflow",
    "skills/merge-workflow",
    "skills/review-workflow",
    "skills/predeploy-workflow",
    "skills/merge-safety",
    "skills/orchestrator-observability",
]

PLANNED_AGENTS = [
    "agents/requirements-interpreter.toml",
    "agents/researcher.toml",
    "agents/implementer.toml",
    "agents/test-designer.toml",
    "agents/test-writer.toml",
    "agents/test-runner.toml",
    "agents/reviewer.toml",
    "agents/critic.toml",
    "agents/conflict-resolver.toml",
    "agents/deployer.toml",
]

PLANNED_HOOKS = [
    "hooks/critical_action_gate.py",
]

DEPRECATED_GLOBAL_AGENTS = [
    "agents/codebase-research-explorer.toml",
]

SCENARIOS = {
    "feature": {
        "state_version": WORKFLOW_STATE_VERSION,
        "task_id": "dry-run-feature",
        "current_mode": "split_workflow",
        "task_class": "feature",
        "selected_skill": "feature",
        "stage": "planning",
        "required_roles": [],
        "delegation_policy": "outcome_based",
        "conditional_roles": ["test_writer", "researcher", "requirements_interpreter", "critic"],
        "role_evidence": {
            "test_designer": {"implementation_diff_visible": False, "sources_read": ["Requirement Contract", "public API/test style"]},
            "test_runner": {"status": "not_run", "commands": []},
            "reviewer": {"result": "not_run"},
        },
        "must_record": ["implementation_owner", "verification_commands", "reviewer_result", "branch_discipline"],
        "final_status": None,
    },
    "merge": {
        "state_version": WORKFLOW_STATE_VERSION,
        "task_id": "dry-run-merge",
        "current_mode": "split_workflow",
        "task_class": "merge",
        "selected_skill": "merge",
        "stage": "planning",
        "required_roles": ["conflict_resolver", "test_runner"],
        "conditional_roles": ["reviewer"],
        "forbidden_roles": ["implementer"],
        "must_use": ["merge-safety", "conflict_resolver"],
        "final_status": None,
    },
    "review": {
        "state_version": WORKFLOW_STATE_VERSION,
        "task_id": "dry-run-review",
        "current_mode": "split_workflow",
        "task_class": "review",
        "selected_skill": "review",
        "stage": "planning",
        "required_roles": ["reviewer"],
        "conditional_roles": ["researcher", "critic"],
        "forbidden_roles": ["implementer", "test_writer", "deployer"],
        "final_status": None,
    },
    "predeploy": {
        "state_version": WORKFLOW_STATE_VERSION,
        "task_id": "dry-run-predeploy",
        "current_mode": "split_workflow",
        "task_class": "predeploy",
        "selected_skill": "predeploy",
        "stage": "planning",
        "required_roles": [],
        "lane": "fast_pre",
        "phase": "planning",
        "intent": "low-risk pre validation of a known ref",
        "action": "checkout,restart,verify",
        "target_env": "aiserver-pre",
        "target_ref": "feature-branch-or-sha",
        "writes_shared_branch": False,
        "risk_flags": [],
        "authorization_required": True,
        "deployer_allowed_without_current_turn_authorization": False,
        "subagent_count": 0,
        "asked_user_count": 0,
        "lanes": {
            "fast_pre": {
                "required_roles": [],
                "writes_shared_branch": False,
                "default_subagent_count": 0,
            },
            "fast_hotfix": {
                "required_roles": [],
                "requires_exact_ref": True,
                "writes_shared_branch": True,
                "default_subagent_count": 0,
            },
            "shared_lane": {
                "required_roles": ["deployer"],
                "may_require": ["merge-workflow", "merge-safety", "reviewer"],
                "writes_shared_branch": True,
            },
            "controlled_ops": {
                "required_roles": ["deployer"],
                "may_require": ["reviewer", "deploy checklist"],
                "risk_flags": ["config", "schema", "table", "external_service", "production_like"],
            },
        },
        "final_status": None,
    },
}


def existing(paths: list[str]) -> list[dict[str, object]]:
    return [
        {"path": path, "exists": (ROOT / path).exists()}
        for path in paths
    ]


def keyword_hits(path: Path, keywords: list[str]) -> list[dict[str, object]]:
    if not path.exists() or not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    hits = []
    lowered_keywords = [keyword.lower() for keyword in keywords]
    for number, line in enumerate(lines, 1):
        lowered = line.lower()
        matched = [keyword for keyword in lowered_keywords if keyword in lowered]
        if matched:
            hits.append({"path": str(path), "line": number, "keywords": matched, "text": line[:240]})
    return hits


def scan_global_codex(codex_home: Path, keywords: list[str]) -> dict[str, object]:
    agents_dir = codex_home / "agents"
    project_bound_agents = []
    if agents_dir.exists():
        for path in sorted(agents_dir.glob("*.toml")):
            name_hit = any(keyword.lower() in path.name.lower() for keyword in keywords)
            content_hits = keyword_hits(path, keywords)
            if name_hit or content_hits:
                project_bound_agents.append({
                    "path": str(path),
                    "name_hit": name_hit,
                    "content_hit_count": len(content_hits),
                })

    config_hits = []
    for path in (codex_home / "config.toml", codex_home / "hooks.json"):
        config_hits.extend(keyword_hits(path, keywords))

    hook_hits = []
    hooks_dir = codex_home / "hooks"
    if hooks_dir.exists():
        for path in sorted(hooks_dir.glob("*")):
            if path.is_file():
                hook_hits.extend(keyword_hits(path, keywords))

    mcp_candidates: set[Path] = set()
    if codex_home.exists():
        for pattern in ("*mcp*", "mcp*/*", "mcp*/**/*", "servers*/*", "servers*/**/*"):
            for path in codex_home.glob(pattern):
                if path.is_file():
                    mcp_candidates.add(path)
    mcp_hits = []
    for path in sorted(mcp_candidates):
        if not path.is_file():
            continue
        mcp_hits.extend(keyword_hits(path, keywords))

    return {
        "codex_home": str(codex_home),
        "keywords": keywords,
        "project_bound_agents": project_bound_agents,
        "project_biased_config_hits": config_hits,
        "project_biased_hook_hits": hook_hits,
        "project_biased_mcp_hits": mcp_hits,
        "mutates_global_codex": False,
    }


def build_report(args: argparse.Namespace) -> dict[str, object]:
    scenarios = SCENARIOS if args.scenario == "all" else {args.scenario: SCENARIOS[args.scenario]}
    return {
        "schema_version": "workflow-mvp-dry-run-v1",
        "workflow_state_version": WORKFLOW_STATE_VERSION,
        "subagent_role_version": SUBAGENT_ROLE_VERSION,
        "planned_install": {
            "skills": existing(PLANNED_SKILLS),
            "agents": existing(PLANNED_AGENTS),
            "hooks": existing(PLANNED_HOOKS),
            "deprecated_global_agents_removed_on_apply": existing(DEPRECATED_GLOBAL_AGENTS),
        },
        "scenario_ledgers": scenarios,
        "global_scan": scan_global_codex(Path(args.codex_home).expanduser(), args.project_keyword),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run the orchestrator MVP workflow installation and global pollution scan.")
    parser.add_argument("--codex-home", default="~/.codex")
    parser.add_argument("--project-keyword", action="append", help="Project keyword to flag in global Codex config. Defaults to aiserver.")
    parser.add_argument("--scenario", choices=["all", *SCENARIOS.keys()], default="all")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    if not args.project_keyword:
        args.project_keyword = ["aiserver"]
    report = build_report(args)
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
