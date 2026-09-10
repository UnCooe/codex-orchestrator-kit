# Subagent Value Review Rubric

Reviewer id: `subagent-value`

Standard: `value-v3`

## Goal

Judge whether a delegated evidence lane created net value through context isolation, independent evidence, specialist judgment, or a separable boundary. Lifecycle activity alone is not value.

## Episode Outcomes

- `good_use`: the handoff was bounded, the result was evidence-backed and compact, and the main Agent used it in a later decision or acceptance check.
- `ineffective_use`: useful work may have occurred, but consumption, compression, or ownership evidence is weak.
- `harmful_use`: the child duplicated work, crossed scope, competed for the same write boundary, misled the parent, or created coordination cost without useful evidence.
- `missed_use`: the main Agent absorbed a clearly noisy or separable evidence surface that delegation could have isolated.
- `insufficient_evidence`: the available trace cannot support a reliable value judgment.

## Dimensions

Consider delegation fit, handoff quality, result quality, compression value, decision impact, context savings, duplication cost, completion quality, failure recoverability, and net utility. Report dimensions separately when the evidence does not support a combined judgment.

## Evidence Rules

- Prefer the work package, child terminal/final result, later parent actions, targeted verification, and final acceptance evidence.
- Treat `close_agent` only as optional legacy evidence. Current completion comes from terminal/final task state or an explicit retained reason.
- If result consumption cannot be observed, lower confidence instead of inventing intent.
- Do not call a delegated scan wasteful merely because the parent later reads a few targeted files for acceptance.
- Call work duplicate only when the same evidence scope is substantially repeated without a new question, changed strategy, or explicit verification purpose.
- Call a result unused only when no later decision, validation, synthesis, or explicit rejection refers to its evidence.
- Do not treat token, spawn, wait, or wall-clock reduction as a completion requirement. Use them only to explain tradeoffs once quality is established.

## Action Policy

- `observe`: value is positive or uncertainty is isolated and harmless.
- `triage`: repeated ineffective or harmful episodes, important unconsumed results, or missing evidence that blocks a trustworthy judgment.
- `proposal`: a narrow prompt, policy, or analyzer change supported by repeated evidence.

Keep the Markdown report decision-oriented. Detailed episode facts belong in the machine-readable artifact.
