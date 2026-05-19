# Orchestrator Repo Contract

Use this repo's orchestrator workflow with these defaults:

- Final replies must include `verification_status`, `review_status`, `subagent_status`, and `risk_status`.
- If subagents were used, also include `Subagents: closed` or `Subagents: retained because ...`.
- Do not expose internal governance text such as `WORK_CONTRACT_BEGIN/END`, role registration, or long hook instructions in the user-visible reply.
- If execution path needs to be explained, compress it into one short sentence.
- Assume `UserPromptSubmit` is silent by default; do not rely on it for routine footer reminders.
- Treat hooks as validators and high-risk reminders, not as the normal prompt layer.
- If a stop-hook repair round happens, reply with only the missing footer or lifecycle delta. Do not restate the main summary.
- A natural-language `ACCEPTANCE:` block is acceptable when it clearly covers verification, remaining risk, and subagent lifecycle semantics.
