# Codex Orchestrator Kit (portable)

This repository contains a portable, open-source friendly drop-in bundle for the Codex Desktop orchestrator workflow.

## What is included

- `skills/orchestrator/`
  - `SKILL.md`
  - `agents/openai.yaml`
  - `references/subagent-prompts.md`
- `hooks/`
  - `orchestrator_user_prompt.py`
  - `orchestrator_pre_tool_use.py`
  - `orchestrator_post_tool_use.py`
  - `orchestrator_stop.py`
  - `orchestrator_observability.py`
- `skills/orchestrator-observability/`
  - `SKILL.md`
  - `scripts/analyze.py`
  - `scripts/triage.py`
  - `scripts/session_facts.py`
  - `agents/openai.yaml`
- `skills/turnstate-observability/`
  - `SKILL.md`
  - `scripts/turnstate_report.py`
- `agents/` Toml role definitions copied from local config
  - `critic.toml`
  - `implementer.toml`
  - `researcher.toml`
  - `reviewer.toml`
  - `test-designer.toml`
- `templates/hooks.json.tpl`
- `docs/research-notes.md`
- `install.ps1` / `uninstall.ps1` (Windows-first)
- `install.sh` (best-effort Unix-like)
- `tests/fixtures/*` sample inputs for hook behavior validation

## Install

Windows (PowerShell):

```
cd C:\Users\...\codex-orchestrator-kit
./install.ps1
```

The installer writes Codex hook and agent files into `%CODEX_HOME%` when set, otherwise `%HOME%/.codex`.
It writes skills into `%AGENTS_HOME%` when set, otherwise `%HOME%/.agents`.
Set custom homes:

```
$env:CODEX_HOME = 'D:\MyCodexHome'
$env:AGENTS_HOME = 'D:\MyAgentsHome'
./install.ps1 -CodexHome $env:CODEX_HOME -AgentsHome $env:AGENTS_HOME
```

Dry run (no file writes):

```
./install.ps1 -DryRun
```

Unix-like:

```
./install.sh --codex-home /path/to/.codex --agents-home /path/to/.agents
```

## Uninstall

Windows:

```
./uninstall.ps1
```

The uninstall removes orchestrator hook/agent/skill files and strips orchestrator hook entries from `hooks.json`.

## Portability design

- No absolute Windows-user paths are stored in distributed sources.
- Installed hook commands are generated from `templates/hooks.json.tpl` at install time.
- Codex hooks/custom agents and global skills use separate homes because Codex Desktop commonly reads hooks from `~/.codex` and global skills from `~/.agents`.
- Existing `hooks.json` is preserved via backup and merged when possible.
- Each overwritten file is backed up with a timestamped `.bak.*` suffix.

## Plugin-v2 direction

Current Codex desktop distribution still consumes local hooks and agents under `~/.codex`.
The kit is intentionally kept as plain config + scripts so it can be migrated later to plugin-v2 packaging
(e.g., artifact download + post-install migration hook) without changing source skill/hook logic.

## Smoke fixture

See `tests/fixtures/` and `tests/smoke-test.ps1`.
Use this for quick local validation. It exercises Windows install, dry-run, idempotent hook merge, core hook behavior, and uninstall against a temporary home:

```
./tests/smoke-test.ps1
```

Unix-like smoke validation:

```
bash ./tests/smoke-test.sh
```

The installer is backup-oriented, not fully transactional. If an unexpected failure happens mid-install, use the timestamped `.bak.*` files to recover or rerun uninstall/install after inspecting the target home.

## Observability

Hooks append compact JSONL events into `CODEX_HOME/orchestrator-observability/events/YYYY-MM-DD.jsonl`.
They keep `transcript_path` only as metadata and do not modify Codex's original session transcript files.

Defaults are privacy-first:

- `ORCHESTRATOR_OBSERVABILITY_CAPTURE_TEXT` is disabled by default.
- Full prompt/tool output/assistant message is not included by default.
- When capture is enabled, values matching key/secret/token-like patterns are redacted before storage.
- Analyzer reports redact full `codex_home` and `cwd` paths by default while preserving project-name aggregation.
- Use `--include-paths` only for local-only debugging when full path metadata is needed.

Run the offline analyzer:

```powershell
python $env:AGENTS_HOME\skills\orchestrator-observability\scripts\analyze.py `
  --codex-home $env:CODEX_HOME `
  --since-days 30
```

Reports are written to:

- `$env:CODEX_HOME\orchestrator-observability\reports\orchestrator-observability-<timestamp>.json`
- `$env:CODEX_HOME\orchestrator-observability\reports\orchestrator-observability-<timestamp>.md`

The analyzer reads both Codex session JSONL and the separate observability event logs, then reports delegation signals, hook blocks, token/reasoning counts, large-output signals, project context summaries, and acceptance gaps.
It emits project aggregation, redacted CWD labels, a privacy-preserving facts list (with timestamp/session/event metadata), and a suggestions section for follow-up actions.

Run the subagent workflow report:

```powershell
python $env:AGENTS_HOME\skills\orchestrator-observability\scripts\subagent_workflow.py `
  --codex-home $env:CODEX_HOME `
  --since-days 14
```

Subagent workflow reports are written to:

- `$env:CODEX_HOME\orchestrator-observability\reports\subagent-workflow-<timestamp>.json`
- `$env:CODEX_HOME\orchestrator-observability\reports\subagent-workflow-<timestamp>.md`

Run triage after a report:

```powershell
python $env:AGENTS_HOME\skills\orchestrator-observability\scripts\triage.py `
  --codex-home $env:CODEX_HOME `
  --since-days 3
```

Triage writes local state under `$env:CODEX_HOME\orchestrator-observability\triage\`:

- `alerts\high-risk-current.json` for a pending high-risk reminder.
- `proposals\<id>\proposal.md`, `manifest.json`, `patch.diff`, `validation.md`, and `rollback.md` for low-risk proposal confirmation.
- `archive\*.json` for no-interrupt outcomes.

The `UserPromptSubmit` hook checks only the pending high-risk alert and reminds at most once per 24 hours.
Low-risk proposals are not pushed into the next prompt; they are surfaced only when a concrete safe patch exists.

Hooks merge during install is deduplicated by event and orchestrator script name, so legacy `python .../orchestrator_*.py` entries are removed when newer `py -3` style entries are installed.

## Turnstate observability

Turnstate reports reuse the same privacy-preserving session fact layer. They are a separate consumer, not a second hook pipeline.

Run the Turnstate compliance report:

```powershell
python $env:AGENTS_HOME\skills\turnstate-observability\scripts\turnstate_report.py `
  --codex-home $env:CODEX_HOME `
  --since-days 3
```

Reports are written to:

- `$env:CODEX_HOME\turnstate-observability\reports\turnstate-compliance-<timestamp>.json`
- `$env:CODEX_HOME\turnstate-observability\reports\turnstate-compliance-<timestamp>.md`

The default rule profile checks observable runtime facts: reasoning-summary presence, tool call/output balance, token-count events, verification signals, and large-output noise. Reports still redact full paths and do not include full prompts, full tool output, or full assistant messages.

Turnstate and subagent checks are intentionally separated:

- Turnstate focuses on main-flow compliance.
- `subagent_workflow.py` focuses on delegation and subagent lifecycle signals.
