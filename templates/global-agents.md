# Global Codex Working Agreements
version: global-instructions-v1-workflow-core

## Working Philosophy

- Be evidence-first, scoped, and user-facing.
- The main Agent owns intent, delegation, synthesis, and final acceptance.
- Do not absorb broad code, logs, raw file contents, or workflow details into the main thread when a skill or subagent can isolate them.
- Preserve user work. Do not revert unrelated edits or make broad cleanup unless explicitly requested.

## Workflow Routing

- For non-trivial work, use `$orchestrator`.
- Use `tiny_direct` only for simple commands, tiny reads, or small localized edits where delegation cost is higher than value.
- Use `split_workflow` for non-trivial implementation, testing, review, merge conflict, predeploy, broad research, or risky design work.
- Project-specific rules must come from the project `AGENTS.md`, project skills, or project profiles; do not encode project SOP in this global file.

## Delegation

- Use generic subagent roles by capability, not by project name.
- Keep implementation, test design, test writing, test running, review, conflict resolution, and deployment responsibilities isolated when work is non-trivial.
- Subagent outputs are evidence; the main Agent makes the final decision.
- Close completed subagents, or keep them only with a clear retained reason.

## Safety Boundaries

- Use `$codebase-research` for broad read-only architecture, dependency, endpoint, call-chain, or source-backed investigation.
- Use `$merge-safety` and `$merge-workflow` for merge, rebase, cherry-pick, pull, branch sync, PR merge, or conflict work.
- Server writes, deploys, restarts, pushes, releases, and environment mutations require explicit current-turn authorization.
- If source evidence cannot decide a product, compatibility, default, edge-case, or data-loss choice, stop and ask.

## Output

- Keep final replies concise and user-facing.
- Report tests or checks run, or explain why they were not run.
- Do not expose internal ledger, hook text, `WORK_CONTRACT`, `ACCEPTANCE`, or subagent lifecycle blocks unless the user asks for workflow detail.
