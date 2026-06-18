---
name: review-workflow
description: Use for review-only requests, PR/diff review, risk review, destructive critique, or missing-test assessment where reviewer and optional critic/researcher roles gather evidence but must not write code.
---

# Review Workflow

Use this skill when the user asks for review, critique, audit, risk analysis, or missing-test assessment without code changes.

## Ledger

Start with:

```text
workflow_state.state_version=1
workflow_state.task_id=<stable task id>
workflow_state.current_mode=split_workflow
workflow_state.task_class=review
workflow_state.selected_skill=review
workflow_state.stage=planning
workflow_state.required_roles=reviewer
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

## Flow

1. Define review scope: diff, branch, files, architecture, or acceptance criteria.
2. Use `researcher` only when evidence discovery is broad.
3. Use `reviewer` for correctness, regression, contract, security, and missing-test findings.
4. Use `critic` only for destructive challenge of assumptions or risky designs.
5. Keep all review roles read-only.
6. Update `workflow_state` after each spawn, wait, close, and review result.
7. Do not spawn `implementer`, `test_writer`, or `deployer` in review-only mode.

## Output

Lead with findings ordered by severity. Use P0/P1/P2 and Needs Context. If no issues are found, say that clearly and mention residual test or evidence gaps.
