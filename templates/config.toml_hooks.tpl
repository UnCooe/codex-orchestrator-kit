[[hooks.SessionStart]]
matcher = ".*"

[[hooks.SessionStart.hooks]]
type = "command"
command = "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/workspace_lifecycle_shadow.py\""
timeout = 10
statusMessage = "Recording workspace lifecycle"

[[hooks.UserPromptSubmit]]
matcher = ".*"

[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/workspace_lifecycle_shadow.py\""
timeout = 10
statusMessage = "Recording workspace lifecycle"

[[hooks.PreToolUse]]
matcher = "^(Bash|exec_command)$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/critical_action_gate.py\""
timeout = 10
statusMessage = "Checking critical action gate"

[[hooks.Stop]]
matcher = ".*"

[[hooks.Stop.hooks]]
type = "command"
command = "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/workspace_lifecycle_shadow.py\""
timeout = 10
statusMessage = "Recording workspace lifecycle"
