#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

from orchestrator_observability import emit_observability_event


DELEGATION_MARKERS = [
    "spawn_agent",
    "delegate",
    "delegation decision",
    "parallel agent",
    "subagent",
    "orchestrator workflow",
    "\u5206\u5de5",
    "\u59d4\u6258",
]


def all_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(all_text(v) for v in value.values())
    if isinstance(value, list):
        return "\n".join(all_text(v) for v in value)
    return "" if value is None else str(value)


def has_delegation_signal(transcript_path: str | None) -> bool:
    if not transcript_path:
        return False
    try:
        text = Path(transcript_path).read_text(encoding="utf-8", errors="ignore")[-120000:].lower()
    except Exception:
        return False
    return any(marker in text for marker in DELEGATION_MARKERS)


def block(reason: str) -> int:
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def is_broad_scan(command: str) -> bool:
    c = command.lower()
    broad_patterns = [
        r"\brg\s+--files\b",
        r"\bget-childitem\b.*\b-recurse\b",
        r"\bselect-string\b.*\b-recurse\b",
        r"\bdir\b.*\s/s\b",
        r"\btree\b.*\s/f\b",
        r"\bls\b.*\s-r\b",
        r"\bfind\s+\.\b",
        r"\bgit\s+grep\b",
    ]
    return any(re.search(pattern, c, re.DOTALL) for pattern in broad_patterns)


def patch_file_count(patch: str) -> int:
    return len(re.findall(r"^\*\*\* (?:Add|Update|Delete) File:", patch, flags=re.MULTILINE))


def main() -> int:
    payload = json.load(sys.stdin)
    tool_name = str(payload.get("tool_name", "")).lower()
    tool_input = payload.get("tool_input", {})
    text = all_text(tool_input)

    if has_delegation_signal(payload.get("transcript_path")):
        emit_observability_event(
            payload,
            event_name="pre_tool_use",
            status="allow",
            reason="delegation_signal_detected",
            text_source=text,
            tool_name=tool_name,
        )
        return 0

    if tool_name == "bash" and is_broad_scan(text):
        block(
            "Broad repo scanning is noisy work. First make an orchestrator delegation decision "
            "and prefer a researcher subagent for repo mapping. If this is a tiny direct task, "
            "state the exception and retry with a narrower command."
        )
        emit_observability_event(
            payload,
            event_name="pre_tool_use",
            status="blocked",
            reason="broad_scan_detected",
            tool_name=tool_name,
            text_source=text,
        )
        return 0

    if tool_name in {"apply_patch", "edit", "write"}:
        files = patch_file_count(text)
        large_patch = len(text) > 12000 or text.count("\n") > 220
        if files > 1 or large_patch:
            block(
                "This looks like a multi-file or large code edit without a prior delegation signal. "
                "Split the work first: assign implementation ownership, independent test design, "
                "and review/critique, or state why this edit is a small exception."
            )
            emit_observability_event(
                payload,
                event_name="pre_tool_use",
                status="blocked",
            reason="large_patch_detected",
            tool_name=tool_name,
            text_source=text,
        )
        return 0

    emit_observability_event(
        payload,
        event_name="pre_tool_use",
        status="allow",
        tool_name=tool_name,
        text_source=text,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
