#!/usr/bin/env bash
set -euo pipefail

PACK_PATH=""
AGENTS_TARGET="${AGENTS_HOME:-}"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pack)
      PACK_PATH="$2"
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
      echo "Usage: install-reviewer-pack.sh --pack PATH [--agents-home PATH] [--dry-run]" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$PACK_PATH" ]]; then
  echo "--pack is required" >&2
  exit 2
fi

USER_HOME="${HOME:-${USERPROFILE:-}}"
if [[ -z "$AGENTS_TARGET" ]]; then
  if [[ -z "$USER_HOME" ]]; then
    echo "HOME or USERPROFILE is required unless --agents-home is provided" >&2
    exit 1
  fi
  AGENTS_TARGET="$USER_HOME/.agents"
fi

SKILLS_ROOT="$PACK_PATH/skills"
if [[ ! -d "$SKILLS_ROOT" ]]; then
  echo "Reviewer pack must contain a skills directory: $SKILLS_ROOT" >&2
  exit 1
fi

shopt -s nullglob
SKILL_DIRS=("$SKILLS_ROOT"/*)
if [[ "${#SKILL_DIRS[@]}" -eq 0 ]]; then
  echo "Reviewer pack has no skills to install: $SKILLS_ROOT" >&2
  exit 1
fi

backup_suffix() {
  printf '%s_%s' "$(date +%Y%m%d_%H%M%S)" "$$"
}

copy_skill() {
  local src_dir="$1"
  local skill_name
  local dst_dir
  skill_name=$(basename "$src_dir")
  dst_dir="$AGENTS_TARGET/skills/$skill_name"

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
    echo "installed: $dst"
  done < <(find "$src_dir" -type f ! -path '*/__pycache__/*' ! -name '*.pyc' -print0)
}

if [[ "$DRY_RUN" != "1" ]]; then
  mkdir -p "$AGENTS_TARGET/skills"
fi

for skill_dir in "${SKILL_DIRS[@]}"; do
  [[ -d "$skill_dir" ]] || continue
  copy_skill "$skill_dir"
done

echo "done"
