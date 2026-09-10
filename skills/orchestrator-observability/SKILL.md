---
name: orchestrator-observability
description: Analyze Codex Orchestrator hook telemetry and local session JSONL to summarize delegation, subagent lifecycle, hook blocks, output noise, token/reasoning signals, and acceptance gaps. Use when reviewing workflow health, debugging orchestration behavior, preparing automation triage, or deciding whether AGENTS.md, skills, prompt templates, or hooks need changes.
---

# Orchestrator Observability

Use this skill to inspect Codex session JSONL and optional historical telemetry without loading raw transcripts into the main context. Resolve every script and reference path from this `SKILL.md` directory.

## Workflow

1. Run the analyzer with a concrete Codex home and time window.
2. Run triage on the generated JSON report when deciding whether the user should be interrupted.
3. Run the subagent workflow report when validating heavy-work delegation behavior.
4. Run the subagent value report when judging whether delegated work produced net value rather than only lifecycle activity.
5. Read `references/subagent-workflow-rubric.md` before deciding whether a subagent signal is good, watch-level, bad, or an allowed exception. Read `references/subagent-value-rubric.md` for value judgments.
6. Read the generated Markdown summary first.
7. Use the JSON report only for exact counts or automation outputs.
8. Recommend changes as a patch list; do not directly change live workflow files unless the user asks.

PowerShell:

```powershell
python "<current-skill-dir>\scripts\analyze.py" --codex-home "$env:CODEX_HOME" --since-days 7
```

Bash:

```bash
python "<current-skill-dir>/scripts/analyze.py" --codex-home "${CODEX_HOME:-$HOME/.codex}" --since-days 7
```

Reports are written under `CODEX_HOME/orchestrator-observability/reports/`.

Subagent workflow report is also available and written under:

- `$env:CODEX_HOME\orchestrator-observability\reports\subagent-workflow-<timestamp>.json`
- `$env:CODEX_HOME\orchestrator-observability\reports\subagent-workflow-<timestamp>.md`

PowerShell:

```powershell
python "<current-skill-dir>\scripts\subagent_workflow.py" --codex-home "$env:CODEX_HOME" --since-days 7
```

Bash:

```bash
python "<current-skill-dir>/scripts/subagent_workflow.py" --codex-home "${CODEX_HOME:-$HOME/.codex}" --since-days 7
```

Use this report for delegation rate, spawn/wait activity, terminal/final completion, remaining open children, heavy-work delegation signals, and broad-scan bypass observability. Legacy `close_agent` events remain parser inputs, not the current completion contract.

Subagent value report is also available and written under:

- `$env:CODEX_HOME\orchestrator-observability\reports\subagent-value-<timestamp>.json`
- `$env:CODEX_HOME\orchestrator-observability\reports\subagent-value-<timestamp>.md`

PowerShell:

```powershell
python "<current-skill-dir>\scripts\subagent_value_review.py" --codex-home "$env:CODEX_HOME" --since-days 7
```

Bash:

```bash
python "<current-skill-dir>/scripts/subagent_value_review.py" --codex-home "${CODEX_HOME:-$HOME/.codex}" --since-days 7
```

Use this report for value-oriented review: delegation fit, handoff quality, result quality, compression value, decision impact, context-savings signals, duplication cost, closure quality, missed-use candidates, and net utility. Treat it as a higher-level diagnostic than raw spawn/wait/close counts.

The Markdown report is intentionally a short decision dashboard for workflow owners. Do not paste full subagent rows or missed-use session lists into user summaries. Use the JSON `issue_groups`, `report_summary`, and `debug_artifact` fields for exact counts, representative evidence, and down-drill.

Triage:

```powershell
python "<current-skill-dir>\scripts\triage.py" --codex-home "$env:CODEX_HOME" --since-days 3
```

Triage state is written under `CODEX_HOME/orchestrator-observability/triage/`.
Low-risk proposals are surfaced only when a concrete safe `patch.diff` exists.
High-risk findings write `triage/alerts/high-risk-current.json` for explicit offline review. No runtime hook consumes it.

## Review Focus

- Delegation rate for heavy work.
- Subagent lifecycle state: spawn, wait, terminal/final completion, and remaining open children.
- Subagent value: whether a delegated episode was consumed by the main agent, compressed context, avoided duplicate work, and closed with positive net utility.
- Hook blocks and likely false positives.
- Large output and noisy-tool indicators.
- Final-response acceptance gaps: missing verification or subagent lifecycle status.
- Recurring candidates for `AGENTS.md`, skill, prompt-template, or hook-threshold updates.

Apply the subagent workflow rubric before escalating. Do not treat every lifecycle delta as an error:

- `observe` historical, low-volume, or explained signals.
- `triage` growing deltas, repeated broad-scan bypasses, or recent heavy-work lifecycle gaps.
- `proposal` only safe workflow changes such as prompt templates, hook thresholds, or reviewer wording.

## User-Facing Output

- If triage returns `none`, do not interrupt the user.
- If triage returns `low`, show only the short confirmation card from the proposal directory.
- If triage returns `high`, surface one concise finding for human review.
- If automation is producing a visible summary, include normal text even when also emitting inbox directives.
- Never paste full reports, prompt text, tool output, assistant messages, or local absolute paths into the user-facing summary.
