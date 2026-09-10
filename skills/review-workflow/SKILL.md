---
name: review-workflow
description: Use for review-only requests, PR/diff review, risk review, destructive critique, or missing-test assessment where reviewer and optional critic/researcher roles gather evidence but must not write code.
---

# Review Workflow

Use this skill when the user asks for review, critique, audit, risk analysis, or missing-test assessment without code changes.

## State Boundary

Use runtime subagent lifecycle events, reviewer output, and hook telemetry as evidence. Do not expose a long `workflow_state.*` ledger in user-visible replies.

If a hook requires an explicit split-workflow declaration before heavy review work, keep it minimal:

```text
workflow_state.current_mode=split_workflow
workflow_state.selected_skill=review
workflow_state.required_roles=reviewer
```

## Flow

1. Define review scope: diff, branch, files, architecture, or acceptance criteria.
2. Use `researcher` only when evidence discovery is broad.
3. Use `reviewer` for correctness, regression, contract, security, and missing-test findings.
4. Use `critic` only for destructive challenge of assumptions or risky designs.
5. Keep all review roles read-only.
6. Let runtime lifecycle events track spawn/wait/close; keep only concise notes needed for final findings and review outcome.
7. Do not spawn `implementer`, `test_writer`, or `deployer` in review-only mode.

## Output

Lead with findings ordered by severity. Use P0/P1/P2 and Needs Context. If no issues are found, say that clearly and mention residual test or evidence gaps.
