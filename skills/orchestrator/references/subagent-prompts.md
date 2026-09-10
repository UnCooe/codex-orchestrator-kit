# Runtime Work Package Contract

Use this compact explicit work package contract in every child `message`. It is the runtime role instruction: do not rely on `agents/*.toml` being injected by Codex. Keep it task-specific and short.

```text
Role: <capability, such as researcher, test_writer, reviewer, or implementer>
Evidence lane: <unique lane name, ownership, and why delegation protects context or adds independent value>
Objective: <one bounded outcome>
Evidence scope: <files, interfaces, commands, or facts in scope>
Write boundary: <read-only or exact paths allowed to change>
Artifact: <required response or files>
Done: <observable completion condition>
Stop: <ambiguity, conflict, or limit that requires return to parent>
No delegation: Do not spawn or relay to subagents unless this work package explicitly grants a bounded read-only exception.
```

The parent assigns one owner per evidence lane and must consume, validate, or explicitly reject the returned artifact. Do not pass full parent history or duplicate another lane.

`agents/*.toml` may supply optional catalog metadata or a drafting reference. They do not guarantee runtime identity and must not be used as acceptance evidence.
