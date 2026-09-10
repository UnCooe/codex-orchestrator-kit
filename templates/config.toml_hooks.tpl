[[hooks.PreToolUse]]
matcher = "^(Bash|exec_command)$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/critical_action_gate.py\""
timeout = 10
statusMessage = "Checking critical action gate"
