---
name: predeploy-workflow
description: Use for predeploy, staging, rollback, release verification, deployment handoff, server write, restart, or environment mutation workflows; interpret authorization and risk boundaries while adapting execution to the project's actual capabilities.
---

# Predeploy Workflow

Version: `predeploy-routing-v2.2-plan-scope`.

Use this as a boundary guide, not a fixed deploy runbook. The main agent interprets intent and chooses an appropriate execution path using project rules and evidence. Project tools assist execution; their scenario coverage does not define the scope of permitted routine nonproduction work. Keep technical safety checks and production controls intact.

## Hard Boundary

For confirmed pre/test work, a clear request such as “上预发验证” authorizes the ordinary project-scoped integration, build, deployment, necessary restart and verification. Default to the project's shared pre/test branch unless explicitly directed otherwise. Wrapper use is optional; do not turn missing wrapper capability into a prerequisite repair project. Check the actual host, application directory and service before direct SSH. Keep destructive/data operations, conflicting use, unknown targets and production outside this routine permission. Finish the task's own source changes and temporary worktrees; preserve unrelated work.

Production writes require explicit approval of the bounded plan and the controlled wrapper. One clear approval authorizes every listed step whose environment, target ref, host, service, and action remain unchanged; do not split push, build, deploy, restart, and verification into repeated approval prompts. If a premise in the user's approval is later proven false, such as assuming `master` already contains a fix, stop and resolve it. A `deployer` role is not authorization.

Hooks are fallback validators. They must not become the normal prompt layer or force the user to repeat a SHA, machine token, English action verb, or an already displayed target.

## Lane Routing

- `fast_pre`: low-risk pre/staging validation, checkout/reset to a known ref, pull/restart, rollback, or restart-only.
- `fast_hotfix`: urgent shared-target action with rollback, authorization, and bounded verification. By default, deploy the latest fetched `master` while using `expected_head` only as a drift guard; use an exact commit SHA only when the user explicitly asks for a pinned/detached SHA deploy or emergency exception.
- `shared_lane`: merge or push a shared branch such as `pre` or `master`, conflict work, or lineage-sensitive handoff.
- `controlled_ops`: config/env/schema/table/external-service, production-like, data-loss, restart-order, monitoring, or rollback-behavior risk.

Fast lanes default to the main agent and no subagent. Use `deployer`, `reviewer`, `merge-workflow`, or `merge-safety` only when project tooling or risk classification requires it.

## Procedure

1. Use applicable project guidance and available tooling; a dedicated planner is optional for routine pre/test work. When absent or unsuitable, use the project's existing scripts or standard commands within the same boundaries, without creating a prerequisite tooling project.
   Confirm it supports this project before invoking it; never run a hardcoded aiserver planner for a different project. An SSH host can serve multiple application directories/services. Select the full project target, preserve other targets, and do not repoint an existing application's mapping to serve another one.
2. Reuse current evidence or make targeted read-only checks to establish the target, shared-environment conflicts, available build/restart path, rollback and verification. For production, verify the controlled wrapper supports the required actions; do not substitute a direct write path.
3. For clear routine pre/test requests, give a short progress update and execute; do not add a mandatory plan/confirmation round. Ask once only if target, conflict handling, destructive scope or authorization is genuinely ambiguous. Production retains the bounded end-to-end approval plan.
4. Execute all listed steps under that approval without asking again. A short affirmative reply such as `按照规划推进` or `推吧` binds to the immediately preceding exact plan.
5. Stop for material scope changes or conflicts. A newly produced pre/test integration SHA within the approved task is not itself a new approval requirement. Do not silently chase unrelated changes on a moving shared branch; freeze the resolved SHA and check drift before applying.
6. Treat checkout-only as a write: it changes what the next restart will run. For production, checkout-only of a non-`master` ref needs the same explicit exception as an immediate deploy/restart.
7. If a subagent returns evidence, consume it; recheck only a representative sample and record why.

## Completion

Report lane, target env/ref, actions taken, verification, rollback path, and remaining risk. Do not imply deployment happened when only dry-run/read-only checks ran.
