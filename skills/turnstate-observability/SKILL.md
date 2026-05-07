---
name: turnstate-observability
description: Generate privacy-preserving Turnstate compliance reports from local Codex session facts, including reasoning-summary presence, tool execution balance, token-count signals, acceptance checks, and large-output noise.
---

# Turnstate Observability

Use this skill when reviewing whether Codex runs followed the expected Turnstate control logic.
It consumes the shared session fact layer from `orchestrator-observability` and does not read full prompt text, full tool output, or full assistant messages.
Use `docs/reviewers/turnstate-rubric.md` as the default standard before deciding whether a signal is good, watch-level, bad, or an allowed exception.

## Workflow

1. Generate a Turnstate compliance report for a concrete time window.
2. Read the Turnstate rubric.
3. Read the Markdown summary first.
4. Use the JSON report only for exact counts, rule failures, and automation output.
5. Recommend rule or workflow changes as a short patch list; do not edit live configuration unless the user asks.

PowerShell:

```powershell
python "$env:AGENTS_HOME\skills\turnstate-observability\scripts\turnstate_report.py" --codex-home "$env:CODEX_HOME" --since-days 3
```

Bash:

```bash
python "${AGENTS_HOME:-$HOME/.agents}/skills/turnstate-observability/scripts/turnstate_report.py" --codex-home "${CODEX_HOME:-$HOME/.codex}" --since-days 3
```

Reports are written under `CODEX_HOME/turnstate-observability/reports/`.

## Rule Profile

The first rule profile is `turnstate-default-v1`.
It checks observable runtime facts only:

- reasoning summary presence for active sessions
- tool call/output balance
- token count event presence
- verification signal on completed work
- large tool output noise

Note: subagent delegation/lifecycle signals are now tracked by the separate
`orchestrator-observability` subagent workflow report and are not part of the Turnstate rule profile.

Apply the rubric before escalating:

- `observe` one-off, old, explained, or purely exploratory signals.
- `triage` repeated or newly worse verification and output-noise issues.
- `proposal` only safe workflow changes such as prompts, rubrics, templates, or report wording.

## User-Facing Output

Keep summaries short:

- current status: `ok`, `watch`, or `needs_review`
- score
- top 1-3 failed rules
- why it matters for Turnstate
- recommended next action

Missing project context should lower confidence but should not be treated as a high-risk finding by itself.
Never paste full reports, prompt text, tool output, assistant messages, or local absolute paths into the user-facing summary.
