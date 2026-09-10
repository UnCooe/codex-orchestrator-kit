# Subagent Workflow Review Rubric

Reviewer id: `subagent-workflow`

Standard: `outcome-v1`

## Goal

Diagnose whether delegation protected the main context and improved trustworthy delivery. Do not score a run by subagent count or by compliance with a fixed role roster.

## Primary Evidence

- named evidence lanes and their work-package boundaries
- task-tree parent, depth, start, terminal, and final states
- whether each result was consumed, sampled for verification, or explicitly rejected
- duplicate scans, competing writers, unauthorized nesting, and unexplained open children
- validation and review evidence tied to the delivered outcome

`close_agent` may appear in legacy sessions, but current completion is established by terminal/final task state. An open child makes coordination metrics partial unless a retained reason is present; it does not by itself invalidate the delivered outcome.

## Healthy Patterns

- bounded local work stays in the main Agent when delegation would add no context value
- noisy, specialist, independent, or separable evidence gets one clearly named owner
- distinct evidence lanes do not overlap, and the main Agent consumes their compact artifacts
- the main Agent performs targeted acceptance checks instead of repeating a delegated broad scan
- only one writer owns a production, test, fixture, or configuration boundary at a time
- default depth is one; any depth-two research exception is explicit and read-only
- no child is created only to satisfy workflow appearance

## Review Findings

Use `observe` when behavior is explainable and delivery remains trustworthy.

Use `triage` when repeated duplicate work, unused results, competing writers, unauthorized nesting, or unexplained open children make the result harder to trust.

Use `proposal` only for a narrow workflow or observability change supported by repeated evidence. Never infer a project-code change from workflow telemetry alone.

## Evidence Limits

- High spawn or wait counts are diagnostic facts, not failures by themselves.
- Low delegation is not a failure without evidence that the main thread absorbed a noisy or separable surface.
- A missing project context file lowers confidence only when the task boundary cannot otherwise be reconstructed.
- Interrupted or truncated sessions should be marked partial or unknown, not forced into a negative judgment.
- Historical legacy events must not be double-counted through inherited fork history.
