# Subagent Value Reviewer Rubric

Reviewer id: `subagent-value`

Standard: `value-v2`

## Goal

Judge whether subagents created measurable workflow value, not merely whether subagent lifecycle events occurred. The user-facing Markdown report is a decision summary, not an episode dump. Complete episode detail belongs in the JSON debug artifact.

The reviewer classifies each delegated episode as:

- `good_use`: the subagent was appropriate, got a clear handoff, returned compact useful evidence, was consumed by the main agent, and closed cleanly.
- `ineffective_use`: the subagent may have completed some work, but value is unclear because output was not consumed, handoff was weak, compression was weak, or closure was incomplete.
- `harmful_use`: the subagent added net cost through verbose/noisy output, missing result, duplicated work, misleading output, or leaked lifecycle state.
- `missed_use`: the main thread performed broad/noisy work that should likely have been delegated.
- `insufficient_evidence`: the trace lacks enough result, close, or consumption evidence to judge value safely.

The reviewer also aggregates episode and missed-use evidence into `issue_groups`. The main report should prioritize these issue groups over individual subagent rows.

## Scoring Dimensions

Use 1-5 scores for:

- `delegation_fit`: was this task appropriate for delegation?
- `handoff_quality`: did the spawn prompt provide goal, scope, evidence requirements, output contract, and completion criteria?
- `result_quality`: was the returned result correct-looking, structured, concise, evidence-backed, and decision-ready?
- `compression_value`: did the subagent compress large search/tool context into a small useful summary?
- `decision_impact`: did the main agent consume the result and use it for a later plan, action, or conclusion?
- `context_savings`: did the main thread avoid rereading or retaining the raw exploration?
- `duplication_cost`: did the subagent avoid repeating main-thread or sibling work?
- `closure_quality`: was the subagent closed or explicitly retained?
- `failure_recoverability`: if the subagent failed or returned partial evidence, did the main agent recover safely?
- `net_utility`: aggregate value after orchestration cost.

Episode scoring is two-stage:

- `raw_scores`: deterministic dimension scores from the observed trace.
- `confidence_penalties`: explicit deductions for missing result events, missing close events, missing consumption evidence, overlong output, or duplicate evidence rechecks.
- `final_score`: the score used for classification and report sorting.

## Evidence Rules

- Do not treat high spawn count as success.
- Do not treat low spawn count as failure unless a missed-use candidate exists.
- Do not treat an unclosed delta as fatal when there is an explicit retained reason.
- Prefer evidence from `spawn_agent` brief, `wait_agent` result output, post-wait assistant/tool activity, and `close_agent`.
- If consumption cannot be observed, classify with lower confidence rather than inventing intent.
- If result, close, or consumption evidence is missing, prefer `observability_gap` or `insufficient_evidence` over claiming the subagent had no value.
- Do not surface every missed-use candidate in the main report. Single broad scans and low-noise searches are debug signals unless multiple delegation-fit signals are present.
- Main report issues must be sorted by severity, frequency, confidence, and fixability, not by session order.

## Action Policy

- `observe`: value is mostly positive or evidence is insufficient and no repeated failure is visible.
- `triage`: repeated ineffective/harmful episodes, missed-use candidates, or unclear consumption on important tasks.
- `proposal`: safe changes to prompt templates, reviewer thresholds, automation packets, or project-context guidance.

Direct automatic workflow mutation is out of scope.

## Report Shape

Markdown should include:

- overall health, score, core problem, and first fix direction;
- key metrics with interpretation;
- top issue groups with priority, count, severity, confidence, and recommended fix;
- representative evidence only;
- links/field names for JSON down-drill.

JSON should retain:

- `issue_groups` and `report_summary`;
- complete `subagents`, `missed_use_candidates`, and `selected_packets`;
- `debug_artifact.episodes`, `debug_artifact.missed_candidates`, `debug_artifact.raw_score_details`, and `debug_artifact.observability_gaps`.
