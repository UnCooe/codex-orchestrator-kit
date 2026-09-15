---
name: merge-safety
description: Use for Codex git merge safety when work involves merge, rebase, cherry-pick, git pull, PR merge, branch sync to shared refs, protected pre/test/master/main integration, or merge conflict resolution; route through merge_guard.py for machine-readable status, plan, checks, evidence, and next_action.
---

# Merge Safety

Use this as a guard entrypoint, not a merge runbook. The main agent owns intent and acceptance; `merge_guard.py` owns deterministic state, checks, blocking reasons, and `next_action`.

Script:

```bash
python scripts/merge_guard.py
```

Run the script from this skill's own directory, or resolve `scripts/merge_guard.py` relative to this SKILL file. Do not hardcode a user home path.

`scripts/branch_lineage.py` is an optional long-lived lineage store for project workflows. It stores events in the repository's common Git directory and accepts `CODEX_MERGE_LINEAGE_COMMON_GIT_DIR` when a project needs to pin the expected repository. The legacy `AISERVER_LINEAGE_COMMON_GIT_DIR` name remains accepted for compatibility. Merge safety does not activate project lineage implicitly.

## First Move

Run one of these before merge mutation:

```bash
python scripts/merge_guard.py plan --cwd /path/to/repo
python scripts/merge_guard.py status --cwd /path/to/repo
```

If `plan`, `status`, `report`, `check-pre`, or `check-post` returns `next_action`, follow that before re-deriving the workflow from natural language.

## Required Gates

- Before any mutating merge/rebase/cherry-pick/pull-like integration: initialize intent with `init`, then run `check-pre` as a separate command.
- After the merge result exists: record validation/review/subagent evidence with `record-evidence`, then run `check-post`.
- For promotion of a pre-validated result to master/main or another protected target: record and check lineage with `record-lineage` / `check-lineage`.
- Clear the active intent with `clear` when the merge is committed, abandoned, or handed off.

## Narrow Cherry-Pick To Protected Branch

Use this for a shared/protected target only when the user intends an exact commit and a branch merge would pull unrelated history.

- Create the integration branch from the fetched target ref, such as `origin/pre`.
- Initialize with the exact commit SHA as `--source-ref` and `--merge-mode 'cherry-pick -x'`.
- Run `check-pre`; for cherry-pick mode, allowed-path checks apply to the exact commit patch, not the whole source branch ancestry.
- Run `git cherry-pick -x <sha>` only after `check-pre` passes.
- Record evidence, then record lineage with `--validated-ref <sha> --allow-patch-equivalent`.
- Run `check-post` before pushing. Do not reinitialize the intent after the cherry-pick result exists.
- Do not use `--allow-patch-equivalent` for arbitrary manual patches or broad branch claims; it is for declared exact cherry-picked commits.

## Protected Branch Sync

Use this when the goal is to make a protected shared branch contain an already checked result without changing any running environment, for example after a feature branch or emergency commit has already been validated and the user asks to sync `master` or `main`.

This is a boundary, not a fixed runbook. The agent may use a clean integration worktree, fast-forward, merge commit, or narrow cherry-pick as appropriate, but the final write must be one explicit protected push and must not deploy, restart, force-push, or mutate servers.

After the result commit exists and validation/review evidence is available, prepare the push state:

```bash
python scripts/merge_guard.py prepare-protected-sync --cwd /path/to/repo --target-ref master --source-ref HEAD --validated-ref <validated-ref> --test "<validation>" --review "<review>"
```

Then run only the returned single-ref `git push` command after current-turn user approval. If `prepare-protected-sync` fails, follow its `next_action` and do not push.

## Agent Boundaries

- Ask the user when source evidence cannot decide product, compatibility, default, edge-case, data-loss, target/source ref, or hotfix authorization choices.
- Do not use ordinary `implementer` for conflict resolution; use `conflict_resolver` only for unresolved conflict files.
- Do not do whole-file ours/theirs, opportunistic refactors, fallback/default behavior changes, schema/API changes, or broad cleanup unless explicitly authorized.
- Use `reviewer` for shared-branch, protected-target, broad, or risky integration; reviewer is read-only and checks scope and unauthorized behavior changes.
- Use `test_runner` for focused validation evidence when needed.

## Completion

Report the target/source refs, operation, guard result, validation, review outcome, rollback or clear state, and remaining risk. Do not claim merge readiness unless `check-post` passes or you explicitly report the missing evidence/blocker.
