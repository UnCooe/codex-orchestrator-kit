from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_TRACKED = (
    re.compile(r"(^|/)\.env(\..*)?$"),
    re.compile(r"\.(pem|key|p12)$", re.IGNORECASE),
    re.compile(r"(^|/)\.codex/(sessions|orchestrator-observability)(/|$)"),
    re.compile(r"(^|/)orchestrator-observability/(reports|triage)(/|$)"),
    re.compile(r"\.bak\."),
)

WINDOWS_USER_PREFIX = "C:" + "\\\\" + "Users" + "\\\\"
POSIX_USER_PREFIX = "/" + "Users" + "/"
REAL_USER_SEGMENT = r"(?!example(?:[\\/]|$)|path(?:[\\/]|$)|\.\.\.(?:[\\/]|$))[A-Za-z0-9._-]+"

FORBIDDEN_CONTENT = (
    re.compile(re.escape(WINDOWS_USER_PREFIX) + REAL_USER_SEGMENT, re.IGNORECASE),
    re.compile(re.escape(POSIX_USER_PREFIX) + REAL_USER_SEGMENT),
    re.compile(r"BEGIN (RSA|DSA|EC|OPENSSH|PRIVATE) KEY"),
)

ALLOW_SYNTHETIC = {
    "tests/hygiene_check.py",
    "tests/smoke-test.ps1",
    "tests/smoke-test.sh",
    "tests/fixtures/session-observability.jsonl",
}


def repo_source_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        text=True,
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


def main() -> int:
    failures: list[str] = []
    tracked = repo_source_files()

    for rel in tracked:
        normalized = rel.replace("\\", "/")
        for pattern in FORBIDDEN_TRACKED:
            if pattern.search(normalized):
                failures.append(f"forbidden tracked path: {rel}")

        path = ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except FileNotFoundError:
            continue

        for pattern in FORBIDDEN_CONTENT:
            if pattern.search(text):
                failures.append(f"forbidden content in {rel}: {pattern.pattern}")

        if normalized not in ALLOW_SYNTHETIC:
            if "sk-1234567890abcdefghijklmnopqrstuvwxyz" in text or "api_key=supersecret" in text:
                failures.append(f"synthetic secret fixture leaked outside test allowlist: {rel}")

    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print("hygiene check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
