---
name: orchestrator
description: Default Codex orchestration workflow for complex tasks, code implementation, repo scanning, research, testing, review, risk analysis, architecture or design comparison, and destructive critique. Use when Codex may need subagents or parallel agent work to preserve the main context window, especially for noisy investigation, multi-file edits, test planning, PR review, dependency/API research, log analysis, or validation.
---

# Orchestrator

Use this skill to keep the main Codex thread focused on decisions while delegating noisy work to subagents.

## Decision Gate

Before broad exploration, long command output, or file edits, decide whether the task is heavy.

Delegate by default for:
- code implementation beyond a trivial single-file edit
- repo-wide or cross-module reading
- research, dependency/API verification, log analysis, or document scanning
- test case design, regression analysis, PR review, risk review, or destructive critique
- architecture, migration, refactor, or option comparison

Do not delegate when:
- the task is a simple command or a tiny single-file change
- the user explicitly asks the main agent to work directly
- subagent startup cost is clearly higher than the task
- the next critical-path step is blocked on information the main agent can obtain faster

When not delegating a non-trivial task, state the reason briefly.

## Delegation Patterns

Use available built-in subagent types when the runtime exposes them. If custom agents are not loaded, use the closest built-in type and put the role contract directly in the prompt:
- `researcher`: use `researcher`/`explorer` for read-only repo or documentation exploration.
- `implementer`: use `implementer`/`worker` for targeted code changes with an explicit write scope.
- `test_designer`: use `test_designer` for independent test cases, edge cases, and acceptance criteria.
- `reviewer`: use `reviewer` for correctness, regression, security, and missing-test review.
- `critic`: use `critic` for destructive review of assumptions, architecture, tradeoffs, and likely failure modes.

Always include the role contract in the subagent prompt. Do not rely on custom TOML being loaded by the runtime.
When delegating, read `references/subagent-prompts.md` and adapt the shortest matching template instead of writing a fresh prompt from scratch.

For coding work, separate roles:
1. Main agent confirms goal, boundaries, and acceptance criteria.
2. `researcher` maps relevant code when the code path is unclear.
3. `implementer` edits only the assigned files or module.
4. `test_designer` defines tests without trusting the implementation.
5. `reviewer` or `critic` challenges the result before final acceptance.

For write-heavy work, avoid parallel edits to the same files. If ownership cannot be cleanly split, use one implementer and keep other subagents read-only.

## Subagent Lifecycle Gate

Treat subagent slots as scarce.

For every subagent:
- record the agent id, role, and task purpose in the main thread
- wait only when the result is needed for the next decision or final acceptance
- close the subagent immediately after its result is collected, unless it is needed for the same continuous workflow
- if retaining a subagent, state the reason, next task, and expected close point

Before final response, include either `Subagents: closed` or `Subagents: retained because ...`.

## Main Thread Contract

Keep subagent prompts self-contained and narrow. Tell each subagent:
- the exact task and scope
- whether it may edit files
- which files or modules it owns, if editing
- that other agents may also be working and it must not revert unrelated edits
- the required concise return format

Ask subagents to return only:
- conclusion
- evidence
- risks
- alternatives
- recommended action
- changed files, when they edited files

Do not paste raw logs, full file dumps, or long reasoning into the main thread. Summarize and cite only the evidence needed for the decision.

## Acceptance Gate

Before final response:
- verify the original request is satisfied
- mention tests/checks run, or why they were not run
- include review/critique outcome for heavy work, or why it was omitted
- include `Subagents: closed` or `Subagents: retained because ...`
- call out remaining assumptions, risks, or rollback notes
- close unused subagents when the workflow is complete
