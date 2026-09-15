# Workspace Lifecycle v1

The workflow kit now has a small, local contract for the problem that Git
status alone cannot solve: a worktree may be dirty while a task is active, but
the changes must have an owner and an explicit next state.

`scripts/workspace_lifecycle.py` and
`scripts/workspace_lifecycle_adapter.py` define an advisory local contract.
The installed `hooks/workspace_lifecycle_shadow.py` may write only its JSON
ledger state; none of these paths create branches, commit files, remove
worktrees, or modify Git content. They can be run against any Git repository
and discover the repository root with Git rather than relying on a project
directory name.

## States

- `active`: the task owns the worktree and may still be editing it.
- `handoff`: the task is paused or handed to another session; an external
  artifact hash records the preserved handoff evidence.
- `pending_closure`: a known task stopped with dirty changes. The binding keeps
  its task/thread id and a queue item is available for direct owner recall.
- `quarantined`: dirty changes have no verified owner. They are isolated from
  new claims until a human or the original session establishes provenance.
- `ready`: the task has a clean worktree and is ready for review or promotion.
- `archived`: the task is terminal and the worktree is clean.
- `expired`: an explicitly named stale secondary worktree was removed by the
  reconciliation command; this state is never used for the primary checkout.

An unclaimed dirty worktree is reported as `unowned_dirty`. A different task
trying to claim an active worktree is reported as `conflict`. A terminal task
may be replaced by a new task only when the worktree is clean; the previous
claim is retained in the binding history.

When a known task stops dirty, the shadow hook changes only the ledger state to
`pending_closure` and writes an owner-recall queue record. The queue is not a
commit or a deletion. When the owner later commits and reaches `ready` or
`archived`, the queue item is marked resolved. Legacy dirty states without a
verified owner can be placed in `quarantined` by the reconciliation command;
the historical ids are retained as candidates, never treated as proof.

## Commands

```sh
python3 scripts/workspace_lifecycle.py audit --repo /path/to/repo
python3 scripts/workspace_lifecycle.py inspect --worktree /path/to/worktree
python3 scripts/workspace_lifecycle.py claim --worktree /path/to/worktree --task-id TASK
python3 scripts/workspace_lifecycle.py transition --worktree /path/to/worktree --task-id TASK --state ready
python3 scripts/workspace_lifecycle.py queue --state-dir /path/to/state

# Read-only reconciliation and owner/expiry classification
python3 scripts/workspace_lifecycle_reconcile.py inventory --repo /path/to/repo
python3 scripts/workspace_lifecycle_reconcile.py queue --state-dir /path/to/state

# Preview first; only explicitly named stale secondary worktrees can be removed
python3 scripts/workspace_lifecycle_reconcile.py expire --repo /path/to/repo \
  --worktree /path/to/old-worktree --older-than-days 30
```

State files default to `~/.codex/workspace-ledger-v1`; set
`CODEX_WORKSPACE_STATE_DIR` to use an isolated directory. The audit output is
JSON and includes `mutated: false` so it can be safely used in a shadow-mode
check before any hook integration.

This is not a requirement that every worktree be permanently clean. The
requirement is that no new task silently inherits another task's dirty files.
The adapter reads common lifecycle payload fields, reports whether a write
would be attributed or blocked, and keeps `mutated: false` in both `shadow`
and isolated `enforce` mode. The shadow hook calls the same contract after the
real payload canary: it claims clean worktrees at session/prompt start and
records `ready` for clean `Stop` events. Dirty or conflicting states only
queue known dirty tasks for owner recall and produce additional context.
Missing task identity or `cwd` is left unattributed rather than inferred from
a directory name. No live hook sends messages itself: the Codex app (or a
human-operated dispatcher) consumes the queue and calls the original task.

`workspace_lifecycle_reconcile.py` is intentionally conservative. It can
classify a queue item as `owner_recall` when the binding contains a verified
thread id, or `quarantine_review` otherwise. `expire` defaults to a 30-day
threshold but is dry-run unless `--apply` is supplied, requires exact
`--worktree` paths, refuses the primary checkout, and never deletes individual
files from a dirty main worktree. A scheduler may invoke `inventory` daily;
this repository does not install a new scheduler or modify live Codex config.

## Payload adapter

```sh
printf '%s\n' '{"hook_event_name":"SessionStart","cwd":"/path/to/worktree","thread_id":"TASK"}' \
  | python3 scripts/workspace_lifecycle_adapter.py --mode shadow
```

The adapter accepts `SessionStart`, `UserPromptSubmit`, `PreToolUse` and
`Stop`. Missing task ids or worktree paths produce a warning rather than a
guessed owner. `shadow` is the only live-safe mode; `enforce` is for an
isolated canary and reports a deny decision for an unowned dirty write without
performing the write itself.

The current external workflow-context log records the event, cwd, detected
root, and branch, but not a stable task/thread id. The adapter can optionally
observe `CODEX_THREAD_ID` or `CODEX_SESSION_ID` from the hook process and marks
that source as `environment`; the real canary confirmed that the payload's
`session_id` is the stable source in the current CLI. Without either an
explicit payload id or an environment id it returns `unknown_task`. This is
an intentional fail-closed result, not a reason to infer ownership from the
directory name.
