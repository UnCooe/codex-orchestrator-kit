{
  "hooks": {
    "SessionStart": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/workspace_lifecycle_shadow.py\"",
            "timeout": 10,
            "statusMessage": "Recording workspace lifecycle"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/workspace_lifecycle_shadow.py\"",
            "timeout": 10,
            "statusMessage": "Recording workspace lifecycle"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "^(Bash|exec_command)$",
        "hooks": [
          {
            "type": "command",
            "command": "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/critical_action_gate.py\"",
            "timeout": 10,
            "statusMessage": "Checking critical action gate"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/workspace_lifecycle_shadow.py\"",
            "timeout": 10,
            "statusMessage": "Recording workspace lifecycle"
          }
        ]
      }
    ]
  }
}
