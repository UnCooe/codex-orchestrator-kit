# Subagent Workflow Reviewer Rubric

Reviewer id: `subagent-workflow`

Standard: `default-v1`

## Goal

Judge whether delegation preserved main-thread focus and whether subagent lifecycle was closed cleanly.

## Input Facts

- `spawn_agent`, `wait_agent`, and `close_agent` counts
- per-session unclosed subagent delta
- heavy-work delegation signals
- broad-scan block and bypass signals
- shell-like tool surface signals
- final assistant subagent status signals
- optional project context and review profile

## Good

A run is good when:

- heavy work has reasonable delegation
- spawn/wait/close are close to balanced
- final response states `Subagents: closed` or explains retention
- broad scans are blocked or justified
- main thread does not spend excessive context on noisy exploration

## Watch

A run is watch-level when:

- there is a small unclosed subagent delta
- delegation rate is low but the observed work may be small
- broad-scan bypass signals exist but are likely intentional
- project context is missing, making task-size judgment lower confidence

Watch should produce a short triage summary and avoid automatic cleanup.

## Bad

A run is bad when:

- subagent spawn count greatly exceeds close count
- multiple sessions have retained agents without explanation
- heavy work is repeatedly handled in the main thread without delegation
- broad scans repeatedly bypass the guard without a clear reason
- final responses omit subagent lifecycle status after delegated work

Bad should request human review before changing workflow rules.

## Allowed Exceptions

- tiny single-file tasks
- direct command questions
- urgent blocker where main thread can verify faster than delegation
- user explicitly asks the main agent to work directly
- subagent retained for a continuous workflow with a stated close point

## Noise To Avoid

Do not treat these as high risk by themselves:

- one missing close in an interrupted session
- low delegation in mostly conversational windows
- broad-scan bypass when a delegation signal is already present
- historical lifecycle delta that is not growing

## Observe Only

Use `observe` for low-volume, explained, or historical lifecycle signals.

## Human Triage

Use `triage` for growing deltas, repeated bypasses, or missing lifecycle status in recent heavy work.

## Proposal

Use `proposal` for safe workflow changes such as prompt templates, AGENTS guidance, hook thresholds, or reviewer wording. Do not propose project code changes from subagent workflow facts alone.

