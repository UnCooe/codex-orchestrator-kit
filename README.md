# Codex Orchestrator Kit

An unofficial, portable workflow layer for Codex. It provides router-first skills,
optional role templates, one narrow critical-action gate, merge-safety helpers, and
offline session observability.

This repository contains runtime workflow infrastructure only. Project-specific
business rules, credentials, production profiles, and evaluation experiments belong
outside this repository.

## Runtime Model

The managed catalog contains seven skills:

- Implicit: `orchestrator`, `orchestrator-observability`
- Explicit or selected after routing: `feature-workflow`, `review-workflow`,
  `merge-workflow`, `predeploy-workflow`, `merge-safety`

`orchestrator` is the generic entry point for non-trivial work. It selects one owner
for the current phase and reads only that owner's complete skill. An exact project
skill may own a phase directly; the generic router must not duplicate that work.

Delegation is outcome-based. A child is useful when it isolates noisy evidence,
provides an independent check, contributes specialist judgment, or owns a genuinely
separate boundary. The runtime policy does not require a fixed role roster. It starts
small, reuses an existing evidence lane, forbids nested delegation by default, and
keeps no more than four children active at once.

Every delegated work package carries a short role contract with the role, objective,
evidence scope, write boundary, expected artifact, done condition, stop condition,
and no-delegation rule. The files under `agents/` are optional templates for drafting
these contracts. They are not proof that a runtime injected an identity.

See [the orchestrator skill](skills/orchestrator/SKILL.md) and
[the role contract reference](skills/orchestrator/references/subagent-prompts.md).

## Safety Boundary

The kit registers two narrow runtime paths. `hooks/critical_action_gate.py` is
the only blocking gate; `hooks/workspace_lifecycle_shadow.py` is a
non-blocking lifecycle recorder.

It is deliberately narrow. Ordinary shell commands remain on the fast path. The
gate validates protected Git operations, pushes, SSH/server writes, `prodctl`, deploy,
restart, rollback, and related high-risk commands. Clear natural-language approval of
the currently displayed action is bound internally to the exact target, action, and
ref; users are not required to copy an opaque token into chat.

The installer also removes registrations for retired kit-owned lifecycle and pilot
hooks during upgrades. It preserves unrelated project and user hooks.

`merge-safety` adds protected-branch intent and verification checks. Its optional
`branch_lineage.py` helper records branch lineage in the repository's Git common
directory. A custom common directory can be selected with
`CODEX_MERGE_LINEAGE_COMMON_GIT_DIR`; the legacy
`AISERVER_LINEAGE_COMMON_GIT_DIR` name remains accepted for compatibility.

## Install

Python 3.11 or newer is required. Installation is a dry-run unless `--apply` or
`-Apply` is provided. Apply mode requires explicit runtime and skills modes.

Unix-like full install:

```bash
./install.sh \
  --codex-home "$HOME/.codex" \
  --hook-mode config_toml \
  --skills-mode codex \
  --apply
```

PowerShell full install:

```powershell
./install.ps1 `
  -CodexHome "$HOME/.codex" `
  -HookMode config_toml `
  -SkillsMode codex `
  -Apply
```

Supported hook modes are `hooks_json`, `config_toml`, and `auto`. Supported skills
modes are `agents`, `codex`, and `auto`. Prefer an explicit non-`auto` mode for a
known live installation.

To install only the seven skills without changing hooks, role templates, `hooks.json`,
or `config.toml`:

```bash
./install.sh --codex-home "$HOME/.codex" --skills-only --skills-mode codex --apply
```

```powershell
./install.ps1 -CodexHome "$HOME/.codex" -SkillsOnly -SkillsMode codex -Apply
```

Every apply verifies the exact managed skill file set and SHA-256 hashes. Full install
also verifies the ten role templates. Backup files, bytecode, and `__pycache__` are
not considered live managed files.

The installer does not replace a global `AGENTS.md`; use
`templates/global-agents.md` as the canonical source when managing that file.

## Uninstall

Unix-like:

```bash
./uninstall.sh \
  --codex-home "$HOME/.codex" \
  --hook-mode config_toml \
  --skills-mode codex
```

PowerShell:

```powershell
./uninstall.ps1 `
  -CodexHome "$HOME/.codex" `
  -HookMode config_toml `
  -SkillsMode codex
```

Use `--skills-only` or `-SkillsOnly` for symmetric skills-only removal. Full uninstall
removes only kit-managed skills, role templates, hook files, and hook registrations;
it preserves unrelated user and project files.

## Offline Observability

`orchestrator-observability` reads local Codex session JSONL and optional historical
hook telemetry to describe delegation, task-tree shape, completion, waits, token
categories, and acceptance gaps. It is not a live lifecycle hook and does not block
normal work.

Examples:

```bash
python3 skills/orchestrator-observability/scripts/analyze.py \
  --sessions-root "$HOME/.codex/sessions" \
  --window 24h \
  --output /tmp/orchestrator-report.json

python3 skills/orchestrator-observability/scripts/subagent_workflow.py \
  --sessions-root "$HOME/.codex/sessions" \
  --window 24h \
  --output /tmp/subagent-workflow.json
```

Forked history is normalized to one task tree so copied ancestor records are not
counted repeatedly. Reports summarize evidence instead of embedding unbounded raw
transcripts.

See [the observability skill](skills/orchestrator-observability/SKILL.md) and
[the review-kit boundary](docs/workflow-review-kit.md).

## Workspace Lifecycle (Shadow Mode)

`scripts/workspace_lifecycle.py` is the first, offline part of the workspace
lifecycle contract. It records task ownership outside the project repository and
classifies registered worktrees as clean, active, pending-closure,
quarantined, unowned-dirty, missing, or stale. It never switches branches,
commits, or removes worktrees.

`scripts/workspace_lifecycle_adapter.py` is the matching read-only payload
adapter. It recognizes the common lifecycle events and returns a shadow
decision for missing ownership, cross-task reuse, and incomplete handoff. It
does not enforce writes. The shadow hook connects it after the real-payload
canary: clean session/prompt starts record an `active` claim, clean `Stop`
events record `ready`; a known dirty stop is recorded as `pending_closure` and
queued for the original task, while an unattributed legacy state remains a
warning. The hook writes JSON ledger/queue state outside the project and never
blocks tools, commits files, switches branches, or removes worktrees.

Run the read-only contract against any Git repository:

```bash
python3 scripts/workspace_lifecycle.py audit --repo /path/to/repo \
  --state-dir /tmp/workspace-ledger-shadow
python3 scripts/workspace_lifecycle_reconcile.py inventory --repo /path/to/repo \
  --state-dir /tmp/workspace-ledger-shadow
```

The live integration remains shadow-only. It is a bookkeeping aid for task
attribution, not a guarantee that every session will commit or close a
worktree; when identity or `cwd` is absent, it does not guess. The Codex app can
read a queued owner-recall item and send the original task a follow-up. Stale
secondary worktrees can be previewed with `reconcile.py expire`; deletion is
explicit, path-scoped, and defaults to a 30-day threshold. The primary checkout
is always refused.

## Repository Layout

- `skills/`: seven managed workflow and observability skills
- `agents/`: ten optional role templates
- `hooks/critical_action_gate.py`: narrow blocking gate for high-risk commands
- `hooks/workspace_lifecycle_shadow.py`: non-blocking task/worktree ledger hook
- `templates/`: canonical hook and global instruction templates
- `scripts/verify_skill_install.py`: source/live parity verifier
- `scripts/workspace_lifecycle.py`: task-to-worktree ownership and audit contract
- `scripts/workspace_lifecycle_reconcile.py`: owner-recall queue, quarantine,
  read-only inventory, and path-scoped stale-worktree expiry
- `scripts/workspace_lifecycle_adapter.py`: read-only lifecycle payload adapter
- `scripts/workflow_mvp_dry_run.py`: static distribution inventory
- `tests/`: routing, safety, installer, merge, and observability tests

Evaluation runners, pilot gates, Task Receipt collection, latency experiments, and
peer-messaging experiments are not runtime dependencies. Reproducible evaluation
code belongs in the separate `codex-evaluation-kit` repository.

## Development Checks

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s skills/merge-safety/tests -p 'test_*.py'
bash -n install.sh uninstall.sh tests/smoke-test.sh
bash tests/smoke-test.sh
python3 tests/hygiene_check.py
git diff --check
```

Run the PowerShell smoke test on Windows when PowerShell is unavailable locally.

## Scope And Status

This is not an official OpenAI project. It is a conservative workflow kit, not a
general autonomous-agent benchmark or a claim that more agents are better. Safety
hooks enforce only narrow high-risk boundaries; ordinary reasoning remains guided by
skills and project context.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [LICENSE](LICENSE).
