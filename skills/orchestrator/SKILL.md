---
name: orchestrator
description: Default Codex orchestration workflow for complex tasks, code implementation, repo scanning, research, testing, review, risk analysis, architecture or design comparison, and destructive critique. Use when Codex may need subagents or parallel agent work to preserve the main context window, especially for noisy investigation, multi-file edits, test planning, PR review, dependency/API research, log analysis, or validation.
---

# Orchestrator

Use this skill as the routing skeleton. Keep the main Codex thread responsible for mode selection, delegation, synthesis, and final acceptance; push scenario details into the selected workflow skill.

Current role contract: `subagent-role-v2.0`. Keep `workflow_state.state_version=1`.

## Mode Gate

Choose one mode before broad reading, long output, or multi-file edits:

- `tiny_direct`: simple command, tiny localized read, or small direct edit where delegation cost is higher than value.
- `split_workflow`: non-trivial implementation, tests, review, merge conflict, predeploy, broad research, or risky design/architecture work.

For heavy work, maintain this machine-readable ledger in the assistant transcript:

```text
workflow_state.state_version=1
workflow_state.task_id=<stable task id>
workflow_state.turn_id=<turn/session id when available>
workflow_state.created_at=<ISO-8601>
workflow_state.updated_at=<ISO-8601>
workflow_state.current_mode=split_workflow
workflow_state.task_class=feature
workflow_state.selected_skill=feature
workflow_state.stage=planning
workflow_state.required_roles=implementer,test_designer,test_runner,reviewer
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
```

For `tiny_direct` heavy exceptions, set `workflow_state.exception_reason=<why delegation is not useful>` and `workflow_state.mode_reason=<same short reason>` for older hooks.

## Skill Routing

Select the narrowest workflow skill:

- Feature or bugfix implementation: use `$feature-workflow`.
- Merge, rebase, cherry-pick, branch sync, or conflict resolution: use `$merge-workflow` and any project merge-safety skill.
- Review-only request: use `$review-workflow`.
- Predeploy/deploy verification: use `$predeploy-workflow`; deployer requires current-turn authorization.

Project-specific rules must come from project profiles or project skills. Do not create globally project-bound roles for a generic workflow.

## Role Defaults

Required core roles for feature work:

- `implementer`
- `test_designer`
- `test_runner`
- `reviewer`

Optional roles:

- `requirements_interpreter`
- `researcher`
- `test_writer`
- `critic` for high-risk architecture, migrations, broad refactors, deployment risk, or explicitly requested destructive critique
- `deployer` for predeploy/deploy only after current-turn authorization

Special role:

- `conflict_resolver` resolves merge conflicts only and does not satisfy `implementer`.

When delegating, read `references/subagent-prompts.md` and adapt the shortest matching template. Always put the role contract in the prompt; do not assume custom TOML is loaded.

## Ledger Discipline

Update `workflow_state` as work progresses:

- after spawn: append the role to `spawned_roles`
- after wait/result consumption: append the role to `waited_roles`
- after close: append the role to `closed_roles`
- after validation: append concrete command objects or not-run blockers to `verification_commands`
- after review: set `reviewer_result=pass|fail|needs_context:<short reason>`
- after each role result: update `role_evidence.<role>` with concise result, status, and summary path if available
- after file edits: update `files_changed`
- before completion: set `stage=done` and `final_status=pass|fail|blocked|needs_context|not_run`
- if retaining any subagent across turns: set `retained_subagents_reason=<why, next task, expected close point>`

Hooks consume this ledger as evidence. Natural-language claims are not the primary completion boundary.

## Output Contract

Keep final replies user-facing. Do not expose internal role registration, long hook instructions, `WORK_CONTRACT` blocks, or `ACCEPTANCE:` blocks unless the user explicitly asks for workflow detail.

Before final response:

- verify the user request is satisfied
- report tests/checks run or why they were not run
- include review outcome for heavy work
- close finished subagents or explain a retained one in one short sentence
