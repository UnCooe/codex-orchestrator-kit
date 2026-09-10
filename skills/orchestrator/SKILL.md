---
name: orchestrator
description: Default Codex orchestration workflow for complex tasks, code implementation, repo scanning, research, testing, review, risk analysis, architecture or design comparison, and destructive critique. Use when Codex may need subagents or parallel agent work to preserve the main context window, especially for noisy investigation, multi-file edits, test planning, PR review, dependency/API research, log analysis, or validation.
---

# Orchestrator

Use this skill as the routing skeleton. Keep the main Codex thread responsible for mode selection, delegation, synthesis, and final acceptance; push scenario details into the selected workflow skill.

## Mode Gate

Choose one mode before broad reading, long output, or multi-file edits:

- `tiny_direct`: simple command, tiny localized read, or small direct edit where delegation cost is higher than value.
- `split_workflow`: non-trivial implementation, tests, review, merge conflict, predeploy, broad research, or risky design/architecture work.

For heavy work that needs an explicit state gate before tool use, use the smallest useful declaration:

```text
workflow_state.current_mode=split_workflow
workflow_state.selected_skill=feature
workflow_state.required_roles=
```

Runtime subagent lifecycle events, hook telemetry, tool results, and concise role summaries are the source of truth. Do not maintain or expose a long `workflow_state.*` ledger in normal user-visible text.

For `tiny_direct` heavy exceptions, state a short mode reason only when a hook asks for one.

After clear current-turn user authorization, pushing an already verified single unprotected feature/candidate branch is a `tiny_direct` follow-on: reuse existing evidence, do not reload the workflow, rebuild the plan, spawn subagents, or rerun the full test suite. Hooks provide a silent fallback for dangerous push shapes, not the normal authorization layer.

## Skill Routing

Soft-owner rule: if an exact project-specific skill is already selected by Codex for the current phase, let that project skill be the direct owner. Do not force project work through a generic sibling workflow, and do not reload, load again, or duplicate-load an already selected project owner. Orchestrator intervenes only when the task is ambiguous, cross-phase, or needs coordination across roles or workflows.

When no exact project-specific owner is already selected, choose the narrowest workflow skill from this SKILL file's own directory. Resolve exactly one primary sibling per phase:

- Feature, bugfix, refactor, or test changes: `../feature-workflow/SKILL.md`. New feature work defaults to a feature branch from latest `origin/master`; follow-up edits must stay on the matching feature branch unless the user explicitly authorizes another branch.
- Review, audit, diff or PR inspection, or risk assessment: `../review-workflow/SKILL.md`.
- Merge, rebase, cherry-pick, pull, branch sync, or conflict resolution: `../merge-workflow/SKILL.md`.
- Pre or staging work, deploy, rollback, or restart: `../predeploy-workflow/SKILL.md`; deployer requires current-turn authorization.

Read the selected leaf skill completely before any task action. Load that file directly; do not emit `$leaf` and wait for a second automatic injection. Do not pre-read, pre-load, bulk read, batch read, or read all sibling workflow skills. For mixed tasks, route and execute phase by phase with exactly one mutation owner in the same phase; a merge phase may subsequently load merge safety as required by the merge owner. Fail closed on missing or unreadable skill paths, report the exact path, and stop. If the user explicitly names a leaf skill, bypass selection and load that leaf directly. `tiny_direct` loads no leaf skill. Broad research stays orchestrator-owned and should be coordinated through a researcher; external `$codebase-research` is optional when it fits the request.

Project-specific rules must come from project profiles or project skills. Do not create globally project-bound roles for a generic workflow.

## Outcome-Based Delegation

Choose named roles only when they add material value to the user's outcome. Required outcomes are one implementation owner for writes, proportional verification evidence, proportional independent review, and branch discipline; these outcomes do not require every capability to be a separate subagent.

The parent must put a short, explicit role contract in every work package sent to a child. `references/subagent-prompts.md` is the runtime source for that contract: name the role, evidence lane, objective, evidence scope, write boundary, artifact, done condition, stop condition, and no-delegation rule. `agents/*.toml` are optional catalog templates for humans and installers; do not assume Codex injects them, and never use their presence as evidence of a child's runtime identity. Work packages must never use `fork_turns="all"`; use `fork_turns="none"` or a bounded turn count so the work package, not full parent context, is the controlling input.

Delegate only when a child provides context isolation, independent evidence, specialist judgment, or a separable boundary. Local bounded work stays root-only when the main thread can keep enough context and produce direct evidence without losing quality.

Evidence lane sizing:

- One noisy or specialist lane means one child.
- Two independent lanes means two children with different evidence ownership.
- Evidence lane: name the lane, owner role, evidence scope, and artifact before spawning.

Wave and active-child budget:

- `tiny_direct`: 0 active children.
- Ordinary phases reuse matching children and get one spawn wave.
- Broad read-only architecture, dependency, endpoint, log, or source-backed investigation uses a first wave of 1-3 named lanes.
- At most one named gap lane may be added after first-wave synthesis for evidence that is still missing.
- There is no task-wide total child cap; the Orchestrator safety ceiling is maximum 4 active children.
- A project skill lane budget replaces the generic wave shape and does not stack with it; the maximum 4 active-child ceiling still applies.

Delegation depth:

- Default depth 1.
- Broad evidence lanes are root children.
- Nested delegation is denied by default, and no child may exist only for re-delegation or relay.
- A root-authorized depth 2 research exception is allowed only when the parent grants a bounded read-only research exception with a clear stop condition.
- Subagent prompt files must say no subagent spawning unless the parent explicitly grants that bounded research exception.

Phase and reuse limits:

- Use one role+scope child per phase.
- Reuse a relevant child when the role and scope still match, with at most two substantive followups.
- Use no duplicate evidence-lane owners; each lane needs a distinct owner and evidence scope.
- Every child result must be consumed, validated, or explicitly rejected before a second wave or task completion.
- After delegating a broad evidence surface, the main Agent consumes the artifact and samples only targeted source for acceptance; do not repeat the child's full scan in the main context.
- Prefer the custom researcher for ordinary orchestrated research. Use the built-in explorer only for an explicit project-skill need or an isolated model diagnostic; do not globally override explorer.

Completion gates:

- A fixed role roster is not a completion gate.
- Spawn, token, and API reductions are not completion gates; completion comes from delivered behavior, evidence, review, and unresolved risk.

Writer boundaries:

- The main agent counts as a writer.
- Allow one writer per named boundary.
- Tests and production may split only when the files are disjoint and the write boundary is explicit.
- A work package must be short and explicit: role, evidence lane, objective, evidence scope, write boundary, artifact, done, stop, and no delegation.

Loop and retry limits:

- Max two material revisions before synthesis.
- If the same root failure remains after two changed strategies, synthesize findings and options instead of repeating the loop.
- Idempotent reads may retry maximum 2 times.
- Writes require a state-check before retry.
- Wait count is diagnostic only; prefer event-driven batch waits and do not gate success on a raw wait count.
- Do not add ETA gates or stop conditions based only on changed-file counts.

Role examples:

- Use `requirements_interpreter` when intent, data flow, affected contracts, or acceptance criteria are unclear.
- Use `researcher` when the code path is unknown, crosses modules, or needs source-backed mapping before implementation.
- Use `test_writer` only when test files, fixtures, or test helpers need to be added or changed.
- Use `critic` for high-risk architecture, migrations, broad refactors, deployment risk, or explicitly requested destructive critique.
- Use `deployer` for predeploy/deploy only after current-turn authorization.
- `conflict_resolver` resolves merge conflicts only and does not satisfy ordinary implementation work.

Branch boundary:

- `$feature-workflow` owns development on the correct feature branch.
- `$merge-workflow` owns promotion to shared branches such as `master`, `main`, `pre`, or `test`.
- Do not silently perform feature or follow-up edits on shared/integration/other-feature branches just because that is the current checkout.

When delegating, read `references/subagent-prompts.md` and fill the compact work-package schema.

## State Discipline

Prefer runtime evidence over text fields:

- Use subagent start/stop and tool events for lifecycle.
- Use validation command output for verification.
- Use reviewer summaries for review outcome.
- Use the final response for human-readable synthesis only.
- If retaining any subagent across turns, explain it in one short sentence.

Hooks should consume runtime evidence first. Text `workflow_state` declarations are a lightweight gate hint, not the primary completion boundary.

## Output Contract

Keep final replies user-facing. Do not expose internal role registration, long hook instructions, `WORK_CONTRACT` blocks, or `ACCEPTANCE:` blocks unless the user explicitly asks for workflow detail.

Before final response:

- verify the user request is satisfied
- report tests/checks run or why they were not run
- include review outcome for heavy work
- ensure finished subagents reached a terminal/final state, or explain a retained one in one short sentence
