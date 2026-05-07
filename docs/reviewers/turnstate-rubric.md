# Turnstate Reviewer Rubric

Reviewer id: `turnstate`

Standard: `default-v1`

## Goal

Judge whether a Codex run has a trustworthy main-flow closure. Turnstate focuses on observable workflow facts, not code correctness.

## Input Facts

- session metadata and project label
- reasoning and token-count event presence
- tool call and tool output balance
- assistant work-done signals
- structured acceptance footer signals
- large output size signals
- optional project context and review profile

## Good

A run is good when:

- work that claims completion includes an explicit verification or acceptance signal
- tool calls and tool outputs are balanced enough to reconstruct execution
- reasoning and token-count events are present for active sessions
- large outputs are summarized or avoided
- project context, if available, does not contradict the final acceptance

## Watch

A run is watch-level when:

- completion is claimed without enough verification in some sessions
- large outputs exist but do not block reconstruction
- project context is missing, making judgment lower confidence
- failures are repeated background noise rather than clearly new regression

Watch should produce a short triage summary, not an automatic fix.

## Bad

A run is bad when:

- tool calls and outputs are materially imbalanced
- work is repeatedly declared complete without verification
- reports cannot support a credible review because key facts are missing
- large outputs overwhelm review or hide important evidence

Bad should request human intervention before further automation changes.

## Allowed Exceptions

- exploratory Q&A with no claimed implementation
- pure explanation or planning
- user explicitly asked not to run tests or make changes
- tool or environment was unavailable and the final response says so
- very small inspection tasks where no durable verification is meaningful

## Noise To Avoid

Do not treat these as high risk by themselves:

- missing project context
- one-off missing verification in old sessions
- large output from a non-shell tool that was already summarized
- active work that ended before final response because the user interrupted or redirected

## Observe Only

Use `observe` when the signal is informational, old, low volume, or explained by an allowed exception.

## Human Triage

Use `triage` when the issue is repeated, newly worse, or likely to affect trust in delivery.

## Proposal

Use `proposal` only for safe workflow changes such as updating prompts, rubrics, templates, or report wording. Do not propose application code changes from Turnstate alone.

