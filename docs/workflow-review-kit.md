# Offline Workflow Review

The runtime kit includes an offline observability skill. It reads local Codex session
JSONL after execution and reports task-tree shape, delegation, waits, completion,
token categories, and evidence-consumption signals.

It is deliberately not a runtime lifecycle system:

- no observability hook is registered;
- no acceptance footer or Task Receipt is injected;
- no report blocks ordinary commands;
- no raw transcript is copied into the generated summary;
- no project-specific business truth is inferred from workflow counts.

Forked sessions may contain copied ancestor records. The parser bounds every file to
records created at or after that file's current `session_meta`, then removes identical
records observed in more than one file. Full aggregate metrics remain available while
embedded fact samples are capped so reports do not grow without bound.

Two bundled analyses are available:

- `analyze.py` builds a privacy-bounded session report.
- `subagent_workflow.py` summarizes delegation and whether child outputs appear to be
  consumed, duplicated, or left open.

These are diagnostic signals, not an automatic quality score. A high spawn count is
not inherently bad, a repeated read may be a legitimate verification step, and an
unclosed child makes coordination metrics partial rather than invalidating the task's
outcome.

Project-specific reviewers, evaluation oracles, and benchmark runners belong outside
this runtime repository. Reusable evaluation contracts and public demonstrations live
in the separate `codex-evaluation-kit` repository.
