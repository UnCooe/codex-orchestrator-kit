#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT

CODEX_HOME="$TMP_ROOT/.codex"
AGENTS_HOME="$TMP_ROOT/.agents"
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
test -f "$CODEX_HOME/hooks/orchestrator_user_prompt.py"
test -f "$CODEX_HOME/agents/critic.toml"

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

SESSIONS_ROOT="$CODEX_HOME/sessions"
mkdir -p "$SESSIONS_ROOT"
cp "$ROOT/tests/fixtures/session-observability.jsonl" "$SESSIONS_ROOT/session-smoke.jsonl"
mkdir -p "$SESSIONS_ROOT/2026/05/03"
cat > "$SESSIONS_ROOT/2026/05/03/rollout-nested-meta-session.jsonl" <<'JSONL'
{"type":"session_meta","payload":{"id":"nested-meta-session","cwd":"/tmp/repo/meta-project"},"timestamp":"2026-05-02T00:00:11Z"}
{"payload":{"type":"message","role":"assistant","content":"implemented nested metadata change. Done."},"timestamp":"2026-05-02T00:00:12Z"}
JSONL

"$PYTHON_BIN" "$AGENTS_HOME/skills/orchestrator-observability/scripts/analyze.py" \
  --codex-home "$CODEX_HOME" \
  --since-days 30 \
  --report-id smoke > /tmp/orchestrator-observability-smoke-report.json

REPORT_DIR="$CODEX_HOME/orchestrator-observability/reports"
JSON_REPORT="$REPORT_DIR/orchestrator-observability-smoke.json"
test -f "$JSON_REPORT"
"$PYTHON_BIN" - "$JSON_REPORT" <<'PY'
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
if not any(f["session_id"] == "nested-meta-session" and f["cwd"].endswith("meta-project") for f in obj["facts"]):
    raise SystemExit("Analyzer did not inherit nested session id/cwd facts")
if not obj.get("suggestions"):
    raise SystemExit("Analyzer suggestions missing")
if not any("verification" in item.lower() for item in obj["suggestions"]):
    raise SystemExit("Analyzer suggestions missing verification guidance")
if any("no sessions were detected" in item.lower() for item in obj["suggestions"]):
    raise SystemExit("Analyzer emitted false session-extraction suggestion")
if any("please implement" in json.dumps(v) for v in obj["facts"]):
    raise SystemExit("Analyzer leaked assistant content in facts")
report_text = json.dumps(obj)
if any(path in report_text for path in ("C:/repo", "D:/repo", "/tmp/repo")):
    raise SystemExit("Analyzer leaked full local paths without --include-paths")
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
    "work_done_missing_subagent_status": 0
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
    "work_done_missing_subagent_status": 0
  },
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
    "work_done_missing_subagent_status": 0
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
