#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
CODEX_TARGET="${CODEX_HOME:-}"
AGENTS_TARGET="${AGENTS_HOME:-}"
DRY_RUN=0

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
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: install.sh [--codex-home PATH] [--agents-home PATH] [--dry-run]" >&2
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

if [[ "$DRY_RUN" != "1" ]]; then
  mkdir -p "$CODEX_TARGET" "$CODEX_TARGET/hooks" "$CODEX_TARGET/agents" "$AGENTS_TARGET/skills"
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
    rel="${file#"$src_dir"/}"
    dst="${dst_dir}/${rel}"
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

PYTHON_MERGE=$(cat <<'PY'
import json
import re


def _tokenize_command(command):
    token_re = re.compile(r'(?:"([^"]+)"|\'([^\']+)\'|(\S+))')
    for match in token_re.finditer(command):
        yield match.group(1) or match.group(2) or match.group(3)


def _scripts_in_entry(entry):
    scripts = set()
    for hook in entry.get("hooks", []):
        command = hook.get("command")
        if not isinstance(command, str):
            continue
        for token in _tokenize_command(command):
            if not token:
                continue
            if re.search(r'(?i)orchestrator_[^\\/"]+\.py$', token):
                scripts.add(re.split(r'[\\/]', token)[-1])
    return scripts


def _hook_python_command(python_cmd):
    command = python_cmd.strip()
    if not command:
        return command
    if command[0] in ('"', "'"):
        return command
    match = re.match(r'^([A-Za-z]:\\.*?\.exe)(\s+.*)?$', command)
    if match:
        return f'"{match.group(1)}"{match.group(2) or ""}'
    if re.search(r'\s', command) and ('/' in command or '\\' in command):
        return '"' + command.replace('"', '\\"') + '"'
    return command


def _merge_event_entries(existing, incoming):
    existing_entries = [dict(x) for x in existing]
    incoming_entries = [dict(x) for x in incoming]

    incoming_scripts = set()
    for entry in incoming_entries:
        incoming_scripts |= _scripts_in_entry(entry)

    if incoming_scripts:
        deduped_existing = []
        for entry in existing_entries:
            hooks = entry.get("hooks", [])
            if not hooks:
                deduped_existing.append(entry)
                continue

            kept_hooks = []
            for hook in hooks:
                script_names = _scripts_in_entry({"hooks": [hook]})
                if script_names and (script_names & incoming_scripts):
                    continue
                kept_hooks.append(hook)

            if not kept_hooks:
                continue

            merged_entry = dict(entry)
            merged_entry["hooks"] = kept_hooks
            deduped_existing.append(merged_entry)
        existing_entries = deduped_existing

    existing_signatures = {
        json.dumps(entry, sort_keys=True, separators=(",", ":"))
        for entry in existing_entries
    }
    for entry in incoming_entries:
        signature = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        if signature in existing_signatures:
            continue
        existing_entries.append(entry)
        existing_signatures.add(signature)

    return existing_entries


def merge_hook_json(template, codex_home, python_cmd, current):
    rendered = template.replace("{{CODEX_HOME}}", json.dumps(codex_home)[1:-1])
    rendered = rendered.replace("{{PYTHON_EXE}}", json.dumps(_hook_python_command(python_cmd))[1:-1])
    incoming = json.loads(rendered)
    if not isinstance(incoming, dict):
        raise SystemExit("Template render did not produce a JSON object")

    merged = {"hooks": {}}
    base = current.get("hooks", {}) if isinstance(current, dict) else {}
    for event, entries in incoming.get("hooks", {}).items():
        merged["hooks"][event] = _merge_event_entries(base.get(event, []), entries)
    for event, entries in base.items():
        if event not in merged["hooks"]:
            merged["hooks"][event] = entries
    return merged
PY
)

merge_hooks() {
  local template="$SCRIPT_DIR/templates/hooks.json.tpl"
  local output="$CODEX_TARGET/hooks.json"
  local codex_home="${CODEX_TARGET//\\//}"

  if [[ "$DRY_RUN" == "1" ]]; then
    "${PYTHON_RUN[@]}" - "$template" "$codex_home" "$PYTHON_CMD" "$PYTHON_MERGE" <<'PY'
import json
import sys

template_path, codex_home, python_cmd, merge_script = sys.argv[1:5]
with open(template_path, 'r', encoding='utf-8') as stream:
    template = stream.read()
globals_ns = {'__name__': '__merge__'}
exec(compile(merge_script, '<merge_hook>', 'exec'), globals_ns)
merged = globals_ns['merge_hook_json'](template, codex_home, python_cmd, {})
print(json.dumps(merged, indent=2))
PY
    return
  fi

  if [[ -f "$output" ]]; then
    cp "$output" "${output}.bak.$(backup_suffix)"
  fi

  "${PYTHON_RUN[@]}" - "$template" "$codex_home" "$PYTHON_CMD" "$output" "$PYTHON_MERGE" <<'PY'
import json
import sys

template_path, codex_home, python_cmd, output, merge_script = sys.argv[1:6]
with open(template_path, 'r', encoding='utf-8') as stream:
    template = stream.read()
try:
    with open(output, 'r', encoding='utf-8') as stream:
        current = json.load(stream)
except Exception:
    current = {}
globals_ns = {'__name__': '__merge__'}
exec(compile(merge_script, '<merge_hook>', 'exec'), globals_ns)
merged = globals_ns['merge_hook_json'](template, codex_home, python_cmd, current)
with open(output, 'w', encoding='utf-8') as stream:
    json.dump(merged, stream, indent=2)
PY
}

copy_sources "$SCRIPT_DIR/skills/orchestrator" "$AGENTS_TARGET/skills/orchestrator"
copy_sources "$SCRIPT_DIR/skills/orchestrator-observability" "$AGENTS_TARGET/skills/orchestrator-observability"
copy_sources "$SCRIPT_DIR/hooks" "$CODEX_TARGET/hooks"
copy_sources "$SCRIPT_DIR/agents" "$CODEX_TARGET/agents"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[DRY-RUN] merge preview:"
  merge_hooks >/dev/null
else
  merge_hooks
fi

echo "done"
