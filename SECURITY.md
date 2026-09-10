# Security Policy

Codex Orchestrator Kit is an experimental, unofficial Codex runtime harness. It
modifies local Codex hook, agent, and skill configuration when installed with
explicit apply flags.

## Sensitive Local Data

Do not attach or publish raw local runtime data from:

- `.codex/sessions/`
- `.codex/orchestrator-observability/`
- generated reports, triage state, proposal patches, and local backups
- real project context loaded by a local observability run

Observability events redact `cwd` and `transcript_path` by default. Setting
`ORCHESTRATOR_OBSERVABILITY_INCLUDE_PATHS=1` is for local debugging only and may
write full local paths into event logs.

## Reporting Issues

For public issues, include minimal reproduction steps and synthetic paths only.
Do not paste real transcripts, local usernames, customer names, secrets, or
private project context.

For suspected vulnerabilities or accidental sensitive-data exposure, use GitHub
private vulnerability reporting if enabled for the repository. If it is not
enabled, open a minimal public issue that requests a private contact path without
posting the sensitive details.
