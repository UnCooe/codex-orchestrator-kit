---
name: predeploy-workflow
description: Use for predeploy, staging, rollback, release verification, deployment handoff, server write, restart, or environment mutation workflows; choose a semantic lane, require current-turn authorization for writes, and let project tooling provide deterministic plans and commands.
---

# Predeploy Workflow

Version: `predeploy-routing-v2.2-plan-scope`.

Use this as a routing card, not a deploy runbook. The main agent interprets intent, calls the project predeploy/deploy planner, and follows that tool's machine-readable plan. Scripts, hooks, and project deploy tools own hard gates.

## Hard Boundary

Do not run server writes, deploy, push, release, restart, or environment mutation unless the user explicitly authorized the exact bounded plan in the current turn. One clear approval of a displayed plan authorizes every listed step whose environment, target ref, host, service, and action remain unchanged; do not split push, build, deploy, restart, and verification into repeated approval prompts. If a premise in the user's approval is later proven false, such as assuming `master` already contains a fix, the approval is stale and must be renewed. A `deployer` role is not authorization.

Hooks are fallback validators. They must not become the normal prompt layer or force the user to repeat a SHA, machine token, English action verb, or an already displayed target.

## Lane Routing

- `fast_pre`: low-risk pre/staging validation, checkout/reset to a known ref, pull/restart, rollback, or restart-only.
- `fast_hotfix`: urgent shared-target action with rollback, authorization, and bounded verification. By default, deploy the latest fetched `master` while using `expected_head` only as a drift guard; use an exact commit SHA only when the user explicitly asks for a pinned/detached SHA deploy or emergency exception.
- `shared_lane`: merge or push a shared branch such as `pre` or `master`, conflict work, or lineage-sensitive handoff.
- `controlled_ops`: config/env/schema/table/external-service, production-like, data-loss, restart-order, monitoring, or rollback-behavior risk.

Fast lanes default to the main agent and no subagent. Use `deployer`, `reviewer`, `merge-workflow`, or `merge-safety` only when project tooling or risk classification requires it.

## Procedure

1. Use the project-specific predeploy/deploy skill or planner first.
2. Before asking for authorization, use read-only checks to establish the shared-environment owner or conflicting validation, controlled-tool support for checkout/build/restart, target ref and expected SHA, service, rollback, and verification steps.
3. Present one bounded end-to-end plan. Ask once if its lane, target, conflict handling, tooling change, rollback, verification, or authorization is ambiguous.
4. Execute all listed steps under that approval without asking again. A short affirmative reply such as `按照规划推进` or `推吧` binds to the immediately preceding exact plan.
5. Stop and ask again only when the environment, protected target, deployed SHA, service, write capability, or action set materially changes. Do not silently chase a moving shared branch; freeze the resolved SHA or report the drift.
6. Treat checkout-only as a write: it changes what the next restart will run. For production, checkout-only of a non-`master` ref needs the same explicit exception as an immediate deploy/restart.
7. If a subagent returns evidence, consume it; recheck only a representative sample and record why.

## Completion

Report lane, target env/ref, actions taken, verification, rollback path, and remaining risk. Do not imply deployment happened when only dry-run/read-only checks ran.
