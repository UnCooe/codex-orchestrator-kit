#!/usr/bin/env bash
set -euo pipefail

PACK_PATH=""
CODEX_TARGET="${CODEX_HOME:-}"
AGENTS_TARGET="${AGENTS_HOME:-}"
DRY_RUN=0
SKILLS_MODE="auto"
AGENTS_TARGET_EXPLICIT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pack)
      PACK_PATH="$2"
      shift 2
      ;;
    --codex-home)
      CODEX_TARGET="$2"
      shift 2
      ;;
    --agents-home)
      AGENTS_TARGET="$2"
      AGENTS_TARGET_EXPLICIT=1
      shift 2
      ;;
    --skills-mode)
      SKILLS_MODE="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: install-reviewer-pack.sh --pack PATH [--codex-home PATH] [--agents-home PATH] [--skills-mode auto|agents|codex] [--dry-run]" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$PACK_PATH" ]]; then
  echo "--pack is required" >&2
  exit 2
fi

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

case "$SKILLS_MODE" in
  auto|agents|codex) ;;
  *)
    echo "Invalid --skills-mode: $SKILLS_MODE" >&2
    exit 2
    ;;
esac

resolve_skills_mode() {
  if [[ "$SKILLS_MODE" != "auto" ]]; then
    printf '%s\n' "$SKILLS_MODE"
    return
  fi
  if [[ -d "$CODEX_TARGET/skills" ]]; then
    printf '%s\n' "codex"
    return
  fi
  if [[ "$AGENTS_TARGET_EXPLICIT" == "1" || -d "$AGENTS_TARGET/skills" ]]; then
    printf '%s\n' "agents"
    return
  fi
  printf '%s\n' "agents"
}

RESOLVED_SKILLS_MODE=$(resolve_skills_mode)
if [[ "$RESOLVED_SKILLS_MODE" == "codex" ]]; then
  SKILLS_TARGET_ROOT="$CODEX_TARGET"
else
  SKILLS_TARGET_ROOT="$AGENTS_TARGET"
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
  dst_dir="$SKILLS_TARGET_ROOT/skills/$skill_name"

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
  mkdir -p "$SKILLS_TARGET_ROOT/skills"
fi

for skill_dir in "${SKILL_DIRS[@]}"; do
  [[ -d "$skill_dir" ]] || continue
  copy_skill "$skill_dir"
done

echo "done"
