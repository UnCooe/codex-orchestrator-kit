# Workspace Lifecycle v1

The workflow kit now has a small, local contract for the problem that Git
status alone cannot solve: a worktree may be dirty while a task is active, but
the changes must have an owner and an explicit next state.

`scripts/workspace_lifecycle.py` is intentionally advisory in this first
phase. It does not create branches, commit files, remove worktrees, or modify
Codex configuration. It can be run against any Git repository and discovers
the repository root with Git rather than relying on a project directory name.

## States

- `active`: the task owns the worktree and may still be editing it.
- `handoff`: the task is paused or handed to another session; an external
  artifact hash records the preserved handoff evidence.
- `ready`: the task has a clean worktree and is ready for review or promotion.
- `archived`: the task is terminal and the worktree is clean.

An unclaimed dirty worktree is reported as `unowned_dirty`. A different task
trying to claim an active worktree is reported as `conflict`. A terminal task
may be replaced by a new task only when the worktree is clean; the previous
claim is retained in the binding history.

## Commands

```sh
python3 scripts/workspace_lifecycle.py audit --repo /path/to/repo
python3 scripts/workspace_lifecycle.py inspect --worktree /path/to/worktree
python3 scripts/workspace_lifecycle.py claim --worktree /path/to/worktree --task-id TASK
python3 scripts/workspace_lifecycle.py transition --worktree /path/to/worktree --task-id TASK --state ready
```

State files default to `~/.codex/workspace-ledger-v1`; set
`CODEX_WORKSPACE_STATE_DIR` to use an isolated directory. The audit output is
JSON and includes `mutated: false` so it can be safely used in a shadow-mode
check before any hook integration.

This is not a requirement that every worktree be permanently clean. The
requirement is that no new task silently inherits another task's dirty files.
The next phase can add a first-write and task-transition adapter after the
contract has been exercised against real repositories.
