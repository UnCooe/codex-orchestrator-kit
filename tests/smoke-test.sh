#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT

CODEX_HOME="$TMP_ROOT/.codex"
AGENTS_HOME="$TMP_ROOT/.agents"
export CODEX_HOME AGENTS_HOME
mkdir -p "$CODEX_HOME" "$AGENTS_HOME"
cp "$ROOT/tests/fixtures/hooks.before.json" "$CODEX_HOME/hooks.json"
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN=python
else
  echo "python3 or python is required" >&2
  exit 1
fi
PYTHON_REAL=$(command -v "$PYTHON_BIN")

"$ROOT/install.sh" --codex-home "$CODEX_HOME" --agents-home "$AGENTS_HOME" >/dev/null

test -f "$AGENTS_HOME/skills/orchestrator/SKILL.md"
test -f "$AGENTS_HOME/skills/orchestrator-observability/SKILL.md"
if [[ -e "$AGENTS_HOME/skills/project-specific-observability/SKILL.md" ]]; then
  echo "Default install should not install project-specific reviewer packs" >&2
  exit 1
fi
test -f "$CODEX_HOME/hooks/orchestrator_user_prompt.py"
test -f "$CODEX_HOME/agents/critic.toml"

CONFIG_CODEX="$TMP_ROOT/config-codex"
mkdir -p "$CONFIG_CODEX/skills"
cat > "$CONFIG_CODEX/config.toml" <<'EOF'
approval_policy = 'never'

[features]
codex_hooks = true
EOF
"$ROOT/install.sh" --codex-home "$CONFIG_CODEX" --hook-mode config_toml --skills-mode codex >/dev/null
test -f "$CONFIG_CODEX/skills/orchestrator/SKILL.md"
test -f "$CONFIG_CODEX/skills/orchestrator-observability/SKILL.md"
test -f "$CONFIG_CODEX/hooks/orchestrator_user_prompt.py"
"$PYTHON_BIN" - "$CONFIG_CODEX/config.toml" <<'PY'
import tomllib
import sys

with open(sys.argv[1], "rb") as stream:
    obj = tomllib.load(stream)
hooks = obj.get("hooks", {})
for required in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"):
    if required not in hooks:
        raise SystemExit(f"config.toml mode missing hook: {required}")
PY
"$ROOT/install.sh" --codex-home "$CONFIG_CODEX" --hook-mode config_toml --skills-mode codex >/dev/null
"$PYTHON_BIN" - "$CONFIG_CODEX/config.toml" <<'PY'
import pathlib
import tomllib
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
obj = tomllib.loads(text)
for required in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"):
    entries = obj["hooks"].get(required, [])
    if len(entries) != 1:
        raise SystemExit(f"config.toml install is not idempotent for {required}: {len(entries)} entries")
PY

EXISTING_HOOKS_CODEX="$TMP_ROOT/config-codex-existing-hooks"
mkdir -p "$EXISTING_HOOKS_CODEX/skills"
cat > "$EXISTING_HOOKS_CODEX/config.toml" <<'EOF'
approval_policy = "never"

[features]
hooks = true

[[hooks.SessionStart]]
matcher = "startup|resume"

[[hooks.SessionStart.hooks]]
type = "command"
command = "python3 /Users/example/.codex/hooks/aiserver_session_context.py"
timeout = 10

[[hooks.UserPromptSubmit]]

[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = "python3 /Users/example/.codex/hooks/aiserver_session_context.py"
timeout = 10

[tui.model_availability_nux]
"gpt-5.5" = 1
EOF
"$ROOT/install.sh" --codex-home "$EXISTING_HOOKS_CODEX" --hook-mode config_toml --skills-mode codex >/dev/null
"$PYTHON_BIN" - "$EXISTING_HOOKS_CODEX/config.toml" <<'PY'
import pathlib
import tomllib
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
obj = tomllib.loads(text)
user_prompt_entries = obj["hooks"]["UserPromptSubmit"]
if len(user_prompt_entries) != 1:
    raise SystemExit(f"expected one UserPromptSubmit block, got {len(user_prompt_entries)}")
commands = [hook["command"] for hook in user_prompt_entries[0]["hooks"]]
if not any("aiserver_session_context.py" in command for command in commands):
    raise SystemExit("missing existing aiserver hook after config merge")
if not any("orchestrator_user_prompt.py" in command for command in commands):
    raise SystemExit("missing orchestrator user prompt hook after config merge")
for required in ("PreToolUse", "PostToolUse", "Stop"):
    if required not in obj["hooks"]:
        raise SystemExit(f"missing {required} after config merge")
PY

if "$PYTHON_BIN" - "$ROOT/templates/review-profile.toml" <<'PY'
import pathlib
import re
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r"enabled_reviewers\s*=\s*\[(.*?)\]", text, re.S)
if not match:
    raise SystemExit(1)
body = match.group(1)
reviewers = re.findall(r'"([^"]+)"', body)
if reviewers != ["subagent-workflow"]:
    raise SystemExit(1)
PY
then
  :
else
  echo "Default review-profile template should enable only subagent-workflow" >&2
  exit 1
fi

DEMO_PACK="$TMP_ROOT/demo-reviewer-pack"
mkdir -p "$DEMO_PACK/skills/demo-observability/scripts"
cat > "$DEMO_PACK/skills/demo-observability/SKILL.md" <<'EOF'
---
name: demo-observability
description: Smoke test external reviewer pack.
---

# Demo Observability
EOF
printf '%s\n' 'print("demo")' > "$DEMO_PACK/skills/demo-observability/scripts/demo.py"
"$ROOT/install-reviewer-pack.sh" --pack "$DEMO_PACK" --agents-home "$AGENTS_HOME" >/dev/null
test -f "$AGENTS_HOME/skills/demo-observability/SKILL.md"
test -f "$AGENTS_HOME/skills/demo-observability/scripts/demo.py"
"$ROOT/install-reviewer-pack.sh" --pack "$DEMO_PACK" --codex-home "$CONFIG_CODEX" --skills-mode codex >/dev/null
test -f "$CONFIG_CODEX/skills/demo-observability/SKILL.md"
test -f "$CONFIG_CODEX/skills/demo-observability/scripts/demo.py"

REGISTERED_PROJECT="$TMP_ROOT/registered-project"
mkdir -p "$REGISTERED_PROJECT"
"$ROOT/register-project.sh" --project "$REGISTERED_PROJECT" --project-id smoke-project --reviewers subagent-workflow,demo-reviewer >/dev/null
test -f "$REGISTERED_PROJECT/.codex/project-context.md"
grep -q 'project_id = "smoke-project"' "$REGISTERED_PROJECT/.codex/review-profile.toml"
grep -q '"subagent-workflow"' "$REGISTERED_PROJECT/.codex/review-profile.toml"
grep -q '"demo-reviewer"' "$REGISTERED_PROJECT/.codex/review-profile.toml"

grep -q 'C:/legacy/orchestrator_user_prompt.py' "$CODEX_HOME/hooks.json" && {
  echo "Legacy orchestrator_user_prompt entry should not be preserved" >&2
  exit 1
}
grep -q 'legacy.*orchestrator_pre_tool_use\.py' "$CODEX_HOME/hooks.json" && {
  echo "Legacy orchestrator_pre_tool_use entry should not be preserved" >&2
  exit 1
}

grep -q 'pre-existing-user-prompt.py' "$CODEX_HOME/hooks.json"
grep -q 'pre-existing-pre-tool.py' "$CODEX_HOME/hooks.json"
grep -q 'orchestrator_user_prompt.py' "$CODEX_HOME/hooks.json"

"$ROOT/install.sh" --codex-home "$CODEX_HOME" --agents-home "$AGENTS_HOME" >/dev/null
COUNT=$(grep -o 'orchestrator_user_prompt.py' "$CODEX_HOME/hooks.json" | wc -l | tr -d ' ')
if [[ "$COUNT" != "1" ]]; then
  echo "Install is not idempotent: orchestrator_user_prompt.py appears $COUNT times" >&2
  exit 1
fi

DRY_CODEX="$TMP_ROOT/dry-codex"
DRY_AGENTS="$TMP_ROOT/dry-agents"
"$ROOT/install.sh" --codex-home "$DRY_CODEX" --agents-home "$DRY_AGENTS" --dry-run >/dev/null
if [[ -e "$DRY_CODEX" || -e "$DRY_AGENTS" ]]; then
  echo "Dry run created target directories" >&2
  exit 1
fi

PRE_TOOL="$CODEX_HOME/hooks/orchestrator_pre_tool_use.py"
POST_TOOL="$CODEX_HOME/hooks/orchestrator_post_tool_use.py"
STOP_HOOK="$CODEX_HOME/hooks/orchestrator_stop.py"
SESSIONS_ROOT="$CODEX_HOME/sessions"
mkdir -p "$SESSIONS_ROOT"

TRANSCRIPT_FOR_HOOKS="$SESSIONS_ROOT/hook-events.jsonl"
SHELL_COMMAND_BLOCK=$(printf '%s' "{\"tool_name\":\"shell_command\",\"tool_input\":{\"command\":\"find .\"},\"transcript_path\":\"$TRANSCRIPT_FOR_HOOKS\"}" | "$PYTHON_BIN" "$PRE_TOOL")
"$PYTHON_BIN" - "$SHELL_COMMAND_BLOCK" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
if obj.get("decision") != "block":
    raise SystemExit("shell_command broad scan was not blocked")
PY

POWERSHELL_BLOCK=$(printf '%s' "{\"tool_name\":\"powershell\",\"tool_input\":{\"command\":\"Get-ChildItem -Recurse\"},\"transcript_path\":\"$TRANSCRIPT_FOR_HOOKS\"}" | "$PYTHON_BIN" "$PRE_TOOL")
"$PYTHON_BIN" - "$POWERSHELL_BLOCK" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
if obj.get("decision") != "block":
    raise SystemExit("PowerShell broad scan was not blocked")
PY

POST_SHELL_BLOCK=$("$PYTHON_BIN" -c "import json; print(json.dumps({'tool_name':'shell_command','tool_response':'x'*60001,'transcript_path':'$TRANSCRIPT_FOR_HOOKS'}))" | "$PYTHON_BIN" "$POST_TOOL")
"$PYTHON_BIN" - "$POST_SHELL_BLOCK" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
if obj.get("decision") != "block" or obj.get("continue") is not False:
    raise SystemExit("Large shell_command output was not blocked")
PY

POST_NON_SHELL=$("$PYTHON_BIN" -c "import json; print(json.dumps({'tool_name':'python','tool_response':'x'*60001,'transcript_path':'$TRANSCRIPT_FOR_HOOKS'}))" | "$PYTHON_BIN" "$POST_TOOL")
if [[ -n "$POST_NON_SHELL" ]]; then
  echo "Large non-shell output should not block" >&2
  exit 1
fi

STRUCTURED_STOP=$(cat <<JSON | "$PYTHON_BIN" "$STOP_HOOK"
{"transcript_path":"$TRANSCRIPT_FOR_HOOKS","last_assistant_message":"implemented hook behavior.\nverification_status=pass\nreview_status=not_required\nsubagent_status=closed\nrisk_status=low"}
JSON
)
"$PYTHON_BIN" - "$STRUCTURED_STOP" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
if obj.get("continue") is not True:
    raise SystemExit("Structured acceptance stop payload did not pass")
PY

PARTIAL_VERIFICATION_STOP=$(cat <<'JSON' | "$PYTHON_BIN" "$STOP_HOOK"
{"last_assistant_message":"implemented hook behavior.\nverification_status=partial；ran smoke checks but deferred live client validation.\nreview_status=pass\nsubagent_status=not_used\nrisk_status=medium；live client validation remains."}
JSON
)
"$PYTHON_BIN" - "$PARTIAL_VERIFICATION_STOP" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
if obj.get("continue") is not True:
    raise SystemExit("Explained partial verification should pass with non-low risk")
PY

LOW_RISK_PARTIAL_STOP=$(cat <<'JSON' | "$PYTHON_BIN" "$STOP_HOOK"
{"last_assistant_message":"implemented hook behavior.\nverification_status=partial；ran smoke checks but deferred live client validation.\nreview_status=pass\nsubagent_status=not_used\nrisk_status=low"}
JSON
)
"$PYTHON_BIN" - "$LOW_RISK_PARTIAL_STOP" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
if obj.get("decision") != "block" or "partial/not_run/blocked" not in obj.get("reason", ""):
    raise SystemExit("Partial verification with low risk should be blocked")
PY

PARTIAL_STOP=$(cat <<'JSON' | "$PYTHON_BIN" "$STOP_HOOK"
{"last_assistant_message":"implemented hook behavior.\nverification_status=pass\nreview_status=pass"}
JSON
)
"$PYTHON_BIN" - "$PARTIAL_STOP" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
if obj.get("decision") != "block" or "Acceptance footer is incomplete" not in obj.get("reason", ""):
    raise SystemExit("Partial acceptance footer should be blocked")
PY

SPACE_PY_DIR="$TMP_ROOT/python with space"
mkdir -p "$SPACE_PY_DIR"
SPACE_PY="$SPACE_PY_DIR/python3"
cat > "$SPACE_PY" <<EOF
#!/usr/bin/env bash
exec "$PYTHON_REAL" "\$@"
EOF
chmod +x "$SPACE_PY"
OVERRIDE_CODEX="$TMP_ROOT/override-codex"
OVERRIDE_AGENTS="$TMP_ROOT/override-agents"
CODEX_ORCHESTRATOR_PYTHON="$SPACE_PY" "$ROOT/install.sh" --codex-home "$OVERRIDE_CODEX" --agents-home "$OVERRIDE_AGENTS" >/dev/null
"$PYTHON_BIN" - "$OVERRIDE_CODEX/hooks.json" "$SPACE_PY" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    hooks = json.load(stream)
command = hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
expected = f'"{sys.argv[2]}" '
if not command.startswith(expected):
    raise SystemExit(f"Python override with spaces was not quoted: {command}")
PY

mkdir -p "$SESSIONS_ROOT"
cp "$ROOT/tests/fixtures/session-observability.jsonl" "$SESSIONS_ROOT/session-smoke.jsonl"
mkdir -p "$SESSIONS_ROOT/2026/05/03"
cat > "$SESSIONS_ROOT/2026/05/03/rollout-nested-meta-session.jsonl" <<'JSONL'
{"type":"session_meta","payload":{"id":"nested-meta-session","cwd":"/tmp/repo/meta-project"},"timestamp":"2026-05-02T00:00:11Z"}
{"payload":{"type":"message","role":"assistant","content":"implemented nested metadata change. Done."},"timestamp":"2026-05-02T00:00:12Z"}
JSONL
PROJECT_CONTEXT_DIR="$TMP_ROOT/project-context-smoke"
PROJECT_CONTEXT_NAME=$(basename "$PROJECT_CONTEXT_DIR")
mkdir -p "$PROJECT_CONTEXT_DIR/.codex"
cat > "$PROJECT_CONTEXT_DIR/.codex/project-context.md" <<'EOF'
项目目标: Smoke validation project
当前阶段: test fixture
核心模块: observability
常用命令: smoke-test
验收标准: reports include redacted context
已知风险: api_key=supersecret
裸 token: sk-1234567890abcdefghijklmnopqrstuvwxyz
EOF
cat > "$SESSIONS_ROOT/2026/05/03/rollout-project-context-session.jsonl" <<JSONL
{"type":"session_meta","payload":{"id":"project-context-session","cwd":"$PROJECT_CONTEXT_DIR"},"timestamp":"2026-05-02T00:00:13Z"}
{"payload":{"type":"message","role":"assistant","content":"implemented context fixture. Verification: smoke test."},"timestamp":"2026-05-02T00:00:14Z"}
JSONL

"$PYTHON_BIN" "$ROOT/tests/test_session_facts_since_days.py"
"$PYTHON_BIN" "$ROOT/tests/test_acceptance_footer_and_triage.py"

"$PYTHON_BIN" "$AGENTS_HOME/skills/orchestrator-observability/scripts/analyze.py" \
  --codex-home "$CODEX_HOME" \
  --since-days 30 \
  --report-id smoke > /tmp/orchestrator-observability-smoke-report.json

REPORT_DIR="$CODEX_HOME/orchestrator-observability/reports"
JSON_REPORT="$REPORT_DIR/orchestrator-observability-smoke.json"
test -f "$JSON_REPORT"
"$PYTHON_BIN" - "$JSON_REPORT" "$PROJECT_CONTEXT_DIR" "$PROJECT_CONTEXT_NAME" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    obj = json.load(stream)
if obj["orchestrator_event_breakdown"]["orchestrator_pre_tool_use"]["total"] < 1:
    raise SystemExit("Analyzer breakdown missing pre_tool_use")
if obj["totals"].get("token_count_max", 0) < 1:
    raise SystemExit("Analyzer did not extract token_count totals")
if "acme-tooling" not in obj["projects"]:
    raise SystemExit("Analyzer missing cwd project aggregation")
if "meta-project" not in obj["projects"]:
    raise SystemExit("Analyzer did not inherit cwd from nested session_meta")
if sys.argv[3] not in obj["projects"]:
    raise SystemExit("Analyzer did not aggregate project context fixture project")
context_key = f"project:{sys.argv[3]}"
context = obj.get("project_context", {}).get(context_key)
if not context or not context.get("available"):
    raise SystemExit("Analyzer did not include available project context")
if "supersecret" in context.get("summary", "") or "sk-1234567890abcdefghijklmnopqrstuvwxyz" in context.get("summary", "") or "[redacted]" not in context.get("summary", ""):
    raise SystemExit("Analyzer project context summary was not redacted")
if obj.get("tool_surface", {}).get("families", {}).get("shell_like", 0) < 1:
    raise SystemExit("Analyzer missing tool_surface shell_like family")
if not any(f["session_id"] == "nested-meta-session" and f["cwd"].endswith("meta-project") for f in obj["facts"]):
    raise SystemExit("Analyzer did not inherit nested session id/cwd facts")
if not any(f.get("event_type") == "orchestrator_stop" and f.get("acceptance", {}).get("source") == "structured" for f in obj["facts"]):
    raise SystemExit("Analyzer did not preserve structured acceptance fields")
if not any(f.get("event_type") == "orchestrator_pre_tool_use" and f.get("tool_name") == "shell_command" and f.get("normalized_tool_name") == "shell_command" for f in obj["facts"]):
    raise SystemExit("Analyzer did not preserve orchestrator event tool_name")
if obj.get("structured_acceptance", {}).get("structured_acceptance_verified", 0) < 1:
    raise SystemExit("Analyzer structured acceptance counters did not count verified footer")
if obj.get("acceptance", {}).get("work_done_missing_acceptance_footer", 0) < 1:
    raise SystemExit("Analyzer did not count missing acceptance footers")
if not obj.get("trend", {}).get("overall"):
    raise SystemExit("Analyzer trend summary missing")
if not obj.get("suggestions"):
    raise SystemExit("Analyzer suggestions missing")
if not any("verification" in item.lower() for item in obj["suggestions"]):
    raise SystemExit("Analyzer suggestions missing verification guidance")
if any("no sessions were detected" in item.lower() for item in obj["suggestions"]):
    raise SystemExit("Analyzer emitted false session-extraction suggestion")
if any("please implement" in json.dumps(v) for v in obj["facts"]):
    raise SystemExit("Analyzer leaked assistant content in facts")
report_text = json.dumps(obj)
if any(path in report_text for path in ("C:/repo", "D:/repo", "/tmp/repo", sys.argv[2])):
    raise SystemExit("Analyzer leaked full local paths without --include-paths")
PY

"$PYTHON_BIN" "$AGENTS_HOME/skills/orchestrator-observability/scripts/subagent_workflow.py" \
  --codex-home "$CODEX_HOME" \
  --since-days 30 \
  --report-id smoke > /tmp/subagent-workflow-smoke-report.json

SUBAGENT_WORKFLOW_REPORT="$REPORT_DIR/subagent-workflow-smoke.json"
test -f "$SUBAGENT_WORKFLOW_REPORT"
"$PYTHON_BIN" - "$SUBAGENT_WORKFLOW_REPORT" "$PROJECT_CONTEXT_DIR" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    obj = json.load(stream)
if obj.get("report_type") != "subagent_workflow":
    raise SystemExit("Subagent workflow report type missing")
if obj.get("subagents", {}).get("spawn", 0) < 1:
    raise SystemExit("Subagent workflow report did not count spawn activity")
if obj.get("workflow_signals", {}).get("broad_scan_blocks", 0) < 1:
    raise SystemExit("Subagent workflow report did not count broad scan blocks")
report_text = json.dumps(obj, ensure_ascii=False)
if sys.argv[2] in report_text or "supersecret" in report_text or "sk-1234567890abcdefghijklmnopqrstuvwxyz" in report_text:
    raise SystemExit("Subagent workflow report leaked project context path or secret")
PY

TRIAGE="$AGENTS_HOME/skills/orchestrator-observability/scripts/triage.py"
USER_PROMPT="$CODEX_HOME/hooks/orchestrator_user_prompt.py"

HIGH_REPORT="$REPORT_DIR/orchestrator-observability-high-smoke.json"
cat > "$HIGH_REPORT" <<'JSON'
{
  "totals": {
    "orchestrator_events": 10,
    "blocked_orchestrator_events": 4
  },
  "subagents": {
    "lifecycle_delta_spawn_minus_close": 0
  },
  "acceptance": {
    "work_done_missing_verification": 0,
    "work_done_missing_subagent_status": 0,
    "work_done_missing_acceptance_footer": 2,
    "acceptance_footer_partial": 1
  },
  "trend": {
    "overall": "worsening",
    "signals": [
      {
        "metric": "work_done_missing_acceptance_footer",
        "status": "worsened",
        "current": 2,
        "previous": 0,
        "delta": 2
      }
    ]
  },
  "suggestions": ["High blocked hook ratio needs review."]
}
JSON
HIGH_TRIAGE=$("$PYTHON_BIN" "$TRIAGE" --codex-home "$CODEX_HOME" --since-days 3 --report "$HIGH_REPORT")
"$PYTHON_BIN" - "$HIGH_TRIAGE" "$CODEX_HOME" <<'PY'
import json
import pathlib
import sys

obj = json.loads(sys.argv[1])
if obj["severity"] != "high":
    raise SystemExit(f"Expected high triage, got {obj}")
alert = pathlib.Path(sys.argv[2]) / "orchestrator-observability" / "triage" / "alerts" / "high-risk-current.json"
if not alert.exists():
    raise SystemExit("High-risk current alert was not written")
PY

HIGH_REMINDER=$(CODEX_HOME="$CODEX_HOME" "$PYTHON_BIN" "$USER_PROMPT" <<'JSON'
{"prompt":"hello","cwd":"/safe/project"}
JSON
)
"$PYTHON_BIN" - "$HIGH_REMINDER" "$HIGH_REPORT" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
context = obj["hookSpecificOutput"]["additionalContext"]
if "ORCHESTRATOR HIGH-RISK ALERT" not in context:
    raise SystemExit("High-risk reminder missing")
if sys.argv[2] in context:
    raise SystemExit("High-risk reminder leaked full report path")
PY

SUPPRESSED=$(CODEX_HOME="$CODEX_HOME" "$PYTHON_BIN" "$USER_PROMPT" <<'JSON'
{"prompt":"hello again","cwd":"/safe/project"}
JSON
)
if [[ -n "$SUPPRESSED" ]]; then
  echo "High-risk reminder should be suppressed within 24 hours" >&2
  exit 1
fi

LOW_REPORT="$REPORT_DIR/orchestrator-observability-low-smoke.json"
cat > "$LOW_REPORT" <<'JSON'
{
  "totals": {
    "orchestrator_events": 3,
    "blocked_orchestrator_events": 0
  },
  "subagents": {
    "lifecycle_delta_spawn_minus_close": 0
  },
  "acceptance": {
    "work_done_missing_verification": 0,
    "work_done_missing_subagent_status": 0,
    "work_done_missing_acceptance_footer": 0,
    "acceptance_footer_partial": 0
  },
  "trend": {
    "overall": "stable",
    "signals": []
  },
  "project_context_available": true,
  "suggestions": ["Update guidance wording."]
}
JSON
cat > "$REPORT_DIR/orchestrator-observability-low-smoke.patch.diff" <<'DIFF'
diff --git a/docs/proposal-guidance.md b/docs/proposal-guidance.md
--- a/docs/proposal-guidance.md
+++ b/docs/proposal-guidance.md
@@ -1 +1,2 @@
 proposal guidance
+short confirmation card guidance
DIFF
LOW_TRIAGE=$("$PYTHON_BIN" "$TRIAGE" --codex-home "$CODEX_HOME" --since-days 3 --report "$LOW_REPORT")
"$PYTHON_BIN" - "$LOW_TRIAGE" <<'PY'
import json
import pathlib
import sys

obj = json.loads(sys.argv[1])
if obj["severity"] != "low" or "proposal_path" not in obj:
    raise SystemExit(f"Expected low proposal, got {obj}")
proposal = pathlib.Path(obj["proposal_path"])
expected = {"manifest.json", "patch.diff", "proposal.md", "rollback.md", "validation.md"}
actual = {p.name for p in proposal.iterdir() if p.is_file()}
if actual != expected:
    raise SystemExit(f"Unexpected proposal files: {actual}")
PY

BAD_REPORT="$REPORT_DIR/orchestrator-observability-badpatch-smoke.json"
cp "$LOW_REPORT" "$BAD_REPORT"
cat > "$REPORT_DIR/orchestrator-observability-badpatch-smoke.patch.diff" <<'DIFF'
diff --git a/hooks/orchestrator_user_prompt.py b/hooks/orchestrator_user_prompt.py
--- a/hooks/orchestrator_user_prompt.py
+++ b/hooks/orchestrator_user_prompt.py
@@ -1 +1,2 @@
 import json
+print("unsafe")
DIFF
BAD_TRIAGE=$("$PYTHON_BIN" "$TRIAGE" --codex-home "$CODEX_HOME" --since-days 3 --report "$BAD_REPORT")
"$PYTHON_BIN" - "$BAD_TRIAGE" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
if obj["severity"] != "none":
    raise SystemExit(f"Unsafe patch should not be low-risk: {obj}")
PY

PRIVACY_REPORT="$REPORT_DIR/orchestrator-observability-privacy-smoke.json"
cat > "$PRIVACY_REPORT" <<'JSON'
{
  "totals": {
    "orchestrator_events": 1,
    "blocked_orchestrator_events": 0
  },
  "subagents": {
    "lifecycle_delta_spawn_minus_close": 0
  },
  "acceptance": {
    "work_done_missing_verification": 0,
    "work_done_missing_subagent_status": 0
  },
  "facts": [{"cwd": "C:/repo/private-project"}],
  "suggestions": ["Update guidance wording."]
}
JSON
PRIVACY_TRIAGE=$("$PYTHON_BIN" "$TRIAGE" --codex-home "$CODEX_HOME" --since-days 3 --report "$PRIVACY_REPORT")
"$PYTHON_BIN" - "$PRIVACY_TRIAGE" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
if obj["severity"] != "high" or obj["summary"]["flags"].get("privacy_hit_count", 0) < 1:
    raise SystemExit(f"Privacy path leak should be high risk: {obj}")
PY

BENIGN_AUTOMATION_REPORT="$REPORT_DIR/orchestrator-observability-benign-automation-smoke.json"
cat > "$BENIGN_AUTOMATION_REPORT" <<'JSON'
{
  "totals": {
    "orchestrator_events": 1,
    "blocked_orchestrator_events": 0
  },
  "subagents": {
    "lifecycle_delta_spawn_minus_close": 0
  },
  "acceptance": {
    "work_done_missing_verification": 0,
    "work_done_missing_subagent_status": 0,
    "work_done_missing_acceptance_footer": 0,
    "acceptance_footer_partial": 0
  },
  "trend": {
    "overall": "stable",
    "signals": []
  },
  "suggestions": ["Automation looks stable; reduce error message verbosity in summaries."]
}
JSON
BENIGN_AUTOMATION_TRIAGE=$("$PYTHON_BIN" "$TRIAGE" --codex-home "$CODEX_HOME" --since-days 3 --report "$BENIGN_AUTOMATION_REPORT")
"$PYTHON_BIN" - "$BENIGN_AUTOMATION_TRIAGE" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
if obj["severity"] != "none":
    raise SystemExit(f"Benign automation wording should not trigger high risk: {obj}")
PY

RECOVERING_REPORT="$REPORT_DIR/orchestrator-observability-recovering-smoke.json"
cat > "$RECOVERING_REPORT" <<'JSON'
{
  "totals": {
    "orchestrator_events": 8,
    "blocked_orchestrator_events": 2
  },
  "subagents": {
    "lifecycle_delta_spawn_minus_close": 0
  },
  "acceptance": {
    "work_done_missing_verification": 0,
    "work_done_missing_subagent_status": 0,
    "work_done_missing_acceptance_footer": 1,
    "acceptance_footer_partial": 0
  },
  "trend": {
    "overall": "improving",
    "signals": [
      {
        "metric": "work_done_missing_acceptance_footer",
        "status": "improved",
        "current": 1,
        "previous": 3,
        "delta": -2
      }
    ]
  },
  "project_context_available": true,
  "suggestions": ["Tighten footer reminders."]
}
JSON
cat > "$REPORT_DIR/orchestrator-observability-recovering-smoke.patch.diff" <<'DIFF'
diff --git a/docs/proposal-guidance.md b/docs/proposal-guidance.md
--- a/docs/proposal-guidance.md
+++ b/docs/proposal-guidance.md
@@ -1 +1,2 @@
 proposal guidance
+footer reminder follow-up
DIFF
RECOVERING_TRIAGE=$("$PYTHON_BIN" "$TRIAGE" --codex-home "$CODEX_HOME" --since-days 3 --report "$RECOVERING_REPORT")
"$PYTHON_BIN" - "$RECOVERING_TRIAGE" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
if obj["severity"] != "low" or obj["summary"].get("classification") != "improved":
    raise SystemExit(f"Recovering trend should downgrade to low proposal: {obj}")
PY

APPLY_PATCH_REPORT="$REPORT_DIR/orchestrator-observability-applypatch-smoke.json"
cp "$LOW_REPORT" "$APPLY_PATCH_REPORT"
cat > "$REPORT_DIR/orchestrator-observability-applypatch-smoke.patch.diff" <<'DIFF'
*** Begin Patch
*** Update File: docs/proposal-guidance.md
@@
-proposal guidance
+proposal guidance with confirmation card
*** End Patch
DIFF
APPLY_PATCH_TRIAGE=$("$PYTHON_BIN" "$TRIAGE" --codex-home "$CODEX_HOME" --since-days 3 --report "$APPLY_PATCH_REPORT")
"$PYTHON_BIN" - "$APPLY_PATCH_TRIAGE" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
if obj["severity"] != "low":
    raise SystemExit(f"Safe apply_patch proposal should be low risk: {obj}")
PY

LIVE_CONFIG_REPORT="$REPORT_DIR/orchestrator-observability-liveconfig-smoke.json"
cp "$LOW_REPORT" "$LIVE_CONFIG_REPORT"
cat > "$REPORT_DIR/orchestrator-observability-liveconfig-smoke.patch.diff" <<'DIFF'
diff --git a/.codex/prompts/automation.yaml b/.codex/prompts/automation.yaml
--- a/.codex/prompts/automation.yaml
+++ b/.codex/prompts/automation.yaml
@@ -1 +1,2 @@
 prompt: old
+prompt: new
DIFF
LIVE_CONFIG_TRIAGE=$("$PYTHON_BIN" "$TRIAGE" --codex-home "$CODEX_HOME" --since-days 3 --report "$LIVE_CONFIG_REPORT")
"$PYTHON_BIN" - "$LIVE_CONFIG_TRIAGE" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
if obj["severity"] != "none":
    raise SystemExit(f"Live config patch should not be low risk: {obj}")
PY

echo "smoke-test.sh passed"
