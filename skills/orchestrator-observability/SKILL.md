---
name: orchestrator-observability
description: Analyze Codex Orchestrator hook telemetry and local session JSONL to summarize delegation, subagent lifecycle, hook blocks, output noise, token/reasoning signals, and acceptance gaps. Use when reviewing workflow health, debugging orchestration behavior, preparing automation triage, or deciding whether AGENTS.md, skills, prompt templates, or hooks need changes.
---

# Orchestrator Observability

Use this skill to inspect compact observability events and Codex session JSONL without loading raw transcripts into the main context.

## Workflow

1. Run the analyzer with a concrete Codex home and time window.
2. Run triage on the generated JSON report when deciding whether the user should be interrupted.
3. Run the subagent workflow report when validating heavy-work delegation behavior.
4. Read the generated Markdown summary first.
4. Use the JSON report only for exact counts or automation outputs.
5. Recommend changes as a patch list; do not directly change live workflow files unless the user asks.

PowerShell:

```powershell
python "$env:AGENTS_HOME\skills\orchestrator-observability\scripts\analyze.py" --codex-home "$env:CODEX_HOME" --since-days 7
```

Bash:

```bash
python "${AGENTS_HOME:-$HOME/.agents}/skills/orchestrator-observability/scripts/analyze.py" --codex-home "${CODEX_HOME:-$HOME/.codex}" --since-days 7
```

Reports are written under `CODEX_HOME/orchestrator-observability/reports/`.

Subagent workflow report is also available and written under:

- `$env:CODEX_HOME\orchestrator-observability\reports\subagent-workflow-<timestamp>.json`
- `$env:CODEX_HOME\orchestrator-observability\reports\subagent-workflow-<timestamp>.md`

PowerShell:

```powershell
python "$env:AGENTS_HOME\skills\orchestrator-observability\scripts\subagent_workflow.py" --codex-home "$env:CODEX_HOME" --since-days 7
```

Bash:

```bash
python "${AGENTS_HOME:-$HOME/.agents}/skills/orchestrator-observability/scripts/subagent_workflow.py" --codex-home "${CODEX_HOME:-$HOME/.codex}" --since-days 7
```

Use this report for subagent delegation-rate, spawn/wait/close balance, unclosed delta, heavy-work delegation signals, and broad-scan bypass observability.

Triage:

```powershell
python "$env:AGENTS_HOME\skills\orchestrator-observability\scripts\triage.py" --codex-home "$env:CODEX_HOME" --since-days 3
```

Triage state is written under `CODEX_HOME/orchestrator-observability/triage/`.
Low-risk proposals are surfaced only when a concrete safe `patch.diff` exists.
High-risk findings write `triage/alerts/high-risk-current.json`, which the `UserPromptSubmit` hook can remind about once per 24 hours.

## Review Focus

- Delegation rate for heavy work.
- Subagent lifecycle balance: spawn, wait, close, and remaining delta.
- Hook blocks and likely false positives.
- Large output and noisy-tool indicators.
- Final-response acceptance gaps: missing verification or subagent lifecycle status.
- Recurring candidates for `AGENTS.md`, skill, prompt-template, or hook-threshold updates.

## User-Facing Output

- If triage returns `none`, do not interrupt the user.
- If triage returns `low`, show only the short confirmation card from the proposal directory.
- If triage returns `high`, create a Triage finding and let the next prompt reminder stay one sentence.
- Never paste full reports, prompt text, tool output, assistant messages, or local absolute paths into the user-facing summary.
