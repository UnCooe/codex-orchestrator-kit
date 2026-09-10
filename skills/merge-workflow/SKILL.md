---
name: merge-workflow
description: Use for git merge, rebase, cherry-pick, pull, branch sync, PR merge, or merge conflict work; route to merge-safety/merge guard when available, use conflict_resolver only for actual conflicts, and keep the main agent responsible for intent and acceptance.
---

# Merge Workflow

Use this as a routing card, not a step-by-step merge manual. Let project tools such as `$merge-safety` / `merge_guard.py` own deterministic gates, next actions, evidence recording, and readiness checks.

## Route

- Use for `merge`, `rebase`, `cherry-pick`, `pull`, branch sync, PR merge, shared-branch promotion, or conflict resolution.
- If the project has merge safety, resolve and fully load `../merge-safety/SKILL.md` before merge planning or mutation; use it as the source of truth for guard state and next action.
- If there are conflict markers or unresolved merge files, delegate only those files to `conflict_resolver`.
- Use `test_runner` for focused validation evidence.
- Use `reviewer` only when the integration affects shared branches, broad scope, lineage, or risky behavior.

## Main-Agent Duties

- Lock user intent before mutation: target/source refs, operation, allowed scope, forbidden changes, acceptance, and unknowns.
- Stop and ask when product, compatibility, default, edge-case, or data-loss choices cannot be decided from source evidence.
- Do not use ordinary `implementer` for conflict resolution.
- Do not do business refactors, broad cleanup, whole-file ours/theirs, or fallback behavior changes unless explicitly authorized.
- Treat guard output as authoritative. Follow its `next_action` instead of re-deriving the workflow from this skill.

## Completion

Claim merge readiness only after the guard/tooling says the result is ready, validation evidence is recorded, and any required review has no blocking findings. Report refs, validation, review outcome, remaining risk, and whether the merge intent was cleared or intentionally retained.
