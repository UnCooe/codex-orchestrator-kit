---
name: merge-workflow
description: Use for git merge, rebase, cherry-pick, pull, branch sync, PR merge, or merge conflict work; route conflict edits to conflict_resolver, require merge-safety intent lock where available, then validate and review with workflow_state ledger tracking.
---

# Merge Workflow

Use this skill for integration and conflict work. If a project has a merge-safety skill, use it before mutating refs or files.

## Ledger

Start with:

```text
workflow_state.state_version=1
workflow_state.task_id=<stable task id>
workflow_state.current_mode=split_workflow
workflow_state.task_class=merge
workflow_state.selected_skill=merge
workflow_state.stage=planning
workflow_state.required_roles=conflict_resolver,test_runner
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

1. Lock merge intent: target/source refs, operation mode, allowed paths, forbidden changes, acceptance, and unknowns.
2. Stop for user input when conflict resolution depends on product, compatibility, default, edge-case, or data-loss choices not discoverable from source.
3. Use `conflict_resolver` for conflict files only. It cannot satisfy the `implementer` role.
4. Use `test_runner` for focused merge/conflict validation.
5. Use `reviewer` for read-only review when conflict scope or lineage risk warrants it.
6. Update `workflow_state` after each spawn, wait, close, validation command, and review result.

## Boundaries

- Do not use ordinary `implementer` for unresolved merge conflicts.
- Do not do business refactors while resolving conflicts.
- Do not claim merge readiness until conflict markers, diff scope, lineage, validation, and review are checked.
