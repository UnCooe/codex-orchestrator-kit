# Workspace Lifecycle Canary

Date: 2026-09-11

## Scope

This was an isolated Codex CLI run. It used a temporary `CODEX_HOME`, a
read-only copy of the current authentication file, a temporary Git repository,
and a local hook that recorded field names and identifiers only. The model was
asked to run one read-only `pwd` command and return a fixed marker. No live
Codex file, project worktree, server, or repository was changed.

## Observed contract

The run produced four lifecycle events:

| Event | Identity | Worktree | Tool evidence |
| --- | --- | --- | --- |
| `SessionStart` | `session_id` present | same `cwd` | none |
| `UserPromptSubmit` | same `session_id` | same `cwd` | none |
| `PreToolUse` | same `session_id` | same `cwd` | `tool_name=Bash`, `tool_input` present |
| `Stop` | same `session_id` | same `cwd` | none |

The hook process did not expose `CODEX_THREAD_ID` or `CODEX_SESSION_ID`; the
payload itself supplied the stable identity. The temporary repository ended
clean. The model returned the expected marker and the tool returned exit code
0.

The sanitized event shape is preserved in
`tests/fixtures/workspace-lifecycle-hook-payloads.jsonl`. Sensitive prompt,
transcript, and command contents are redacted or reduced to the read-only
`pwd` probe.

## Decision

The payload contract is sufficient for an isolated shadow integration using
`session_id` and `cwd`. It is not yet evidence that a live hook should enforce
ownership: the canary used a temporary trust bypass and a single no-write task.
The next step is a shadow-only integration canary that records claim decisions
for a normal task and a continuation, while keeping enforcement disabled.
