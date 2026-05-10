#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
PROJECT_PATH=""
PROJECT_ID=""
REVIEWERS="subagent-workflow"
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      PROJECT_PATH="$2"
      shift 2
      ;;
    --project-id)
      PROJECT_ID="$2"
      shift 2
      ;;
    --reviewers)
      REVIEWERS="$2"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: register-project.sh --project PATH [--project-id ID] [--reviewers a,b] [--force]" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$PROJECT_PATH" ]]; then
  echo "--project is required" >&2
  exit 2
fi
if [[ ! -d "$PROJECT_PATH" ]]; then
  echo "Project path not found: $PROJECT_PATH" >&2
  exit 1
fi
if [[ -z "$PROJECT_ID" ]]; then
  PROJECT_ID=$(basename "$PROJECT_PATH")
fi

CODEX_DIR="$PROJECT_PATH/.codex"
CONTEXT_PATH="$CODEX_DIR/project-context.md"
PROFILE_PATH="$CODEX_DIR/review-profile.toml"

if [[ "$FORCE" != "1" && ( -e "$CONTEXT_PATH" || -e "$PROFILE_PATH" ) ]]; then
  echo "Project already has .codex review files. Use --force to overwrite: $CODEX_DIR" >&2
  exit 1
fi

mkdir -p "$CODEX_DIR"
cp "$SCRIPT_DIR/templates/project-context.md" "$CONTEXT_PATH"

python_bin=python3
if ! command -v "$python_bin" >/dev/null 2>&1; then
  python_bin=python
fi

"$python_bin" - "$SCRIPT_DIR/templates/review-profile.toml" "$PROFILE_PATH" "$PROJECT_ID" "$REVIEWERS" <<'PY'
import re
import sys
from pathlib import Path

template_path, output_path, project_id, reviewers_csv = sys.argv[1:5]
reviewers = [item.strip() for item in reviewers_csv.split(",") if item.strip()]
if not reviewers:
    raise SystemExit("At least one reviewer must be specified.")

text = Path(template_path).read_text(encoding="utf-8")
text = re.sub(r'project_id\s*=\s*"[^"]*"', f'project_id = "{project_id.replace(chr(34), chr(92) + chr(34))}"', text)
reviewer_lines = "\n".join(f'  "{reviewer.replace(chr(34), chr(92) + chr(34))}",' for reviewer in reviewers)
text = re.sub(r"(?s)enabled_reviewers\s*=\s*\[.*?\]", f"enabled_reviewers = [\n{reviewer_lines}\n]", text)
Path(output_path).write_text(text, encoding="utf-8")
PY

echo "registered project review profile: $CODEX_DIR"
