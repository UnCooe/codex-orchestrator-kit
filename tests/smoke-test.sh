#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT

CODEX_HOME="$TMP_ROOT/.codex"
AGENTS_HOME="$TMP_ROOT/.agents"
FIXTURE_SINCE_DAYS=36500
export CODEX_HOME AGENTS_HOME
mkdir -p "$CODEX_HOME" "$AGENTS_HOME"
cp "$ROOT/tests/fixtures/hooks.before.json" "$CODEX_HOME/hooks.json"
mkdir -p "$CODEX_HOME/agents"
printf '%s\n' 'name = "codebase_research_explorer"' > "$CODEX_HOME/agents/codebase-research-explorer.toml"
mkdir -p "$AGENTS_HOME/skills/orchestrator"
printf '%s\n' 'stale' > "$AGENTS_HOME/skills/orchestrator/stale-managed-file.txt"
PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
  then
    PYTHON_BIN="$candidate"
    break
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python >= 3.11 is required; checked python3.13, python3.12, python3.11, python3" >&2
  exit 1
fi
PYTHON_REAL=$(command -v "$PYTHON_BIN")
VERIFY_SKILL_INSTALL="$ROOT/scripts/verify_skill_install.py"
MANAGED_SKILLS=(
  orchestrator
  orchestrator-observability
  feature-workflow
  review-workflow
  merge-workflow
  predeploy-workflow
  merge-safety
)

assert_managed_skills_only() {
  local skills_root=$1
  for skill in "${MANAGED_SKILLS[@]}"; do
    test -f "$skills_root/$skill/SKILL.md"
  done
  "$PYTHON_BIN" - "$skills_root" "${MANAGED_SKILLS[@]}" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
expected = set(sys.argv[2:])
actual = {path.name for path in root.iterdir() if path.is_dir()}
if actual != expected:
    raise SystemExit(f"managed skill catalog mismatch: {sorted(actual)}")
PY
}

assert_skill_parity() {
  local target_root=$1
  "$PYTHON_BIN" "$VERIFY_SKILL_INSTALL" --source "$ROOT/skills" --target "$target_root"
}

assert_router_v3_hook_runtime() {
  local hooks_root=$1
  "$PYTHON_BIN" - "$hooks_root" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
actual = {path.name for path in root.iterdir() if path.is_file()}
expected = {"critical_action_gate.py"}
if actual != expected:
    raise SystemExit(f"Router v3 hook runtime mismatch: {sorted(actual)}")
PY
}

assert_router_v3_hooks_json() {
  local hooks_path=$1
  "$PYTHON_BIN" - "$hooks_path" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    obj = json.load(stream)

gate_entries = []
commands = []
for event, entries in obj.get("hooks", {}).items():
    for entry in entries:
        matcher = entry.get("matcher", "")
        for hook in entry.get("hooks", []):
            command = str(hook.get("command", ""))
            commands.append(command)
            if "critical_action_gate.py" in command:
                gate_entries.append((event, matcher, command))

if len(gate_entries) != 1 or gate_entries[0][:2] != ("PreToolUse", "^(Bash|exec_command)$"):
    raise SystemExit(f"expected one command PreTool critical gate, got: {gate_entries}")
if any("pilot_receipt_gate.py" in command for command in commands):
    raise SystemExit("retired pilot gate must not be registered")
if any(matcher == "*" for _event, matcher, _command in gate_entries):
    raise SystemExit(f"managed hooks must not use wildcard matcher: {gate_entries}")
if any(matcher == "^apply_patch$" for _event, matcher, _command in gate_entries):
    raise SystemExit(f"critical gate must not be registered on apply_patch: {gate_entries}")
for legacy in (
    "orchestrator_user_prompt.py",
    "orchestrator_pre_tool_use.py",
    "orchestrator_post_tool_use.py",
    "orchestrator_stop.py",
):
    if any(legacy in command for command in commands):
        raise SystemExit(f"legacy managed hook still registered: {legacy}")
for project_hook in ("pre-existing-user-prompt.py", "pre-existing-pre-tool.py"):
    if not any(project_hook in command for command in commands):
        raise SystemExit(f"project hook was not preserved: {project_hook}")
PY
}

DEFAULT_DRY_CODEX="$TMP_ROOT/default-dry-codex"
DEFAULT_DRY_AGENTS="$TMP_ROOT/default-dry-agents"
"$ROOT/install.sh" --codex-home "$DEFAULT_DRY_CODEX" --agents-home "$DEFAULT_DRY_AGENTS" >/dev/null
if [[ -e "$DEFAULT_DRY_CODEX" || -e "$DEFAULT_DRY_AGENTS" ]]; then
  echo "Default install without --apply created target directories" >&2
  exit 1
fi
if "$ROOT/install.sh" --codex-home "$DEFAULT_DRY_CODEX" --agents-home "$DEFAULT_DRY_AGENTS" --apply >/dev/null 2>&1; then
  echo "Apply mode without explicit modes should fail" >&2
  exit 1
fi

"$ROOT/install.sh" --codex-home "$CODEX_HOME" --agents-home "$AGENTS_HOME" --apply --hook-mode auto --skills-mode auto >/dev/null

assert_managed_skills_only "$AGENTS_HOME/skills"
assert_skill_parity "$AGENTS_HOME/skills"
test ! -e "$AGENTS_HOME/skills/orchestrator/stale-managed-file.txt"
compgen -G "$AGENTS_HOME/skills/orchestrator/stale-managed-file.txt.bak.*" >/dev/null
if [[ -e "$AGENTS_HOME/skills/project-specific-observability/SKILL.md" ]]; then
  echo "Default install should not install project-specific reviewer packs" >&2
  exit 1
fi
assert_router_v3_hook_runtime "$CODEX_HOME/hooks"
assert_router_v3_hooks_json "$CODEX_HOME/hooks.json"
test -f "$CODEX_HOME/agents/critic.toml"
if [[ -e "$CODEX_HOME/agents/codebase-research-explorer.toml" ]]; then
  echo "Deprecated codebase-research-explorer agent should be removed during install" >&2
  exit 1
fi

BAD_SKILLS="$TMP_ROOT/bad-skills"
cp -R "$AGENTS_HOME/skills" "$BAD_SKILLS"
mkdir -p "$BAD_SKILLS/orchestrator/__pycache__" "$BAD_SKILLS/orchestrator/.bak.20260722"
printf '%s\n' 'ignored' > "$BAD_SKILLS/orchestrator/__pycache__/ignored.pyc"
printf '%s\n' 'ignored' > "$BAD_SKILLS/orchestrator/.bak.20260722/SKILL.md"
assert_skill_parity "$BAD_SKILLS"
printf '%s\n' '# tampered' >> "$BAD_SKILLS/orchestrator/SKILL.md"
if assert_skill_parity "$BAD_SKILLS" >/dev/null 2>&1; then
  echo "verify_skill_install.py should fail on SHA-256 mismatch" >&2
  exit 1
fi

SKILLS_ONLY_CODEX="$TMP_ROOT/skills-only-codex"
SKILLS_ONLY_AGENTS="$TMP_ROOT/skills-only-agents"
mkdir -p "$SKILLS_ONLY_CODEX/agents"
printf '%s\n' '{"hooks":{"UserPromptSubmit":[]}}' > "$SKILLS_ONLY_CODEX/hooks.json"
printf '%s\n' 'sentinel = true' > "$SKILLS_ONLY_CODEX/config.toml"
printf '%s\n' 'name = "keep_me"' > "$SKILLS_ONLY_CODEX/agents/keep-me.toml"
mkdir -p "$SKILLS_ONLY_CODEX/hooks"
printf '%s\n' 'keep' > "$SKILLS_ONLY_CODEX/hooks/keep-me.py"
HOOKS_BEFORE=$(cat "$SKILLS_ONLY_CODEX/hooks.json")
CONFIG_BEFORE=$(cat "$SKILLS_ONLY_CODEX/config.toml")
"$ROOT/install.sh" --codex-home "$SKILLS_ONLY_CODEX" --agents-home "$SKILLS_ONLY_AGENTS" --apply --skills-only --skills-mode agents >/dev/null
assert_managed_skills_only "$SKILLS_ONLY_AGENTS/skills"
assert_skill_parity "$SKILLS_ONLY_AGENTS/skills"
if [[ "$(cat "$SKILLS_ONLY_CODEX/hooks.json")" != "$HOOKS_BEFORE" ]]; then
  echo "Skills-only install mutated hooks.json" >&2
  exit 1
fi
test -f "$SKILLS_ONLY_CODEX/agents/keep-me.toml"
test -f "$SKILLS_ONLY_CODEX/hooks/keep-me.py"
if [[ "$(cat "$SKILLS_ONLY_CODEX/config.toml")" != "$CONFIG_BEFORE" ]]; then
  echo "Skills-only install mutated config.toml" >&2
  exit 1
fi
"$ROOT/uninstall.sh" --codex-home "$SKILLS_ONLY_CODEX" --agents-home "$SKILLS_ONLY_AGENTS" --skills-only --skills-mode agents >/dev/null
if [[ -e "$SKILLS_ONLY_AGENTS/skills/orchestrator/SKILL.md" ]]; then
  echo "Skills-only uninstall did not remove installed skills" >&2
  exit 1
fi
if [[ "$(cat "$SKILLS_ONLY_CODEX/hooks.json")" != "$HOOKS_BEFORE" ]]; then
  echo "Skills-only uninstall mutated hooks.json" >&2
  exit 1
fi
test -f "$SKILLS_ONLY_CODEX/agents/keep-me.toml"
test -f "$SKILLS_ONLY_CODEX/hooks/keep-me.py"
test "$(cat "$SKILLS_ONLY_CODEX/config.toml")" = "$CONFIG_BEFORE"

SKILLS_ONLY_CODEX_MODE="$TMP_ROOT/skills-only-codex-mode"
SKILLS_ONLY_CODEX_MODE_AGENTS="$TMP_ROOT/skills-only-codex-mode-agents"
mkdir -p "$SKILLS_ONLY_CODEX_MODE/skills/orchestrator" "$SKILLS_ONLY_CODEX_MODE/agents" "$SKILLS_ONLY_CODEX_MODE/hooks"
printf '%s\n' 'stale' > "$SKILLS_ONLY_CODEX_MODE/skills/orchestrator/stale-managed-file.txt"
printf '%s\n' '{"hooks":{"UserPromptSubmit":[]}}' > "$SKILLS_ONLY_CODEX_MODE/hooks.json"
printf '%s\n' 'sentinel = true' > "$SKILLS_ONLY_CODEX_MODE/config.toml"
printf '%s\n' 'keep' > "$SKILLS_ONLY_CODEX_MODE/agents/keep-me.toml"
printf '%s\n' 'keep' > "$SKILLS_ONLY_CODEX_MODE/hooks/keep-me.py"
"$ROOT/install.sh" --codex-home "$SKILLS_ONLY_CODEX_MODE" --agents-home "$SKILLS_ONLY_CODEX_MODE_AGENTS" --apply --skills-only --skills-mode codex >/dev/null
assert_managed_skills_only "$SKILLS_ONLY_CODEX_MODE/skills"
assert_skill_parity "$SKILLS_ONLY_CODEX_MODE/skills"
test ! -e "$SKILLS_ONLY_CODEX_MODE/skills/orchestrator/stale-managed-file.txt"
compgen -G "$SKILLS_ONLY_CODEX_MODE/skills/orchestrator/stale-managed-file.txt.bak.*" >/dev/null
test -f "$SKILLS_ONLY_CODEX_MODE/agents/keep-me.toml"
test -f "$SKILLS_ONLY_CODEX_MODE/hooks/keep-me.py"
grep -q 'sentinel = true' "$SKILLS_ONLY_CODEX_MODE/config.toml"
"$ROOT/uninstall.sh" --codex-home "$SKILLS_ONLY_CODEX_MODE" --agents-home "$SKILLS_ONLY_CODEX_MODE_AGENTS" --skills-only --skills-mode codex >/dev/null
test ! -e "$SKILLS_ONLY_CODEX_MODE/skills/orchestrator/SKILL.md"
test -f "$SKILLS_ONLY_CODEX_MODE/agents/keep-me.toml"
test -f "$SKILLS_ONLY_CODEX_MODE/hooks/keep-me.py"
grep -q 'sentinel = true' "$SKILLS_ONLY_CODEX_MODE/config.toml"

CONFIG_CODEX="$TMP_ROOT/config-codex"
mkdir -p "$CONFIG_CODEX/skills/orchestrator"
printf '%s\n' 'stale' > "$CONFIG_CODEX/skills/orchestrator/stale-managed-file.txt"
cat > "$CONFIG_CODEX/config.toml" <<'EOF'
approval_policy = 'never'

[features]
codex_hooks = true
EOF
"$ROOT/install.sh" --codex-home "$CONFIG_CODEX" --hook-mode config_toml --skills-mode codex --apply >/dev/null
assert_managed_skills_only "$CONFIG_CODEX/skills"
assert_skill_parity "$CONFIG_CODEX/skills"
test ! -e "$CONFIG_CODEX/skills/orchestrator/stale-managed-file.txt"
compgen -G "$CONFIG_CODEX/skills/orchestrator/stale-managed-file.txt.bak.*" >/dev/null
assert_router_v3_hook_runtime "$CONFIG_CODEX/hooks"
"$PYTHON_BIN" - "$CONFIG_CODEX/config.toml" <<'PY'
import tomllib
import sys

with open(sys.argv[1], "rb") as stream:
    obj = tomllib.load(stream)
hooks = obj.get("hooks", {})
if set(hooks) != {"PreToolUse"}:
    raise SystemExit(f"config.toml mode should install only PreToolUse: {sorted(hooks)}")
entries = hooks["PreToolUse"]
by_matcher = {
    entry.get("matcher"): [
        hook["command"]
        for hook in entry.get("hooks", [])
    ]
    for entry in entries
}
if set(by_matcher) != {"^(Bash|exec_command)$"}:
    raise SystemExit(f"config.toml mode missing narrow command matcher: {entries}")
commands = by_matcher["^(Bash|exec_command)$"]
if len(commands) != 1 or "critical_action_gate.py" not in commands[0]:
    raise SystemExit(f"config.toml mode missing single critical gate: {by_matcher}")
PY
"$ROOT/install.sh" --codex-home "$CONFIG_CODEX" --hook-mode config_toml --skills-mode codex --apply >/dev/null
"$PYTHON_BIN" - "$CONFIG_CODEX/config.toml" <<'PY'
import pathlib
import tomllib
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
obj = tomllib.loads(text)
entries = obj["hooks"].get("PreToolUse", [])
gate_commands = [
    hook["command"]
    for entry in entries
    for hook in entry.get("hooks", [])
    if "critical_action_gate.py" in hook.get("command", "")
]
if len(gate_commands) != 1:
    raise SystemExit(f"config.toml install is not idempotent for critical gate: {gate_commands}")
pilot_commands = [
    hook["command"]
    for entry in entries
    for hook in entry.get("hooks", [])
    if "pilot_receipt_gate.py" in hook.get("command", "")
]
if pilot_commands:
    raise SystemExit(f"retired pilot gate remains registered: {pilot_commands}")
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
"$ROOT/install.sh" --codex-home "$EXISTING_HOOKS_CODEX" --hook-mode config_toml --skills-mode codex --apply >/dev/null
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
pretool_entries = obj["hooks"].get("PreToolUse", [])
gate_entries = [
    entry
    for entry in pretool_entries
    if any("critical_action_gate.py" in hook.get("command", "") for hook in entry.get("hooks", []))
]
if len(gate_entries) != 1 or gate_entries[0].get("matcher") != "^(Bash|exec_command)$":
    raise SystemExit(f"missing single command critical gate after config merge: {gate_entries}")
for forbidden in ("PostToolUse", "Stop"):
    if forbidden in obj["hooks"]:
        raise SystemExit(f"unexpected legacy managed event after config merge: {forbidden}")
PY

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
grep -q 'critical_action_gate.py' "$CODEX_HOME/hooks.json"
if grep -q 'pilot_receipt_gate.py' "$CODEX_HOME/hooks.json"; then
  echo "Retired pilot gate should not be installed" >&2
  exit 1
fi

"$ROOT/install.sh" --codex-home "$CODEX_HOME" --agents-home "$AGENTS_HOME" --apply --hook-mode auto --skills-mode auto >/dev/null
COUNT=$(grep -o 'critical_action_gate.py' "$CODEX_HOME/hooks.json" | wc -l | tr -d ' ')
if [[ "$COUNT" != "1" ]]; then
  echo "Install is not idempotent: critical_action_gate.py appears $COUNT times" >&2
  exit 1
fi
assert_router_v3_hooks_json "$CODEX_HOME/hooks.json"

DRY_CODEX="$TMP_ROOT/dry-codex"
DRY_AGENTS="$TMP_ROOT/dry-agents"
"$ROOT/install.sh" --codex-home "$DRY_CODEX" --agents-home "$DRY_AGENTS" --dry-run >/dev/null
if [[ -e "$DRY_CODEX" || -e "$DRY_AGENTS" ]]; then
  echo "Dry run created target directories" >&2
  exit 1
fi

CRITICAL_GATE="$CODEX_HOME/hooks/critical_action_gate.py"
SESSIONS_ROOT="$CODEX_HOME/sessions"
mkdir -p "$SESSIONS_ROOT"

ORDINARY_GATE=$(printf '%s' '{"tool_name":"Bash","tool_input":{"cmd":"python -V"}}' | "$PYTHON_BIN" "$CRITICAL_GATE")
if [[ -n "$ORDINARY_GATE" ]]; then
  echo "Ordinary Bash fast path should stay silent" >&2
  exit 1
fi

DANGEROUS_GATE=$(printf '%s' '{"tool_name":"Bash","tool_input":{"cmd":"git push origin HEAD:pre"}}' | "$PYTHON_BIN" "$CRITICAL_GATE")
"$PYTHON_BIN" - "$DANGEROUS_GATE" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
hook_output = obj.get("hookSpecificOutput", {})
reason = str(hook_output.get("permissionDecisionReason", "")).lower()
if hook_output.get("permissionDecision") != "deny" or "current-turn" not in reason:
    raise SystemExit(f"unauthorized push candidate was not blocked: {obj}")
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
CODEX_ORCHESTRATOR_PYTHON="$SPACE_PY" "$ROOT/install.sh" --codex-home "$OVERRIDE_CODEX" --agents-home "$OVERRIDE_AGENTS" --apply --hook-mode auto --skills-mode auto >/dev/null
"$PYTHON_BIN" - "$OVERRIDE_CODEX/hooks.json" "$SPACE_PY" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    hooks = json.load(stream)
entries = hooks["hooks"]["PreToolUse"]
commands = [
    hook["command"]
    for entry in entries
    for hook in entry.get("hooks", [])
    if "critical_action_gate.py" in hook.get("command", "")
]
if len(commands) != 1:
    raise SystemExit(f"Python override install missing single critical gate: {commands}")
command = commands[0]
expected = f'"{sys.argv[2]}" '
if not command.startswith(expected):
    raise SystemExit(f"Python override with spaces was not quoted: {command}")
PY

mkdir -p "$SESSIONS_ROOT"
cp "$ROOT/tests/fixtures/session-observability.jsonl" "$SESSIONS_ROOT/session-smoke.jsonl"
FIXTURE_TIMESTAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
cat >> "$SESSIONS_ROOT/session-smoke.jsonl" <<JSONL
{"event":"orchestrator_observability","event_name":"pre_tool_use","status":"blocked","created_at":"$FIXTURE_TIMESTAMP","reason":"broad_scan_without_research_capability","session_id":"smoke-router-v3","cwd":"C:/repo/acme-tooling","tool_name":"shell_command","normalized_tool_name":"shell_command","tool_family":"shell_like","input_length":8}
{"event":"orchestrator_observability","event_name":"stop","status":"allow","created_at":"$FIXTURE_TIMESTAMP","reason":"fixture structured acceptance","session_id":"smoke-router-v3","cwd":"C:/repo/acme-tooling","acceptance_source":"structured","acceptance":{"source":"structured","verification_status":"pass","review_status":"not_required","subagent_status":"closed","risk_status":"low","has_work_done_signal":true,"has_verification_signal":true,"has_subagent_signal":false,"footer_field_count":4,"footer_complete":true,"footer_missing_fields":[]}}
JSONL
mkdir -p "$SESSIONS_ROOT/2026/05/03"
cat > "$SESSIONS_ROOT/2026/05/03/rollout-nested-meta-session.jsonl" <<'JSONL'
{"type":"session_meta","payload":{"id":"nested-meta-session","cwd":"/tmp/repo/meta-project"},"timestamp":"2026-05-02T00:00:11Z"}
{"payload":{"type":"message","role":"assistant","content":"implemented nested metadata change. Done."},"timestamp":"2026-05-02T00:00:12Z"}
JSONL
PROJECT_CONTEXT_DIR="$TMP_ROOT/project-context-smoke"
PROJECT_CONTEXT_NAME=$(basename "$PROJECT_CONTEXT_DIR")
mkdir -p "$PROJECT_CONTEXT_DIR/.codex"
# The following project context contains synthetic fixture secrets for redaction tests only.
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

"$PYTHON_BIN" "$AGENTS_HOME/skills/orchestrator-observability/scripts/analyze.py" \
  --codex-home "$CODEX_HOME" \
  --since-days "$FIXTURE_SINCE_DAYS" \
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
  --since-days "$FIXTURE_SINCE_DAYS" \
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

UNINSTALL_CODEX="$TMP_ROOT/uninstall-codex"
UNINSTALL_AGENTS="$TMP_ROOT/uninstall-agents"
mkdir -p "$UNINSTALL_CODEX" "$UNINSTALL_AGENTS"
cp "$ROOT/tests/fixtures/hooks.before.json" "$UNINSTALL_CODEX/hooks.json"
"$ROOT/install.sh" --codex-home "$UNINSTALL_CODEX" --agents-home "$UNINSTALL_AGENTS" --apply --hook-mode auto --skills-mode auto >/dev/null
bash "$ROOT/uninstall.sh" --codex-home "$UNINSTALL_CODEX" --agents-home "$UNINSTALL_AGENTS" --hook-mode auto --skills-mode auto >/dev/null
if [[ -e "$UNINSTALL_AGENTS/skills/orchestrator/SKILL.md" ]]; then
  echo "uninstall.sh left orchestrator skill installed" >&2
  exit 1
fi
if [[ -e "$UNINSTALL_CODEX/hooks/critical_action_gate.py" ]]; then
  echo "uninstall.sh left critical action gate installed" >&2
  exit 1
fi
if [[ -e "$UNINSTALL_CODEX/hooks/pilot_receipt_gate.py" ]]; then
  echo "uninstall.sh left pilot receipt gate installed" >&2
  exit 1
fi
grep -q 'pre-existing-user-prompt.py' "$UNINSTALL_CODEX/hooks.json"
grep -q 'pre-existing-pre-tool.py' "$UNINSTALL_CODEX/hooks.json"
if grep -q 'critical_action_gate.py\|pilot_receipt_gate.py' "$UNINSTALL_CODEX/hooks.json"; then
  echo "uninstall.sh left managed hook entries in hooks.json" >&2
  exit 1
fi

echo "smoke-test.sh passed"
