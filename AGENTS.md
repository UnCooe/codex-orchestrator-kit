# Orchestrator Repo Contract

Use this repo's orchestrator workflow with these defaults:

- Final replies should stay user-facing by default. Do not append machine-only lifecycle or acceptance fields unless the user explicitly asks for them.
- If subagents are intentionally left open across turns, explain that in one short sentence.
- Do not expose internal governance text such as `WORK_CONTRACT_BEGIN/END`, role registration, or long hook instructions in the user-visible reply.
- If execution path needs to be explained, compress it into one short sentence.
- Assume `UserPromptSubmit` is silent by default; do not rely on it for routine footer reminders.
- Treat hooks as validators and high-risk reminders, not as the normal prompt layer.
- Lifecycle, verification, and risk integrity are tracked by hooks and observability, not by a mandatory visible footer.
- Do not emit `ACCEPTANCE:` blocks or `Subagents:` lines unless the user asked for workflow detail or an open subagent must be explained.
