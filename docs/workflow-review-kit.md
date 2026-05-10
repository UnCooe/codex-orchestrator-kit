# Codex Workflow Review Kit

This repository provides the default Codex orchestrator workflow layer and an optional workflow review kit layer.
It is not the project being reviewed.

Its purpose is harness-oriented: give recurring automations and local agents a stable way to decide whether Codex workflow behavior is trustworthy, and if not, whether the fix belongs in the harness or in the monitored project.

## Product Definition

The product is a reusable workflow-review substrate for Codex.
It is not a bundled reviewer for one specific business repo, and it is not an automation that gets to define severity by itself.

What this kit owns:

- portable orchestrator prompts, hooks, and stop-gate contracts
- privacy-preserving evidence extraction
- report summaries and trend comparison
- reviewer rubrics and triage staging

What this kit does not own:

- project-specific business truth
- project-specific reviewer policy unless a project explicitly installs a reviewer pack
- severity decisions based on raw counts alone

## Layer Model

### Built-In Workflow Layer

The default install surface owns global Codex workflow capabilities:

- hook event capture
- orchestrator skill guidance
- subagent role definitions
- subagent workflow self-observability

These capabilities should be useful without knowing anything about a user's business project.
They exist to keep delegation, closure, and review discipline stable across projects.

### Review Kit Layer

The review kit owns shared infrastructure:

- session fact extraction
- privacy redaction
- offline report generation
- reviewer documentation
- triage output conventions
- installer and portability behavior

The review kit should not encode one monitored project as the default truth. It should provide reusable review capability.
Its reports should help answer "what should be fixed next" rather than only "which count is high".
It owns evidence normalization, including whether acceptance footer signals were present, partial, complete, improving, or regressing.

### Reviewer Layer

A reviewer is a focused review capability with a documented rubric.

Default reviewer:

- `subagent-workflow`: delegation quality, spawn/wait/close balance, unclosed agents, heavy-work delegation, and broad-scan behavior.

Optional external reviewer packs live outside the core kit, usually in the monitored project's `.codex/reviewer-packs/` directory or in another local checkout.

Future reviewers can be added without changing the meaning of the kit. Examples: test discipline, docs drift, notification health, CI regression, project context quality.

### Project Profile Layer

Each monitored project can provide local context and review preferences.

Supported v1 files:

- `.codex/project-context.md`: goal, phase, modules, commands, acceptance standards, known risks.
- `.codex/review-profile.toml`: enabled reviewers, phase, reminder policy, action policy, known noisy signals, protected scopes.

These files belong in the monitored project. `CODEX_HOME` stores runtime state, reports, automation memory, and installed tools; it should not become the source of project-specific truth.

If a project profile is missing, reviewers use the default rubric. Missing context should reduce confidence; it should not automatically make a finding high risk.
Missing context is usually setup debt, not proof that the workflow failed.

### Automation Instance Layer

An automation is only a trigger. It chooses a time window, runs reviewers, compares evidence, and emits visible triage.

Automations should:

- read reviewer rubrics before deciding severity
- read project context when available
- distinguish new problems from repeated background noise
- separate workflow regressions from setup debt and business-project issues
- avoid exposing full prompts, full tool output, full assistant messages, or local absolute paths
- default to observe/triage/proposal, not automatic mutation
- treat timers as triggers only; rubric plus context still decide severity

## Responsibility Chain

Read recurring workflow failures in this order:

1. Is the user-facing delivery trustworthy?
2. If not, is the failure in agent execution or in harness guidance?
3. If the harness is the problem, which layer owns the fix?

Typical ownership:

- hook or prompt contract missing -> built-in workflow layer
- evidence extraction, footer parsing, or trend comparison weak -> review kit layer
- rubric judgment or escalation threshold weak -> reviewer layer
- missing project goal, acceptance standard, or noise annotation -> project profile layer
- project-specific business behavior wrong -> monitored project, outside this kit

Applied to acceptance footer drift:

1. hooks enforce the footer contract
2. `session_facts.py` records footer presence/completeness
3. `analyze.py` compares footer gaps against prior reports
4. the reviewer decides whether that is observe, triage, or proposal
5. `triage.py` stages the reminder/proposal state, but does not invent a new policy

## Action Policy

Reviewers use three action levels:

- `observe`: record the signal, no interruption needed.
- `triage`: summarize the issue and ask for human judgment.
- `proposal`: prepare a safe, reviewable fix plan or patch, but do not apply it automatically.

Direct automated fixes are out of scope for v1.

## Design Boundary

Subagent workflow is the built-in default reviewer because it validates the kit's own orchestration behavior.
Project-specific reviewers belong to the monitored project or a separate reviewer-pack checkout. The durable goal is calibrated, cross-project workflow review without making any one external project part of the core install surface.
