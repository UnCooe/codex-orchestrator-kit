{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/orchestrator_user_prompt.py\"",
            "timeout": 10,
            "statusMessage": "Checking orchestration policy"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/orchestrator_pre_tool_use.py\"",
            "timeout": 10,
            "statusMessage": "Checking delegation guardrail"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "^Bash$",
        "hooks": [
          {
            "type": "command",
            "command": "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/orchestrator_post_tool_use.py\"",
            "timeout": 10
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "{{PYTHON_EXE}} \"{{CODEX_HOME}}/hooks/orchestrator_stop.py\"",
            "timeout": 10,
            "statusMessage": "Checking acceptance gate"
          }
        ]
      }
    ]
  }
}
