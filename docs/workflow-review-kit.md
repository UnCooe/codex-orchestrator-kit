# Codex Workflow Review Kit

This repository is a workflow review kit for Codex runs. It is not the project being reviewed.

The kit gives recurring automations and local agents a stable way to collect privacy-preserving facts, apply reviewer standards, and produce triage summaries across many projects.

## Layer Model

### Kit Layer

The kit owns shared infrastructure:

- hook event capture
- session fact extraction
- privacy redaction
- offline report generation
- reviewer documentation
- triage output conventions
- installer and portability behavior

The kit should not encode one monitored project as the default truth. It should provide reusable review capability.

### Reviewer Layer

A reviewer is a focused review capability with a documented rubric.

Current reviewers:

- `turnstate`: main-flow closure, verification, tool balance, token/reasoning signals, and large-output noise.
- `subagent-workflow`: delegation quality, spawn/wait/close balance, unclosed agents, heavy-work delegation, and broad-scan behavior.

Future reviewers can be added without changing the meaning of the kit. Examples: test discipline, docs drift, notification health, CI regression, project context quality.

### Project Profile Layer

Each monitored project can provide local context and review preferences.

Supported v1 files:

- `.codex/project-context.md`: goal, phase, modules, commands, acceptance standards, known risks.
- `.codex/review-profile.toml`: enabled reviewers, phase, reminder policy, action policy, known noisy signals, protected scopes.

If a project profile is missing, reviewers use the default rubric. Missing context should reduce confidence; it should not automatically make a finding high risk.

### Automation Instance Layer

An automation is only a trigger. It chooses a time window, runs reviewers, compares evidence, and emits visible triage.

Automations should:

- read reviewer rubrics before deciding severity
- read project context when available
- distinguish new problems from repeated background noise
- avoid exposing full prompts, full tool output, full assistant messages, or local absolute paths
- default to observe/triage/proposal, not automatic mutation

## Action Policy

Reviewers use three action levels:

- `observe`: record the signal, no interruption needed.
- `triage`: summarize the issue and ask for human judgment.
- `proposal`: prepare a safe, reviewable fix plan or patch, but do not apply it automatically.

Direct automated fixes are out of scope for v1.

## Design Boundary

Turnstate and subagent workflow are the first reviewers, not the final product boundary. The durable goal is calibrated, cross-project workflow review.

