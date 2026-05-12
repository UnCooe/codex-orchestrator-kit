#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
CODEX_TARGET="${CODEX_HOME:-}"
AGENTS_TARGET="${AGENTS_HOME:-}"
DRY_RUN=0
HOOK_MODE="auto"
SKILLS_MODE="auto"
AGENTS_TARGET_EXPLICIT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --codex-home)
      CODEX_TARGET="$2"
      shift 2
      ;;
    --agents-home)
      AGENTS_TARGET="$2"
      AGENTS_TARGET_EXPLICIT=1
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
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
      echo "Usage: install.sh [--codex-home PATH] [--agents-home PATH] [--dry-run] [--hook-mode auto|hooks_json|config_toml] [--skills-mode auto|agents|codex]" >&2
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

if [[ -n "${CODEX_ORCHESTRATOR_PYTHON:-${CODX_ORCHESTRATOR_PYTHON:-}}" ]]; then
  PYTHON_CMD="${CODEX_ORCHESTRATOR_PYTHON:-${CODX_ORCHESTRATOR_PYTHON:-}}"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=python
else
  echo "python3 or python is required for template merge" >&2
  exit 1
fi

if [[ -x "$PYTHON_CMD" ]]; then
  PYTHON_RUN=("$PYTHON_CMD")
else
  read -r -a PYTHON_RUN <<< "$PYTHON_CMD"
fi

hook_python_command() {
  local command="$1"
  if [[ -z "$command" ]]; then
    printf '%s\n' "$command"
    return
  fi
  case "$command" in
    \"*|\'*)
      printf '%s\n' "$command"
      return
      ;;
  esac
  if [[ "$command" =~ ^([A-Za-z]:\\.*\.exe)([[:space:]].*)?$ ]]; then
    printf '"%s"%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    return
  fi
  if [[ "$command" == *" "* ]]; then
    if [[ "$command" == */* || "$command" == *\\* ]]; then
      printf '"%s"\n' "$command"
      return
    fi
  fi
  printf '%s\n' "$command"
}

HOOK_PYTHON_CMD=$(hook_python_command "$PYTHON_CMD")

resolve_hook_mode() {
  if [[ "$HOOK_MODE" != "auto" ]]; then
    printf '%s\n' "$HOOK_MODE"
    return
  fi
  if [[ -f "$CODEX_TARGET/hooks.json" ]]; then
    printf '%s\n' "hooks_json"
    return
  fi
  if [[ -f "$CODEX_TARGET/config.toml" ]]; then
    printf '%s\n' "config_toml"
    return
  fi
  printf '%s\n' "hooks_json"
}

resolve_skills_mode() {
  if [[ "$SKILLS_MODE" != "auto" ]]; then
    printf '%s\n' "$SKILLS_MODE"
    return
  fi
  if [[ -d "$CODEX_TARGET/skills" ]]; then
    printf '%s\n' "codex"
    return
  fi
  if [[ "$RESOLVED_HOOK_MODE" == "config_toml" ]]; then
    printf '%s\n' "codex"
    return
  fi
  if [[ "$AGENTS_TARGET_EXPLICIT" == "1" || -d "$AGENTS_TARGET/skills" ]]; then
    printf '%s\n' "agents"
    return
  fi
  printf '%s\n' "agents"
}

RESOLVED_HOOK_MODE=$(resolve_hook_mode)
RESOLVED_SKILLS_MODE=$(resolve_skills_mode)
if [[ "$RESOLVED_SKILLS_MODE" == "codex" ]]; then
  SKILLS_TARGET_ROOT="$CODEX_TARGET"
else
  SKILLS_TARGET_ROOT="$AGENTS_TARGET"
fi

if [[ "$DRY_RUN" != "1" ]]; then
  mkdir -p "$CODEX_TARGET" "$CODEX_TARGET/hooks" "$CODEX_TARGET/agents" "$SKILLS_TARGET_ROOT/skills"
fi

backup_suffix() {
  printf '%s_%s' "$(date +%Y%m%d_%H%M%S)" "$$"
}

copy_sources() {
  local src_dir="$1"
  local dst_dir="$2"

  if [[ ! -d "$src_dir" ]]; then
    return
  fi

  if [[ "$DRY_RUN" != "1" ]]; then
    mkdir -p "$dst_dir"
  fi

  while IFS= read -r -d '' file; do
    local rel="${file#"$src_dir"/}"
    local dst="${dst_dir}/${rel}"
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "[DRY-RUN] $file -> $dst"
      continue
    fi
    mkdir -p "$(dirname "$dst")"
    if [[ -f "$dst" ]]; then
      cp "$dst" "${dst}.bak.$(backup_suffix)"
    fi
    cp "$file" "$dst"
  done < <(find "$src_dir" -type f ! -path '*/__pycache__/*' ! -name '*.pyc' -print0)
}

merge_hooks_json() {
  local template="$SCRIPT_DIR/templates/hooks.json.tpl"
  local output="$CODEX_TARGET/hooks.json"
  local codex_home="${CODEX_TARGET//\\//}"

  "${PYTHON_RUN[@]}" - "$template" "$codex_home" "$HOOK_PYTHON_CMD" "$output" "$DRY_RUN" <<'PY'
import json
import re
import sys
from pathlib import Path

template_path, codex_home, python_cmd, output, dry_run = sys.argv[1:6]
template = Path(template_path).read_text(encoding="utf-8")
rendered = template.replace("{{CODEX_HOME}}", json.dumps(codex_home)[1:-1])
rendered = rendered.replace("{{PYTHON_EXE}}", json.dumps(python_cmd)[1:-1])
incoming = json.loads(rendered)

def tokenize_command(command):
    token_re = re.compile(r'(?:"([^"]+)"|\'([^\']+)\'|(\S+))')
    for match in token_re.finditer(command):
        yield match.group(1) or match.group(2) or match.group(3)

def scripts_in_entry(entry):
    scripts = set()
    for hook in entry.get("hooks", []):
        command = hook.get("command")
        if not isinstance(command, str):
            continue
        for token in tokenize_command(command):
            if token and re.search(r'(?i)orchestrator_[^\\/"]+\.py$', token):
                scripts.add(re.split(r'[\\/]', token)[-1])
    return scripts

def merge_event_entries(existing, updates):
    existing_entries = [dict(x) for x in existing]
    incoming_entries = [dict(x) for x in updates]
    incoming_scripts = set()
    for entry in incoming_entries:
        incoming_scripts |= scripts_in_entry(entry)
    if incoming_scripts:
        deduped = []
        for entry in existing_entries:
            hooks = entry.get("hooks", [])
            if not hooks:
                deduped.append(entry)
                continue
            kept = []
            for hook in hooks:
                names = scripts_in_entry({"hooks": [hook]})
                if names and (names & incoming_scripts):
                    continue
                kept.append(hook)
            if not kept:
                continue
            merged = dict(entry)
            merged["hooks"] = kept
            deduped.append(merged)
        existing_entries = deduped
    signatures = {json.dumps(entry, sort_keys=True, separators=(",", ":")) for entry in existing_entries}
    for entry in incoming_entries:
        signature = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        if signature not in signatures:
            existing_entries.append(entry)
            signatures.add(signature)
    return existing_entries

path = Path(output)
try:
    current = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    current = {}
merged = {"hooks": {}}
base = current.get("hooks", {}) if isinstance(current, dict) else {}
for event, entries in incoming.get("hooks", {}).items():
    merged["hooks"][event] = merge_event_entries(base.get(event, []), entries)
for event, entries in base.items():
    if event not in merged["hooks"]:
        merged["hooks"][event] = entries

if dry_run == "1":
    print(json.dumps(merged, indent=2))
else:
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
PY
}

merge_config_toml_hooks() {
  local template="$SCRIPT_DIR/templates/config.toml_hooks.tpl"
  local output="$CODEX_TARGET/config.toml"
  local codex_home="${CODEX_TARGET//\\//}"
  local begin_marker="# BEGIN codex-orchestrator-kit managed hooks"
  local end_marker="# END codex-orchestrator-kit managed hooks"

  "${PYTHON_RUN[@]}" - "$template" "$codex_home" "$HOOK_PYTHON_CMD" "$output" "$begin_marker" "$end_marker" "$DRY_RUN" <<'PY'
import json
import re
import sys
from pathlib import Path

template_path, codex_home, python_cmd, output, begin_marker, end_marker, dry_run = sys.argv[1:8]
template = Path(template_path).read_text(encoding="utf-8")
rendered = template.replace("{{CODEX_HOME}}", json.dumps(codex_home)[1:-1])
rendered = rendered.replace("{{PYTHON_EXE}}", json.dumps(python_cmd)[1:-1])
managed_block = f"{begin_marker}\n{rendered.rstrip()}\n{end_marker}\n"

path = Path(output)
try:
    current = path.read_text(encoding="utf-8")
except FileNotFoundError:
    current = ""

pattern = re.compile(rf"(?ms)^[ \t]*{re.escape(begin_marker)}\n.*?^[ \t]*{re.escape(end_marker)}\n?")
current = re.sub(pattern, "", current).rstrip()
new_text = current + ("\n\n" if current else "") + managed_block

if dry_run == "1":
    print(new_text)
else:
    path.write_text(new_text, encoding="utf-8")
PY
}

copy_sources "$SCRIPT_DIR/skills/orchestrator" "$SKILLS_TARGET_ROOT/skills/orchestrator"
copy_sources "$SCRIPT_DIR/skills/orchestrator-observability" "$SKILLS_TARGET_ROOT/skills/orchestrator-observability"
copy_sources "$SCRIPT_DIR/hooks" "$CODEX_TARGET/hooks"
copy_sources "$SCRIPT_DIR/agents" "$CODEX_TARGET/agents"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[DRY-RUN] hook_mode=$RESOLVED_HOOK_MODE skills_mode=$RESOLVED_SKILLS_MODE skills_root=$SKILLS_TARGET_ROOT"
fi

if [[ "$RESOLVED_HOOK_MODE" == "hooks_json" ]]; then
  if [[ "$DRY_RUN" != "1" && -f "$CODEX_TARGET/hooks.json" ]]; then
    cp "$CODEX_TARGET/hooks.json" "$CODEX_TARGET/hooks.json.bak.$(backup_suffix)"
  fi
  merge_hooks_json >/dev/null
else
  if [[ "$DRY_RUN" != "1" && -f "$CODEX_TARGET/config.toml" ]]; then
    cp "$CODEX_TARGET/config.toml" "$CODEX_TARGET/config.toml.bak.$(backup_suffix)"
  fi
  merge_config_toml_hooks >/dev/null
fi

echo "done"
