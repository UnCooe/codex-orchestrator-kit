#!/usr/bin/env bash
set -euo pipefail

CODEX_TARGET="${CODEX_HOME:-}"
AGENTS_TARGET="${AGENTS_HOME:-}"
HOOK_MODE="auto"
SKILLS_MODE="auto"
SKILLS_ONLY=0
MANAGED_SKILLS=(
  orchestrator
  orchestrator-observability
  feature-workflow
  review-workflow
  merge-workflow
  predeploy-workflow
  merge-safety
)

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
    --skills-only)
      SKILLS_ONLY=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: uninstall.sh [--codex-home PATH] [--agents-home PATH] [--skills-only] [--hook-mode auto|hooks_json|config_toml] [--skills-mode auto|agents|codex]" >&2
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

if [[ "$SKILLS_ONLY" != "1" && "$HOOK_MODE" != "auto" ]]; then
  RESOLVED_HOOK_MODE="$HOOK_MODE"
elif [[ "$SKILLS_ONLY" != "1" && -f "$CODEX_TARGET/hooks.json" ]]; then
  RESOLVED_HOOK_MODE="hooks_json"
elif [[ "$SKILLS_ONLY" != "1" && -f "$CODEX_TARGET/config.toml" ]]; then
  RESOLVED_HOOK_MODE="config_toml"
else
  RESOLVED_HOOK_MODE="hooks_json"
fi

if [[ "$SKILLS_ONLY" != "1" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_RUN=(python3)
  elif command -v python >/dev/null 2>&1; then
    PYTHON_RUN=(python)
  else
    echo "python3 or python is required for hook cleanup" >&2
    exit 1
  fi
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

SKILL_REMOVE_TARGETS=()
for skill in "${MANAGED_SKILLS[@]}"; do
  SKILL_REMOVE_TARGETS+=("$SKILLS_HOME/skills/$skill")
done

backup_suffix() {
  printf '%s_%s' "$(date +%Y%m%d_%H%M%S)" "$$"
}

fail_if_symlink() {
  local path="$1"
  if [[ -L "$path" ]]; then
    echo "Refusing to remove symlink target: $path" >&2
    exit 1
  fi
}

fail_if_tree_contains_symlink() {
  local root="$1"
  if [[ ! -e "$root" ]]; then
    return
  fi
  fail_if_symlink "$root"
  local symlink
  symlink=$(find "$root" -type l -print -quit)
  if [[ -n "$symlink" ]]; then
    echo "Refusing to remove symlink target: $symlink" >&2
    exit 1
  fi
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

managed_scripts = {
    "orchestrator_user_prompt.py",
    "orchestrator_pre_tool_use.py",
    "orchestrator_post_tool_use.py",
    "orchestrator_stop.py",
    "orchestrator_observability.py",
    "workflow_profile.py",
    "first_write_gate.py",
    "critical_action_gate.py",
    "pilot_receipt_gate.py",
}
token_re = re.compile(r'(?:"([^"]+)"|\'([^\']+)\'|(\S+))')

def is_managed(command):
    for match in token_re.finditer(str(command)):
        token = match.group(1) or match.group(2) or match.group(3)
        script = re.split(r"[\\/]", token)[-1]
        if script.casefold() in {name.casefold() for name in managed_scripts}:
            return True
    return False

updated = {}
for event, entries in hooks.items():
    kept_entries = []
    for entry in entries:
        kept_hooks = [
            hook
            for hook in entry.get("hooks", [])
            if not is_managed(hook.get("command", ""))
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
text = pattern.sub("", text)
managed = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])(?:orchestrator_user_prompt|orchestrator_pre_tool_use|"
    r"orchestrator_post_tool_use|orchestrator_stop|orchestrator_observability|workflow_profile|"
    r"first_write_gate|critical_action_gate|pilot_receipt_gate)"
    r"\.py(?![A-Za-z0-9_.-])"
)
event_header = re.compile(r"^\[\[hooks\.([A-Za-z0-9_]+)\]\]\s*$")
subhook_header = re.compile(r"^\[\[hooks\.([A-Za-z0-9_]+)\.hooks\]\]\s*$")
any_header = re.compile(r"^\[")

lines = text.splitlines(keepends=True)
output = []
index = 0
while index < len(lines):
    stripped = lines[index].strip()
    event_match = event_header.match(stripped)
    if not event_match:
        output.append(lines[index])
        index += 1
        continue

    event_lines = [lines[index]]
    index += 1
    while index < len(lines):
        next_stripped = lines[index].strip()
        if subhook_header.match(next_stripped) or (any_header.match(next_stripped) and not next_stripped.startswith("[[hooks.")):
            break
        if event_header.match(next_stripped):
            break
        event_lines.append(lines[index])
        index += 1

    subblocks = []
    while index < len(lines):
        next_stripped = lines[index].strip()
        if not subhook_header.match(next_stripped):
            break
        subblock = [lines[index]]
        index += 1
        while index < len(lines):
            lookahead = lines[index].strip()
            if event_header.match(lookahead) or subhook_header.match(lookahead) or (any_header.match(lookahead) and not lookahead.startswith("[[hooks.")):
                break
            subblock.append(lines[index])
            index += 1
        subblock_text = "".join(subblock)
        has_active_managed_hook = any(
            not line.lstrip().startswith("#") and managed.search(line)
            for line in subblock_text.splitlines()
        )
        if not has_active_managed_hook:
            subblocks.append(subblock_text)

    if subblocks:
        output.extend(event_lines)
        output.extend(subblocks)

updated = "".join(output)
path.write_text(updated.rstrip() + "\n", encoding="utf-8")
PY
}

echo "uninstall plan:"
echo "  codex_home=$CODEX_TARGET"
echo "  agents_home=$AGENTS_TARGET"
echo "  skills_mode=$RESOLVED_SKILLS_MODE"
echo "  skills_only=$([[ "$SKILLS_ONLY" == "1" ]] && printf 'true' || printf 'false')"
if [[ "$SKILLS_ONLY" != "1" ]]; then
  echo "  hook_mode=$RESOLVED_HOOK_MODE"
fi

if [[ "$SKILLS_ONLY" != "1" ]]; then
  fail_if_symlink "$CODEX_TARGET/hooks"
  if [[ "$RESOLVED_HOOK_MODE" == "hooks_json" ]]; then
    HOOK_SURFACE="$CODEX_TARGET/hooks.json"
    fail_if_symlink "$HOOK_SURFACE"
    if [[ -f "$HOOK_SURFACE" ]]; then
      backup_path "$HOOK_SURFACE"
      remove_hooks_json "$HOOK_SURFACE"
    fi
  else
    HOOK_SURFACE="$CODEX_TARGET/config.toml"
    fail_if_symlink "$HOOK_SURFACE"
    if [[ -f "$HOOK_SURFACE" ]]; then
      backup_path "$HOOK_SURFACE"
      remove_config_managed_hooks "$HOOK_SURFACE"
    fi
  fi
fi

remove_targets=(
  "${SKILL_REMOVE_TARGETS[@]}"
)

if [[ "$SKILLS_ONLY" != "1" ]]; then
  remove_targets+=(
  "$CODEX_TARGET/hooks/orchestrator_pre_tool_use.py"
  "$CODEX_TARGET/hooks/orchestrator_post_tool_use.py"
  "$CODEX_TARGET/hooks/orchestrator_stop.py"
  "$CODEX_TARGET/hooks/orchestrator_user_prompt.py"
  "$CODEX_TARGET/hooks/orchestrator_observability.py"
  "$CODEX_TARGET/hooks/workflow_profile.py"
  "$CODEX_TARGET/hooks/first_write_gate.py"
  "$CODEX_TARGET/hooks/critical_action_gate.py"
  "$CODEX_TARGET/hooks/pilot_receipt_gate.py"
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
fi

for target in "${remove_targets[@]}"; do
  if [[ -e "$target" || -L "$target" ]]; then
    fail_if_tree_contains_symlink "$target"
    backup_path "$target"
    rm -rf "$target"
    echo "removed: $target"
  fi
done

echo "done"
