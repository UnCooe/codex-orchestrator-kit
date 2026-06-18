#!/usr/bin/env bash
set -euo pipefail

CODEX_TARGET="${CODEX_HOME:-}"
AGENTS_TARGET="${AGENTS_HOME:-}"
HOOK_MODE="auto"
SKILLS_MODE="auto"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --codex-home)
      CODEX_TARGET="$2"
      shift 2
      ;;
    --agents-home)
      AGENTS_TARGET="$2"
      shift 2
      ;;
    --hook-mode)
      HOOK_MODE="$2"
      shift 2
      ;;
    --skills-mode)
      SKILLS_MODE="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: uninstall.sh [--codex-home PATH] [--agents-home PATH] [--hook-mode auto|hooks_json|config_toml] [--skills-mode auto|agents|codex]" >&2
      exit 2
      ;;
  esac
done

USER_HOME="${HOME:-${USERPROFILE:-}}"
if [[ -z "$CODEX_TARGET" ]]; then
  if [[ -z "$USER_HOME" ]]; then
    echo "HOME or USERPROFILE is required unless --codex-home is provided" >&2
    exit 1
  fi
  CODEX_TARGET="$USER_HOME/.codex"
fi

if [[ -z "$AGENTS_TARGET" ]]; then
  if [[ -z "$USER_HOME" ]]; then
    echo "HOME or USERPROFILE is required unless --agents-home is provided" >&2
    exit 1
  fi
  AGENTS_TARGET="$USER_HOME/.agents"
fi

case "$HOOK_MODE" in
  auto|hooks_json|config_toml) ;;
  *)
    echo "Invalid --hook-mode: $HOOK_MODE" >&2
    exit 2
    ;;
esac

case "$SKILLS_MODE" in
  auto|agents|codex) ;;
  *)
    echo "Invalid --skills-mode: $SKILLS_MODE" >&2
    exit 2
    ;;
esac

if command -v python3 >/dev/null 2>&1; then
  PYTHON_RUN=(python3)
elif command -v python >/dev/null 2>&1; then
  PYTHON_RUN=(python)
else
  echo "python3 or python is required for hook cleanup" >&2
  exit 1
fi

if [[ "$HOOK_MODE" != "auto" ]]; then
  RESOLVED_HOOK_MODE="$HOOK_MODE"
elif [[ -f "$CODEX_TARGET/hooks.json" ]]; then
  RESOLVED_HOOK_MODE="hooks_json"
elif [[ -f "$CODEX_TARGET/config.toml" ]]; then
  RESOLVED_HOOK_MODE="config_toml"
else
  RESOLVED_HOOK_MODE="hooks_json"
fi

if [[ "$SKILLS_MODE" != "auto" ]]; then
  RESOLVED_SKILLS_MODE="$SKILLS_MODE"
elif [[ -d "$CODEX_TARGET/skills" ]]; then
  RESOLVED_SKILLS_MODE="codex"
elif [[ -d "$AGENTS_TARGET/skills" ]]; then
  RESOLVED_SKILLS_MODE="agents"
else
  RESOLVED_SKILLS_MODE="agents"
fi

if [[ "$RESOLVED_SKILLS_MODE" == "codex" ]]; then
  SKILLS_HOME="$CODEX_TARGET"
else
  SKILLS_HOME="$AGENTS_TARGET"
fi

backup_suffix() {
  printf '%s_%s' "$(date +%Y%m%d_%H%M%S)" "$$"
}

backup_path() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    return
  fi
  local backup="${path}.bak.$(backup_suffix)"
  cp -R "$path" "$backup"
  echo "backed up -> $backup"
}

remove_hooks_json() {
  local hooks_path="$1"
  if [[ ! -f "$hooks_path" ]]; then
    return
  fi
  "${PYTHON_RUN[@]}" - "$hooks_path" <<'PY'
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
obj = json.loads(path.read_text(encoding="utf-8"))
hooks = obj.get("hooks")
if not isinstance(hooks, dict):
    raise SystemExit(0)

pattern = re.compile(r"orchestrator_(pre_tool_use|post_tool_use|stop|user_prompt|observability)\.py")
updated = {}
for event, entries in hooks.items():
    kept_entries = []
    for entry in entries:
        kept_hooks = [
            hook
            for hook in entry.get("hooks", [])
            if not pattern.search(str(hook.get("command", "")))
        ]
        if kept_hooks:
            new_entry = dict(entry)
            new_entry["hooks"] = kept_hooks
            kept_entries.append(new_entry)
    if kept_entries:
        updated[event] = kept_entries

if updated:
    obj["hooks"] = updated
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
else:
    path.unlink()
PY
}

remove_config_managed_hooks() {
  local config_path="$1"
  if [[ ! -f "$config_path" ]]; then
    return
  fi
  "${PYTHON_RUN[@]}" - "$config_path" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
begin = "# BEGIN codex-orchestrator-kit managed hooks"
end = "# END codex-orchestrator-kit managed hooks"
pattern = re.compile(rf"(?ms)^[ \t]*{re.escape(begin)}\n.*?^[ \t]*{re.escape(end)}\n?")
path.write_text(pattern.sub("", text).rstrip() + "\n", encoding="utf-8")
PY
}

echo "uninstall plan:"
echo "  codex_home=$CODEX_TARGET"
echo "  agents_home=$AGENTS_TARGET"
echo "  hook_mode=$RESOLVED_HOOK_MODE"
echo "  skills_mode=$RESOLVED_SKILLS_MODE"

if [[ "$RESOLVED_HOOK_MODE" == "hooks_json" ]]; then
  HOOK_SURFACE="$CODEX_TARGET/hooks.json"
  if [[ -f "$HOOK_SURFACE" ]]; then
    backup_path "$HOOK_SURFACE"
    remove_hooks_json "$HOOK_SURFACE"
  fi
else
  HOOK_SURFACE="$CODEX_TARGET/config.toml"
  if [[ -f "$HOOK_SURFACE" ]]; then
    backup_path "$HOOK_SURFACE"
    remove_config_managed_hooks "$HOOK_SURFACE"
  fi
fi

remove_targets=(
  "$SKILLS_HOME/skills/orchestrator"
  "$SKILLS_HOME/skills/orchestrator-observability"
  "$SKILLS_HOME/skills/feature-workflow"
  "$SKILLS_HOME/skills/merge-workflow"
  "$SKILLS_HOME/skills/review-workflow"
  "$SKILLS_HOME/skills/predeploy-workflow"
  "$CODEX_TARGET/hooks/orchestrator_pre_tool_use.py"
  "$CODEX_TARGET/hooks/orchestrator_post_tool_use.py"
  "$CODEX_TARGET/hooks/orchestrator_stop.py"
  "$CODEX_TARGET/hooks/orchestrator_user_prompt.py"
  "$CODEX_TARGET/hooks/orchestrator_observability.py"
  "$CODEX_TARGET/agents/critic.toml"
  "$CODEX_TARGET/agents/implementer.toml"
  "$CODEX_TARGET/agents/researcher.toml"
  "$CODEX_TARGET/agents/reviewer.toml"
  "$CODEX_TARGET/agents/test-designer.toml"
  "$CODEX_TARGET/agents/requirements-interpreter.toml"
  "$CODEX_TARGET/agents/test-writer.toml"
  "$CODEX_TARGET/agents/test-runner.toml"
  "$CODEX_TARGET/agents/conflict-resolver.toml"
  "$CODEX_TARGET/agents/deployer.toml"
  "$CODEX_TARGET/agents/codebase-research-explorer.toml"
)

for target in "${remove_targets[@]}"; do
  if [[ -e "$target" ]]; then
    backup_path "$target"
    rm -rf "$target"
    echo "removed: $target"
  fi
done

echo "done"
