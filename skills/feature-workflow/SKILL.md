---
name: feature-workflow
description: Use for non-trivial feature, bugfix, refactor, or test work where Codex should split requirements, implementation, independent test design or writing, validation, and review across generic subagent roles with workflow_state ledger tracking.
---

# Feature Workflow

Use this skill for non-trivial code changes. The main agent owns orchestration and final acceptance.

## Ledger

Start with:

```text
workflow_state.state_version=1
workflow_state.task_id=<stable task id>
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

Use `test_writer` only when tests or fixtures must be added or changed. `test_runner` remains required for verification evidence; if validation cannot run, record a not-run command object with the reason.

## Role Triggers

Default required roles for feature and bugfix work:

- `implementer`
- `test_designer`
- `test_runner`
- `reviewer`

Conditional roles:

- Use `requirements_interpreter` only when intent, data flow, affected contracts, or acceptance criteria are unclear.
- Use `researcher` only when the code path is unknown, crosses modules, or needs source-backed mapping before implementation.
- Use `test_writer` only when test files, fixtures, or test helpers need to be added or changed.
- Use `critic` only for high-risk architecture, migrations, broad refactors, deployment risk, or explicit destructive critique. Do not include it in the default small feature loop.

## Review Loop Budget

- Default to at most one implement/review repair loop.
- If P0/P1 findings remain after one repair loop, stop and report the remaining findings, risk, and options instead of continuing indefinitely.
- Run another repair loop only after explicit user direction or a clearly bounded continuation from the parent agent.
- Every subagent should return minimal sufficient output, not long documentation.

## Flow

1. Build a Requirement Contract. Use `requirements_interpreter` when intent, data flow, or acceptance is unclear.
2. Use `researcher` when the code path is unknown or crosses modules.
3. Send the Requirement Contract to `test_designer` before exposing implementation details.
4. Assign `implementer` a narrow write scope and acceptance criteria.
5. Use `test_writer` to write tests from the test plan when needed, then use `test_runner` to run focused validation.
6. Use `reviewer` for read-only correctness, regression, contract, and missing-test review.
7. Update `workflow_state` after each spawn, wait, close, validation command, and review result.

## Isolation Rules

- `test_designer` may see Requirement Contract, existing test style, public interfaces, schemas, and fixtures only.
- `test_designer` must not read implementer diff, summary, patch, or changed-code explanation.
- Prefer spawning `test_designer` before implementation edits. Otherwise use a clean base snapshot/worktree or pass only allowed public files.
- `test_designer` must return `sources_read` and `implementation_diff_visible=false`; if it detects implementation diff or patch content, it must stop and report violation.
- `test_writer` writes tests from the test plan and must not weaken assertions to match implementation details.
- `reviewer` is read-only and must not patch code.

## Completion

Before final response:

- all required roles are spawned, waited, and closed, or retained with a reason
- `verification_commands` records concrete commands or explicit not-run blockers
- `reviewer_result` is `pass`, `fail`, or `needs_context:<reason>`
- `role_evidence` records at least `test_designer`, `test_runner`, and `reviewer` results
- `final_status` is set before completion
