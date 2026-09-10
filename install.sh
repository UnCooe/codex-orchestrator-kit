#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
CODEX_TARGET="${CODEX_HOME:-}"
AGENTS_TARGET="${AGENTS_HOME:-}"
DRY_RUN=1
HOOK_MODE="auto"
SKILLS_MODE="auto"
SKILLS_ONLY=0
AGENTS_TARGET_EXPLICIT=0
HOOK_MODE_EXPLICIT=0
SKILLS_MODE_EXPLICIT=0
MANAGED_PRETOOL_SCRIPTS=(
  critical_action_gate.py
)
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
      AGENTS_TARGET_EXPLICIT=1
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --apply)
      DRY_RUN=0
      shift
      ;;
    --skills-only)
      SKILLS_ONLY=1
      shift
      ;;
    --hook-mode)
      HOOK_MODE="$2"
      HOOK_MODE_EXPLICIT=1
      shift 2
      ;;
    --skills-mode)
      SKILLS_MODE="$2"
      SKILLS_MODE_EXPLICIT=1
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: install.sh [--codex-home PATH] [--agents-home PATH] [--dry-run|--apply] [--skills-only] [--hook-mode auto|hooks_json|config_toml] [--skills-mode auto|agents|codex]" >&2
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

if [[ "$DRY_RUN" != "1" ]]; then
  if [[ "$SKILLS_ONLY" != "1" && "$HOOK_MODE_EXPLICIT" != "1" ]]; then
    echo "Apply mode requires an explicit --hook-mode value: hooks_json, config_toml, or auto" >&2
    exit 2
  fi
  if [[ "$SKILLS_MODE_EXPLICIT" != "1" ]]; then
    echo "Apply mode requires an explicit --skills-mode value: agents, codex, or auto" >&2
    exit 2
  fi
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

echo "mode: $([[ "$DRY_RUN" == "1" ]] && printf 'dry-run' || printf 'apply')"
if [[ "$SKILLS_ONLY" == "1" ]]; then
  echo "input: source=$SCRIPT_DIR, codex_home=$CODEX_TARGET, agents_home=$AGENTS_TARGET, skills_mode=$RESOLVED_SKILLS_MODE, skills_only=true"
else
  echo "input: source=$SCRIPT_DIR, codex_home=$CODEX_TARGET, agents_home=$AGENTS_TARGET, hook_mode=$RESOLVED_HOOK_MODE, skills_mode=$RESOLVED_SKILLS_MODE"
fi
echo "install plan:"
for skill in "${MANAGED_SKILLS[@]}"; do
  echo "  files: $SCRIPT_DIR/skills/$skill -> $SKILLS_TARGET_ROOT/skills/$skill"
done
if [[ "$SKILLS_ONLY" != "1" ]]; then
  for script_name in "${MANAGED_PRETOOL_SCRIPTS[@]}"; do
    echo "  files: $SCRIPT_DIR/hooks/$script_name -> $CODEX_TARGET/hooks/$script_name"
  done
  for plan_entry in \
    "$SCRIPT_DIR/agents -> $CODEX_TARGET/agents"; do
    echo "  files: $plan_entry"
  done
  if [[ "$RESOLVED_HOOK_MODE" == "hooks_json" ]]; then
    echo "  hooks: $CODEX_TARGET/hooks.json ($([[ -f "$CODEX_TARGET/hooks.json" ]] && printf 'backup then merge' || printf 'create'))"
  else
    echo "  hooks: $CODEX_TARGET/config.toml ($([[ -f "$CODEX_TARGET/config.toml" ]] && printf 'backup then merge managed entries' || printf 'create managed entries'))"
  fi
fi

if [[ "$DRY_RUN" != "1" ]]; then
  if [[ -L "$SKILLS_TARGET_ROOT/skills" ]]; then
    echo "Refusing to install managed skills through symlink target: $SKILLS_TARGET_ROOT/skills" >&2
    exit 1
  fi
  mkdir -p "$SKILLS_TARGET_ROOT/skills"
fi

backup_suffix() {
  printf '%s_%s' "$(date +%Y%m%d_%H%M%S)" "$$"
}

fail_if_symlink() {
  local path="$1"
  if [[ -L "$path" ]]; then
    echo "Refusing to operate on symlink target: $path" >&2
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
    echo "Refusing to operate on symlink target: $symlink" >&2
    exit 1
  fi
}

copy_sources() {
  local src_dir="$1"
  local dst_dir="$2"

  if [[ ! -d "$src_dir" ]]; then
    return
  fi

  if [[ "$DRY_RUN" != "1" ]]; then
    fail_if_tree_contains_symlink "$dst_dir"
    mkdir -p "$dst_dir"
  fi

  while IFS= read -r -d '' file; do
    local rel="${file#"$src_dir"/}"
    local dst="${dst_dir}/${rel}"
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "[DRY-RUN] $file -> $dst"
      continue
    fi
    fail_if_symlink "$dst"
    mkdir -p "$(dirname "$dst")"
    if [[ -f "$dst" ]]; then
      local backup="${dst}.bak.$(backup_suffix)"
      cp "$dst" "$backup"
      echo "backed up -> $backup"
    fi
    cp "$file" "$dst"
    echo "installed: $dst"
  done < <(find "$src_dir" -type f ! -path '*/__pycache__/*' ! -name '*.pyc' -print0)
}

copy_file() {
  local src="$1"
  local dst="$2"

  if [[ ! -f "$src" ]]; then
    echo "Source not found: $src" >&2
    exit 1
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY-RUN] $src -> $dst"
    return
  fi

  mkdir -p "$(dirname "$dst")"
  fail_if_symlink "$dst"
  if [[ -f "$dst" ]]; then
    local backup="${dst}.bak.$(backup_suffix)"
    cp "$dst" "$backup"
    echo "backed up -> $backup"
  fi
  cp "$src" "$dst"
  echo "installed: $dst"
}

prune_stale_skill_files() {
  local src_dir="$1"
  local dst_dir="$2"

  if [[ ! -d "$dst_dir" ]]; then
    return
  fi
  if [[ "$DRY_RUN" != "1" ]]; then
    fail_if_tree_contains_symlink "$dst_dir"
  fi

  while IFS= read -r -d '' file; do
    local rel="${file#"$dst_dir"/}"
    if [[ -f "$src_dir/$rel" ]]; then
      continue
    fi
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "[DRY-RUN] retire stale managed skill file: $file"
      continue
    fi
    local backup="${file}.bak.$(backup_suffix)"
    cp "$file" "$backup"
    rm -f "$file"
    echo "retired stale managed skill file: $file (backup: $backup)"
  done < <(
    find "$dst_dir" -type f \
      ! -path '*/__pycache__/*' \
      ! -path '*/.bak.*/*' \
      ! -name '*.pyc' \
      ! -name '*.bak.*' \
      -print0
  )
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

MANAGED_HOOK_SCRIPTS = {
    "orchestrator_user_prompt.py",
    "orchestrator_" + "pre_" + "tool_use.py",
    "orchestrator_post_tool_use.py",
    "orchestrator_stop.py",
    "orchestrator_observability.py",
    "workflow_profile.py",
    "first_write_gate.py",
    "critical_action_gate.py",
    "pilot_receipt_gate.py",
}

def scripts_in_entry(entry):
    scripts = set()
    for hook in entry.get("hooks", []):
        command = hook.get("command")
        if not isinstance(command, str):
            continue
        for token in tokenize_command(command):
            script = re.split(r'[\\/]', token)[-1] if token else ""
            if script.casefold() in {name.casefold() for name in MANAGED_HOOK_SCRIPTS}:
                scripts.add(script)
    return scripts

def filter_managed_hooks(entries):
    filtered_entries = []
    for entry in entries:
        hooks = entry.get("hooks", [])
        if not hooks:
            filtered_entries.append(entry)
            continue
        kept = []
        for hook in hooks:
            names = scripts_in_entry({"hooks": [hook]})
            if names:
                continue
            kept.append(hook)
        if kept:
            new_entry = dict(entry)
            new_entry["hooks"] = kept
            filtered_entries.append(new_entry)
    return filtered_entries

def merge_event_entries(existing, updates):
    existing_entries = filter_managed_hooks([dict(x) for x in existing])
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
base = {
    event: filter_managed_hooks(entries)
    for event, entries in base.items()
}
for event, entries in incoming.get("hooks", {}).items():
    merged["hooks"][event] = merge_event_entries(base.get(event, []), entries)
for event, entries in base.items():
    if event not in merged["hooks"]:
        filtered = filter_managed_hooks(entries)
        if filtered:
            merged["hooks"][event] = filtered

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
try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None
from pathlib import Path

template_path, codex_home, python_cmd, output, begin_marker, end_marker, dry_run = sys.argv[1:8]
template = Path(template_path).read_text(encoding="utf-8")
rendered = template.replace("{{CODEX_HOME}}", json.dumps(codex_home)[1:-1])
rendered = rendered.replace("{{PYTHON_EXE}}", json.dumps(python_cmd)[1:-1])

path = Path(output)
try:
    current = path.read_text(encoding="utf-8")
except FileNotFoundError:
    current = ""

managed_pattern = re.compile(
    rf"(?ms)^[ \t]*{re.escape(begin_marker)}\n.*?^[ \t]*{re.escape(end_marker)}\n?"
)
current = re.sub(managed_pattern, "", current)

EVENT_HEADER_RE = re.compile(r"^\[\[hooks\.([A-Za-z0-9_]+)\]\]\s*$")
SUBHOOK_HEADER_RE = re.compile(r"^\[\[hooks\.([A-Za-z0-9_]+)\.hooks\]\]\s*$")
ANY_HEADER_RE = re.compile(r"^\[")
MANAGED_HOOK_SCRIPT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])(?:orchestrator_user_prompt|orchestrator_"
    r"pre_tool_use|orchestrator_post_tool_use|orchestrator_stop|orchestrator_observability|"
    r"workflow_profile|first_write_gate|critical_action_gate|pilot_receipt_gate)"
    r"\.py(?![A-Za-z0-9_.-])"
)

def split_hook_region(text):
    lines = text.splitlines(keepends=True)
    start = None
    end = None
    for index, line in enumerate(lines):
        if EVENT_HEADER_RE.match(line.strip()):
            start = index
            break
    if start is None:
        return text.rstrip(), "", ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped:
            continue
        if ANY_HEADER_RE.match(stripped) and not stripped.startswith("[[hooks."):
            end = index
            break
    prefix = "".join(lines[:start]).rstrip()
    hook_region = "".join(lines[start:end])
    suffix = "".join(lines[end:]).lstrip("\n")
    return prefix, hook_region, suffix

def parse_event_blocks(text):
    lines = text.splitlines(keepends=True)
    blocks = []
    current_event = None
    current_lines = []
    for line in lines:
        match = EVENT_HEADER_RE.match(line.strip())
        if match:
            if current_event is not None:
                blocks.append((current_event, "".join(current_lines)))
            current_event = match.group(1)
            current_lines = [line]
            continue
        if current_event is not None:
            current_lines.append(line)
    if current_event is not None:
        blocks.append((current_event, "".join(current_lines)))
    return blocks

def split_event_block(block_text, event):
    lines = block_text.splitlines(keepends=True)
    header = []
    subblocks = []
    current_subblock = []
    inside_subblock = False
    expected_header = f"[[hooks.{event}.hooks]]"
    for line in lines:
        if line.strip() == expected_header:
            if inside_subblock and current_subblock:
                subblocks.append("".join(current_subblock))
            current_subblock = [line]
            inside_subblock = True
            continue
        if inside_subblock:
            current_subblock.append(line)
        else:
            header.append(line)
    if inside_subblock and current_subblock:
        subblocks.append("".join(current_subblock))
    return "".join(header), subblocks

def subblock_has_managed_hook(subblock):
    return any(
        not line.lstrip().startswith("#") and MANAGED_HOOK_SCRIPT_RE.search(line)
        for line in subblock.splitlines()
    )

def normalize_block(block_text, event):
    header, subblocks = split_event_block(block_text, event)
    kept_subblocks = [
        subblock for subblock in subblocks if not subblock_has_managed_hook(subblock)
    ]
    if not kept_subblocks:
        return None
    header = header.rstrip() + "\n"
    rendered = header
    for subblock in kept_subblocks:
        rendered += "\n" + subblock.rstrip() + "\n"
    return rendered.rstrip() + "\n"

def append_managed_hook(block_text, managed_block_text, event):
    existing_header, existing_subblocks = split_event_block(block_text, event)
    managed_header, managed_subblocks = split_event_block(managed_block_text, event)

    if not existing_subblocks:
        base_header = managed_header if managed_header.strip() else existing_header
        existing_subblocks = []
    else:
        base_header = existing_header

    existing_joined = "\n".join(existing_subblocks)
    merged_subblocks = list(existing_subblocks)
    for subblock in managed_subblocks:
        if subblock in merged_subblocks:
            continue
        merged_subblocks.append(subblock)
        existing_joined += "\n" + subblock

    result = base_header.rstrip() + "\n"
    for subblock in merged_subblocks:
        result += "\n" + subblock.rstrip() + "\n"
    return result.rstrip() + "\n"

prefix, hook_region, suffix = split_hook_region(current)
existing_blocks = parse_event_blocks(hook_region)
merged_blocks = []
for event, block_text in existing_blocks:
    normalized = normalize_block(block_text, event)
    if normalized is not None:
        merged_blocks.append(normalized.rstrip())
merged_blocks.append(rendered.strip())

sections = []
if prefix:
    sections.append(prefix.rstrip())
if merged_blocks:
    sections.append("\n\n".join(block for block in merged_blocks if block).rstrip())
if suffix:
    sections.append(suffix.rstrip())
new_text = "\n\n".join(section for section in sections if section) + "\n"

if tomllib is not None:
    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"generated invalid config.toml: {exc}")

if dry_run == "1":
    print(new_text)
else:
    path.write_text(new_text, encoding="utf-8")
PY
}

for skill in "${MANAGED_SKILLS[@]}"; do
  prune_stale_skill_files "$SCRIPT_DIR/skills/$skill" "$SKILLS_TARGET_ROOT/skills/$skill"
  copy_sources "$SCRIPT_DIR/skills/$skill" "$SKILLS_TARGET_ROOT/skills/$skill"
done

verify_skill_only_install() {
  if [[ "$DRY_RUN" == "1" ]]; then
    return
  fi
  "${PYTHON_RUN[@]}" "$SCRIPT_DIR/scripts/verify_skill_install.py" --source "$SCRIPT_DIR/skills" --target "$SKILLS_TARGET_ROOT/skills"
}

verify_skill_only_install

if [[ "$SKILLS_ONLY" == "1" ]]; then
  echo "done"
  exit 0
fi

copy_sources "$SCRIPT_DIR/agents" "$CODEX_TARGET/agents"

remove_deprecated_agent() {
  local target="$CODEX_TARGET/agents/$1"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY-RUN] remove deprecated agent if present: $target"
    return
  fi
  if [[ -e "$target" ]]; then
    local backup="${target}.bak.$(backup_suffix)"
    cp "$target" "$backup"
    rm -f "$target"
    echo "removed deprecated agent: $target (backup: $backup)"
  fi
}

remove_deprecated_agent "codebase-research-explorer.toml"

verify_skill_install() {
  if [[ "$DRY_RUN" == "1" ]]; then
    return
  fi
  "${PYTHON_RUN[@]}" "$SCRIPT_DIR/scripts/verify_skill_install.py" \
    --source "$SCRIPT_DIR/skills" \
    --target "$SKILLS_TARGET_ROOT/skills" \
    --source-agents "$SCRIPT_DIR/agents" \
    --target-agents "$CODEX_TARGET/agents"
}

verify_skill_install

if [[ "$DRY_RUN" != "1" ]]; then
  fail_if_symlink "$CODEX_TARGET/hooks"
  if [[ "$RESOLVED_HOOK_MODE" == "hooks_json" ]]; then
    fail_if_symlink "$CODEX_TARGET/hooks.json"
  else
    fail_if_symlink "$CODEX_TARGET/config.toml"
  fi
fi

for script_name in "${MANAGED_PRETOOL_SCRIPTS[@]}"; do
  copy_file \
    "$SCRIPT_DIR/hooks/$script_name" \
    "$CODEX_TARGET/hooks/$script_name"
done

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[DRY-RUN] hook_mode=$RESOLVED_HOOK_MODE skills_mode=$RESOLVED_SKILLS_MODE skills_root=$SKILLS_TARGET_ROOT"
fi

if [[ "$RESOLVED_HOOK_MODE" == "hooks_json" ]]; then
  fail_if_symlink "$CODEX_TARGET/hooks.json"
  if [[ "$DRY_RUN" != "1" && -f "$CODEX_TARGET/hooks.json" ]]; then
    hook_backup="$CODEX_TARGET/hooks.json.bak.$(backup_suffix)"
    cp "$CODEX_TARGET/hooks.json" "$hook_backup"
    echo "backed up -> $hook_backup"
  fi
  merge_hooks_json >/dev/null
  [[ "$DRY_RUN" == "1" ]] || echo "installed: $CODEX_TARGET/hooks.json"
else
  fail_if_symlink "$CODEX_TARGET/config.toml"
  if [[ "$DRY_RUN" != "1" && -f "$CODEX_TARGET/config.toml" ]]; then
    config_backup="$CODEX_TARGET/config.toml.bak.$(backup_suffix)"
    cp "$CODEX_TARGET/config.toml" "$config_backup"
    echo "backed up -> $config_backup"
  fi
  merge_config_toml_hooks >/dev/null
  [[ "$DRY_RUN" == "1" ]] || echo "installed: $CODEX_TARGET/config.toml"
fi

echo "done"
