---
name: feature-workflow
description: Use for non-trivial feature, bugfix, refactor, or test work where Codex should split requirements, implementation, independent test design or writing, validation, and review across generic subagent roles.
---

# Feature Workflow

Use this skill for non-trivial code changes. The main agent owns orchestration and final acceptance.

## State Boundary

Use Codex subagent lifecycle events, hook telemetry, tool results, and concise role summaries as the source of truth. Do not make the user-visible final reply carry a long machine ledger.

For heavy work that needs an explicit mode declaration before tool use, keep it minimal:

```text
workflow_state.current_mode=split_workflow
workflow_state.selected_skill=feature
workflow_state.required_roles=
```

Only add compact workflow fields when they resolve an actual gate or ambiguity. Do not echo `spawned_roles`, `waited_roles`, `closed_roles`, or other lifecycle fields just to satisfy formatting.

Use `test_writer` only when tests or fixtures must be added or changed. Verification evidence remains required; select an independent `test_runner` only when it materially improves confidence. If validation cannot run, record the blocked command and reason.

## Feature Branch Discipline

- New feature work defaults to a new feature branch created from the latest `origin/master`.
- Before production code or test edits, identify `base_ref`, `current_branch`, `feature_branch`, and branch discipline in the working notes or subagent prompt when relevant.
- For follow-up changes to an existing feature, work on that feature's branch. If the matching branch cannot be determined from source evidence or user context, stop and ask instead of editing the current branch.
- Do not make feature or follow-up edits directly on `master`, `main`, `pre`, `test`, release branches, integration branches, or another feature branch unless the user explicitly authorizes that exact branch and reason in the current turn.
- If the current worktree has uncommitted changes, classify whether they belong to the same feature branch before editing. Do not mix unrelated user changes into the feature branch.
- Feature workflow owns development on the correct feature branch. Promotion to `master`, `pre`, `test`, or another shared branch belongs to `$merge-workflow` / `$merge-safety`, not to an implicit feature edit.
- After clear current-turn user authorization, pushing an already verified single unprotected feature/candidate branch is a direct follow-on. Reuse existing branch, validation, and review evidence; do not reload the workflow, rebuild the plan, spawn subagents, or rerun the full test suite just for that push. Hooks silently fall back only for dangerous shapes.

## Outcome-Based Role Triggers

Feature and bugfix default `required_roles` are empty. Pick roles only when they add material value.

Delegate only when a child protects the main Agent through context isolation, produces independent evidence, supplies specialist judgment, or owns a separable boundary. Local bounded work may remain root-only. Give each child one unique evidence lane, then consume, validate, or explicitly reject its result; role count is not a goal.

Required outcomes still apply:

- One implementation owner for each write boundary. The main agent counts as a writer.
- Verification evidence is proportional to risk and blast radius.
- Independent review is proportional to shared contracts, data loss risk, security risk, and user-visible behavior.
- Branch discipline must be established before production or test edits.

Explicit preserve, unchanged, no-expansion, or only-change acceptance requires a smallest source-backed invariant matrix before implementation. Reuse an already selected test or research lane, or the main agent, to establish those invariants; do not create a mandatory new role. Hand the matrix to the sole writer at the named write boundary.

- Use `requirements_interpreter` only when intent, data flow, affected contracts, or acceptance criteria are unclear.
- Use `researcher` only when the code path is unknown, crosses modules, or needs source-backed mapping before implementation.
- Use `test_writer` only when test files, fixtures, or test helpers need to be added or changed.
- Use `critic` only for high-risk architecture, migrations, broad refactors, deployment risk, or explicit destructive critique. Do not include it in the default small feature loop.

## Review Loop Budget

- Default to at most one implement/review repair loop.
- Allow at most one genuine same-reviewer P0/P1 corrective re-review after targeted repair.
- Redundant or speculative re-review is blocked; open review loops remain capped.
- If P0/P1 findings remain after one repair loop, stop and report the remaining findings, risk, and options instead of continuing indefinitely.
- Run another repair loop only after explicit user direction or a clearly bounded continuation from the parent agent.
- Every subagent should return minimal sufficient output, not long documentation.

## Flow

1. Build a Requirement Contract. Use `requirements_interpreter` when intent, data flow, or acceptance is unclear.
2. Use `researcher` when the code path is unknown or crosses modules.
3. Establish branch discipline before edits: fetch/check the default branch when needed, create a feature branch from latest `origin/master` for new work, or switch to the matching feature branch for follow-up work.
4. Use independent test design only when the change risk warrants it; keep implementation details out of that plan.
5. Assign the implementation owner a narrow write scope, the required branch, and acceptance criteria.
6. Use `test_writer` when tests must be added or changed, then collect focused validation evidence.
7. Use reviewer/critic capability when an independent check materially reduces regression, security, or contract risk.
8. Let runtime lifecycle events track spawn/wait/close. Keep local notes concise enough to synthesize final validation, review, branch, and risk evidence.

## Isolation Rules

- `test_designer` may see Requirement Contract, existing test style, public interfaces, schemas, and fixtures only.
- `test_designer` must not read implementer diff, summary, patch, or changed-code explanation.
- Prefer spawning `test_designer` before implementation edits. Otherwise use a clean base snapshot/worktree or pass only allowed public files.
- `test_designer` must return `sources_read` and `implementation_diff_visible=false`; if it detects implementation diff or patch content, it must stop and report violation.
- `test_writer` writes tests from the test plan and must not weaken assertions to match implementation details.
- `reviewer` is read-only and must not patch code.

## Completion

Before final response:

- validation evidence records concrete commands or explicit not-run blockers
- reviewer outcome is known for heavy work
- branch evidence reflects the actual branch used; if branch setup was blocked, do not report success
- final response stays user-facing: changed files, validation, review outcome, and remaining risk
