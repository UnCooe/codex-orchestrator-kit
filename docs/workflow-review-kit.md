# Codex Workflow Review Kit

This repository provides the default Codex orchestrator workflow layer and an optional workflow review kit layer. It is not the project being reviewed.

The kit gives recurring automations and local agents a stable way to collect privacy-preserving facts, apply reviewer standards, and produce triage summaries across many projects.

## Layer Model

### Built-In Workflow Layer

The default install surface owns global Codex workflow capabilities:

- hook event capture
- orchestrator skill guidance
- subagent role definitions
- subagent workflow self-observability

These capabilities should be useful without knowing anything about a user's business project.

### Review Kit Layer

The review kit owns shared infrastructure:

- session fact extraction
- privacy redaction
- offline report generation
- reviewer documentation
- triage output conventions
- installer and portability behavior

The review kit should not encode one monitored project as the default truth. It should provide reusable review capability.

### Reviewer Layer

A reviewer is a focused review capability with a documented rubric.

Default reviewer:

- `subagent-workflow`: delegation quality, spawn/wait/close balance, unclosed agents, heavy-work delegation, and broad-scan behavior.

Optional external reviewer packs:

- `turnstate`: main-flow closure, verification, tool balance, token/reasoning signals, and large-output noise.

Future reviewers can be added without changing the meaning of the kit. Examples: test discipline, docs drift, notification health, CI regression, project context quality.

### Project Profile Layer

Each monitored project can provide local context and review preferences.

Supported v1 files:

- `.codex/project-context.md`: goal, phase, modules, commands, acceptance standards, known risks.
- `.codex/review-profile.toml`: enabled reviewers, phase, reminder policy, action policy, known noisy signals, protected scopes.

These files belong in the monitored project. `CODEX_HOME` stores runtime state, reports, automation memory, and installed tools; it should not become the source of project-specific truth.

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

Subagent workflow is the built-in default reviewer because it validates the kit's own orchestration behavior.
Turnstate is an optional external reviewer pack because it belongs to a monitored external project. The durable goal is calibrated, cross-project workflow review without making any one external project part of the core install surface.
