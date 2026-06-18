---
name: predeploy-workflow
description: Use for predeploy, staging, rollback, release verification, deployment handoff, server write, restart, or environment mutation workflows with current-turn authorization, semantic lane routing, and workflow_state ledger tracking.
---

# Predeploy Workflow

Version: `predeploy-routing-v2.0`.

Use this skill for predeploy, staging validation, rollback, release handoff, or deployment operations. Route by user intent and risk, not by branch name alone.

## Authorization Gate

Do not run server writes, deploy, push, release, restart, or environment mutation unless the user explicitly authorized that action in the current turn. The `deployer` role inherits the current session sandbox and is not pre-authorized by its role name. Read-only checks and dry-runs may happen before authorization when project policy allows.

## Lane Routing

Choose one lane before write actions:

- `fast_pre`: low-risk pre/staging validation, checkout/reset to a known ref, pull/restart, rollback, or restart-only.
- `fast_hotfix`: urgent master/shared-target action requested by the user with an exact ref and minimal verification.
- `shared_lane`: merge to or push a shared branch such as `pre` or `master`, conflict work, or lineage-sensitive handoff.
- `controlled_ops`: config/env/schema/table/external-service, production-like, data-loss, rollback-behavior, or operational-risk changes.

Fast lanes do not require a `deployer` subagent by default. Use a subagent only when the project skill marks the action as high risk, broad, ambiguous, or operationally complex. `shared_lane` and `controlled_ops` should use the project deploy/predeploy skill and may require `deployer`, `reviewer`, `merge-workflow`, or `merge-safety`.

If intent is ambiguous, ask once for either lane selection or current-turn authorization. Do not expand into multi-question planning unless source evidence cannot identify the target ref or rollback point.

## Ledger

Start with:

```text
workflow_state.state_version=1
workflow_state.task_id=<stable task id>
workflow_state.current_mode=split_workflow
workflow_state.task_class=predeploy
workflow_state.selected_skill=predeploy
workflow_state.stage=planning
workflow_state.required_roles=deployer
workflow_state.lane=<fast_pre|fast_hotfix|shared_lane|controlled_ops>
workflow_state.phase=planning
workflow_state.intent=<user intent summary>
workflow_state.action=<checkout|reset|restart|rollback|merge|push|deploy|verify>
workflow_state.target_env=<environment or host>
workflow_state.target_ref=<branch|sha|tag>
workflow_state.writes_shared_branch=<true|false>
workflow_state.risk_flags=<comma-separated flags or empty>
workflow_state.spawned_roles=
workflow_state.waited_roles=
workflow_state.closed_roles=
workflow_state.role_assignments={}
workflow_state.role_evidence={}
workflow_state.verification_commands=
workflow_state.reviewer_result=
workflow_state.exception_reason=
workflow_state.retained_subagents_reason=
workflow_state.files_changed=
workflow_state.final_status=
workflow_state.elapsed_step=
workflow_state.asked_user_count=0
workflow_state.subagent_count=0
workflow_state.rollback_plan=<captured rollback branch/head/config backup or pending>
```

For `fast_pre` and `fast_hotfix`, set `required_roles=` unless a subagent is actually needed. For `shared_lane` and `controlled_ops`, keep the project-required roles.

## Flow

1. Confirm the compact input card: environment/host, ref/version, allowed actions, forbidden actions, config keys, restart permission, verification marker, and rollback plan.
2. Use project-specific deploy/predeploy skill for procedure.
3. Spawn `deployer` only after current-turn authorization and only when the selected lane requires an operational subagent.
4. Prefer dry-run/read-only checks before mutation.
5. Record concrete commands and verification evidence in `workflow_state.verification_commands`.
6. Record `phase`, `elapsed_step`, `asked_user_count`, and `subagent_count` when available.
7. Close any spawned subagent or record `retained_subagents_reason`.

## Duplicate Evidence Rule

After a subagent returns evidence, the main agent should use that result. If the main agent rechecks the same broad evidence, record `workflow_state.sampled_recheck_reason=<short reason>` and keep the recheck representative rather than repeating the full investigation.

## Completion

Report target, ref/version, actions taken, verification, rollback path, and remaining risk. Do not imply deployment happened if only dry-run or read-only checks ran.
