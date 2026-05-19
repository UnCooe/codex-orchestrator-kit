# Subagent Workflow Reviewer Rubric

Reviewer id: `subagent-workflow`

Standard: `default-v1`

## Goal

Judge whether the harness-prescribed subagent workflow actually ran as intended.
This reviewer is about workflow trustworthiness, not about counting subagent calls for their own sake.

The question is:

- did heavy work move into subagents when it should?
- did the main thread stay focused on goals, tradeoffs, and acceptance?
- did the run close or justify retained subagents clearly enough to trust the result?

## Input Facts

- `spawn_agent`, `wait_agent`, and `close_agent` counts
- per-session unclosed subagent delta
- heavy-work delegation signals
- broad-scan block and bypass signals
- shell-like tool surface signals
- final assistant subagent status signals
- optional project context and review profile

## Review Intent

Use this reviewer to decide whether a recurring problem should be blamed on:

- agent execution that bypassed or ignored the workflow
- harness contracts that were too weak or too ambiguous
- setup debt such as missing project context

Do not escalate just because raw counts look ugly. Escalate when the evidence makes delivery less trustworthy.
Use trend before blame: a stable historical tail is different from a new regression, and a recovering workflow is different from a worsening one.

## Good

A run is good when:

- heavy work has reasonable delegation
- spawn/wait/close are close to balanced
- lifecycle can be reconstructed from transcript and hook events without relying on user-visible footer text
- broad scans are blocked or justified
- main thread does not spend excessive context on noisy exploration

## Watch

A run is watch-level when:

- there is a small unclosed subagent delta
- delegation rate is low but the observed work may be small
- broad-scan bypass signals exist but are likely intentional
- project context is missing, making task-size judgment lower confidence
- user-visible formatting is inconsistent, but lifecycle and state signals are still reconstructable

Watch should produce a short triage summary and avoid automatic cleanup.
Watch is also the default outcome when signals exist but trend says they are stable background rather than a fresh regression.

## Bad

A run is bad when:

- subagent spawn count greatly exceeds close count
- multiple sessions have retained agents without explanation
- heavy work is repeatedly handled in the main thread without delegation
- broad scans repeatedly bypass the guard without a clear reason
- lifecycle or state-integrity cannot be reconstructed after delegated work
- repeated signals make it unclear whether the harness or the agent is drifting

Bad should request human review before changing workflow rules.
Bad is strongest when the trend is new or worsening, because that points to active workflow drift instead of stale historical residue.

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
- missing project context without any new workflow regression

## Observe Only

Use `observe` for low-volume, explained, or historical lifecycle signals.

## Human Triage

Use `triage` for growing deltas, repeated bypasses, repeated acceptance/footer gaps, or missing lifecycle status in recent heavy work.

When trend is available, classify it explicitly:

- `first_run`: no historical baseline yet; treat counts conservatively
- `worsening`: same signal is growing; bias toward human triage
- `mixed`: some signals improved while others worsened; keep the evidence compact
- `improving`: issue is still open but moving down; avoid over-escalation
- `stable`: repeated background state with no material change; prefer observe unless trust is still low

## Proposal

Use `proposal` for safe workflow changes such as prompt templates, AGENTS guidance, hook thresholds, or reviewer wording. Do not propose project code changes from subagent workflow facts alone.
