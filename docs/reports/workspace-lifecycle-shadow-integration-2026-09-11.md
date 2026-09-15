# Workspace Lifecycle Shadow Integration

Date: 2026-09-11

## Result

The real-payload canary established that `codex-cli 0.144.1` supplies a stable
`session_id` and the current worktree `cwd` on `SessionStart`,
`UserPromptSubmit`, `PreToolUse`, and `Stop`. The kit now has a shadow-only
runtime integration for those fields.

## Runtime behavior

- A clean session or prompt start records one `active` task binding.
- A clean `Stop` records `ready` for that same task.
- A dirty, unowned, handed-off, or conflicting worktree emits a warning. A
  known active task that stops dirty is recorded as `pending_closure` with an
  owner-recall queue item; an unowned legacy state is not guessed or claimed.
- A clean terminal binding can be replaced by a later task; the previous task
  remains in binding history.
- Missing identity or worktree fields are not guessed.

The hook writes only JSON ledger and queue files under the configured Codex
state directory. It does not edit project files, commit, switch branches,
delete worktrees, send messages, or deny tools. The Codex app can consume a
queue item and send a prompt to the recorded original task; that dispatch is
kept outside the hook. `critical_action_gate.py` remains the only blocking
runtime gate.

## Evidence and limits

The isolated canary used a temporary Codex home and a read-only task. It proves
payload shape and the adapter-to-hook transition, not that every task will
finish cleanly or commit its work. The shadow ledger is therefore an
attribution and warning mechanism; it intentionally does not claim to solve
unfinished worktrees by itself. The reconciliation command supplies a
read-only inventory, explicit owner-recall prompts, and a dry-run expiry path
for named stale secondary worktrees. The primary checkout is never eligible
for automatic deletion.
