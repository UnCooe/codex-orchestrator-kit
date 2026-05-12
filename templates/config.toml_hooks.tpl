# BEGIN managed by codex-orchestrator-kit
[[hooks.UserPromptSubmit]]

[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/orchestrator_user_prompt.py\""
timeout = 10

[[hooks.PreToolUse]]
matcher = "*"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/orchestrator_pre_tool_use.py\""
timeout = 10

[[hooks.PostToolUse]]
matcher = "^Bash$"

[[hooks.PostToolUse.hooks]]
type = "command"
command = "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/orchestrator_post_tool_use.py\""
timeout = 10

[[hooks.Stop]]

[[hooks.Stop.hooks]]
type = "command"
command = "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/orchestrator_stop.py\""
timeout = 10
