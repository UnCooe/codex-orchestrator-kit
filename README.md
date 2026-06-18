# Codex Orchestrator Kit (portable)

Status: experimental, unofficial Codex runtime harness for advanced users.

This project is not an official Codex product or a stable public runtime API.
It installs local hooks, skills, and agent role files into a Codex runtime home
only when you pass an explicit apply flag. Run the default dry-run first and
read the install plan before writing to your main Codex environment.

This repository is a harness for Codex subagent workflow, not a business-project framework.
Its job is to keep Agent delegation, execution, acceptance, and closure predictable enough to review, trust, and correct.

The default install surface is the built-in workflow layer: global orchestrator guidance, subagent roles, hooks, and subagent workflow self-observability.
It also includes a workflow review kit layer: privacy-preserving fact extraction, reports, triage conventions, reviewer rubrics, and optional external reviewer packs.

The kit is not the monitored project. Project-specific context, business rules, and reviewer choices belong in the monitored project's `.codex/` directory.

## What This Harness Guarantees

The harness is meant to make subagent-based work stable and reviewable:

- delegation discipline for heavy work
- explicit subagent lifecycle closure or justified retention
- final acceptance signals that say what was verified, reviewed, and left risky
- recurring review that can tell the difference between new regression, repeated noise, and setup debt

## What This Harness Does Not Guarantee

The harness does not prove:

- business correctness
- application test quality
- project-specific architecture choices
- whether a monitored repository shipped the right feature

Those belong to the monitored project and its own reviewers.

## How To Read Failures

When automation reports a problem, read it in this order:

1. Is the delivered result trustworthy for the user?
2. If not, is the problem in agent execution, or in harness guidance and contracts?
3. If the harness is at fault, should the fix land in hooks, prompts, rubrics, triage, or project context?

Typical ownership:

- workflow constraint too weak -> hooks / prompts / rubric / triage
- acceptance footer unclear or inconsistently enforced -> acceptance contract
- missing project semantics -> `.codex/project-context.md` / `.codex/review-profile.toml`
- business behavior wrong but workflow evidence is sound -> monitored project, not this kit

## Workflow review model

See `docs/workflow-review-kit.md` for the full boundary.

- Built-in workflow layer: orchestrator skill, subagent roles, hooks, and default subagent workflow self-review.
- Review kit layer: fact extraction, reports, rubrics, triage conventions, installer, and optional reviewer packs.
- Project profile layer: optional `.codex/project-context.md` and `.codex/review-profile.toml` in each monitored project.
- Automation instance layer: scheduled runs that apply rubrics, read project context, and output visible triage.

Recurring automation should treat timers as triggers only. Severity should come from reviewer rubrics plus project context, not from raw counts alone.

## Product boundary

This repo is the workflow and review infrastructure, not the business/project reviewer itself.

- The built-in layer owns runtime behavior: prompts, hooks, delegation guardrails, and stop-gate acceptance enforcement.
- The observability layer owns facts, summaries, trend comparison, and triage state.
- Reviewer rubrics own judgment criteria. They decide what counts as observe/triage/proposal.
- The monitored project owns local context, enabled reviewers, and any project-specific reviewer pack.
- Automation owns scheduling and reminder delivery only. It does not define severity by itself.

That accountability chain is intentional: hook metrics are evidence, rubrics are policy, project context is calibration, and triage is the visible decision output.
This harness is successful when recurring review can explain whether the workflow itself needs reinforcement, not merely that a metric moved.

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
  - `scripts/subagent_workflow.py`
  - `scripts/subagent_value_review.py`
  - `agents/openai.yaml`
- `agents/` Toml role definitions copied from local config
  - `critic.toml`
  - `implementer.toml`
  - `researcher.toml`
  - `reviewer.toml`
  - `test-designer.toml`
- `templates/hooks.json.tpl`
- `templates/project-context.md`
- `templates/review-profile.toml`
- `docs/research-notes.md`
- `docs/workflow-review-kit.md`
- `docs/reviewers/*-rubric.md`
- `install-reviewer-pack.ps1` / `install-reviewer-pack.sh`
- `register-project.ps1` / `register-project.sh`
- `install.ps1` / `uninstall.ps1` (Windows-first)
- `install.sh` / `uninstall.sh` (best-effort Unix-like)
- `tests/fixtures/*` sample inputs for hook behavior validation

## Install

Default install commands are dry-run only and do not write files. Apply mode
requires an explicit runtime surface selection, including an explicit `auto`
when you want auto-detection.

Windows (PowerShell):

```
cd C:\Users\...\codex-orchestrator-kit
./install.ps1
./install.ps1 -Apply -HookMode auto -SkillsMode auto
```

The installer writes Codex hook and agent files into `%CODEX_HOME%` when set, otherwise `%HOME%/.codex`.
It supports two runtime surfaces:

- `hooks.json` plus `%HOME%/.agents\skills`
- `config.toml` plus `%HOME%/.codex\skills`

Auto mode detects the live surface from the target Codex home. If `hooks.json` exists, it updates that. If `config.toml` exists without `hooks.json`, it writes a managed hook block into `config.toml`. Skills default to `%HOME%/.agents\skills`, but switch to `%HOME%/.codex\skills` when the Codex-managed surface is detected.

Set custom homes:

```
$env:CODEX_HOME = 'D:\MyCodexHome'
$env:AGENTS_HOME = 'D:\MyAgentsHome'
./install.ps1 -CodexHome $env:CODEX_HOME -AgentsHome $env:AGENTS_HOME
./install.ps1 -CodexHome $env:CODEX_HOME -AgentsHome $env:AGENTS_HOME -Apply -HookMode hooks_json -SkillsMode agents
```

Dry run is the default. `-DryRun` is kept as an explicit compatibility alias:

```
./install.ps1 -DryRun
```

Unix-like:

```
./install.sh --codex-home /path/to/.codex --agents-home /path/to/.agents
./install.sh --codex-home /path/to/.codex --agents-home /path/to/.agents --apply --hook-mode auto --skills-mode auto
```

Explicit runtime selection:

```bash
./install.sh --codex-home /path/to/.codex --hook-mode config_toml --skills-mode codex
./install.sh --codex-home /path/to/.codex --hook-mode config_toml --skills-mode codex --apply
./install.sh --codex-home /path/to/.codex --agents-home /path/to/.agents --hook-mode hooks_json --skills-mode agents --apply
```

## Uninstall

Windows:

```
./uninstall.ps1
```

Unix-like:

```
./uninstall.sh --codex-home /path/to/.codex --agents-home /path/to/.agents --hook-mode auto --skills-mode auto
```

The uninstall removes orchestrator hook/agent/skill files and strips orchestrator hook entries from `hooks.json`. In `config.toml` mode it removes the managed hook block instead.

## Portability design

- No absolute Windows-user paths are stored in distributed sources.
- Installed hook commands are generated from `templates/hooks.json.tpl` or `templates/config.toml_hooks.tpl` at install time.
- The harness supports both `hooks.json + ~/.agents/skills` and `config.toml + ~/.codex/skills`.
- Existing `hooks.json` is preserved via backup and merged when possible. Existing `config.toml` is preserved and updated through a managed block.
- Each overwritten file is backed up with a timestamped `.bak.*` suffix.

## Support matrix

- Windows PowerShell: primary supported path.
- Unix-like Bash: best-effort support covered by smoke tests.
- Python: 3.11 or newer is required for `tomllib` and CI parity.
- Codex runtime surfaces: local `hooks.json` and `config.toml` layouts are
  supported as current local runtime surfaces, not as a guaranteed public API.

## Rollback and local data

Installers are backup-oriented, not fully transactional. If an unexpected
failure happens mid-install, inspect the timestamped `.bak.*` files next to the
modified target, then either restore the backup manually or rerun uninstall and
install against the intended target home.

Do not commit or attach local generated runtime data:

- `.codex/sessions/`
- `.codex/orchestrator-observability/`
- generated reports, triage state, proposal patches, and local backups
- real project `.codex/project-context.md` or `.codex/review-profile.toml`
- project-specific reviewer packs that contain private context

Distributed source files use synthetic fixture paths and synthetic fixture
secrets only for redaction tests. Runtime logs may contain project names and,
when explicitly enabled, full local paths.

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
- `cwd` and `transcript_path` are redacted in raw observability events by default.
- `ORCHESTRATOR_OBSERVABILITY_INCLUDE_PATHS=1` preserves raw paths for local debugging only.
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

The point is not to maximize telemetry. The point is to answer a harness question:

- did the agent use the subagent workflow the way this kit expects?
- if not, is it a new regression, repeated background noise, or setup debt?

Project-specific context is opt-in. Copy `templates/project-context.md` into a monitored project as `.codex/project-context.md`.
Copy `templates/review-profile.toml` as `.codex/review-profile.toml` when the project needs reviewer-specific reminder or action policy.

Register a monitored project with default subagent workflow review:

```powershell
./register-project.ps1 -ProjectPath C:\path\to\project
```

Register a project with an external reviewer selected by that project:

```powershell
./register-project.ps1 -ProjectPath C:\path\to\project -Reviewers subagent-workflow,my-project-reviewer
```

Install an external reviewer pack from a monitored project or another local checkout:

```powershell
./install-reviewer-pack.ps1 -PackPath C:\path\to\project\.codex\reviewer-packs\my-project
```

For Codex-managed skills under `~/.codex\skills`, install the pack into the Codex surface:

```powershell
./install-reviewer-pack.ps1 -PackPath C:\path\to\project\.codex\reviewer-packs\my-project -SkillsMode codex
```

Unix-like equivalents:

```bash
./register-project.sh --project /path/to/project
./install-reviewer-pack.sh --pack /path/to/project/.codex/reviewer-packs/my-project
./install-reviewer-pack.sh --pack /path/to/project/.codex/reviewer-packs/my-project --skills-mode codex --codex-home /path/to/.codex
```

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
The `Stop` hook enforces a compact acceptance footer for work-done replies:

- `verification_status=...`
- `review_status=...`
- `subagent_status=...`
- `risk_status=...`

Reports and triage treat that footer as a first-class signal. Missing or partial footers are tracked separately from plain verification gaps so trend classification can distinguish new regressions from stable background noise.

Hooks merge during install is deduplicated by event and orchestrator script name, so legacy `python .../orchestrator_*.py` entries are removed when newer `py -3` style entries are installed.

## External Reviewer Packs

External reviewer packs belong to the monitored project or to a separate local checkout. The core kit does not ship project-specific reviewers.

A reviewer pack has this minimal shape:

```text
my-project/.codex/reviewer-packs/my-project/
└─ skills/
   └─ my-project-observability/
      ├─ SKILL.md
      └─ scripts/
```

Install it with `install-reviewer-pack`, then enable its reviewer id in the monitored project's `.codex/review-profile.toml`.

Reviewer standards:

- Subagent workflow: `docs/reviewers/subagent-workflow-rubric.md`

Automations should read the relevant rubric, then project context, then the report summary.
External reviewer packs are project-profile choices, not core kit defaults.
Missing context lowers confidence; it is not automatically a high-risk finding.
