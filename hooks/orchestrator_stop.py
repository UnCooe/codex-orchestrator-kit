#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

from orchestrator_observability import emit_observability_event


WORK_DONE = [
    r"\bimplemented\b",
    r"\bupdated\b",
    r"\bcreated\b",
    r"\bchanged\b",
    r"\bmodified\b",
    r"\bfixed\b",
    r"\badded\b",
    r"\bremoved\b",
    "\u5b8c\u6210",
    "\u5b9e\u73b0",
    "\u4fee\u590d",
    "\u4fee\u6539",
    "\u65b0\u589e",
]

VERIFICATION = [
    r"\btest",
    r"\bverification\b",
    r"\bvalidated\b",
    r"\bran\b",
    r"\bnot run\b",
    r"\bcould not run\b",
    r"\breview\b",
    r"\brisk\b",
    "\u6d4b\u8bd5",
    "\u9a8c\u8bc1",
    "\u9a8c\u6536",
    "\u672a\u8fd0\u884c",
]

SUBAGENT_MARKERS = [
    '"name":"spawn_agent"',
    '"name": "spawn_agent"',
    '"name":"wait_agent"',
    '"name": "wait_agent"',
    '"name":"close_agent"',
    '"name": "close_agent"',
    '"tool":"spawn_agent"',
    '"tool": "spawn_agent"',
    "<subagent_notification",
]

SUBAGENT_LIFECYCLE = [
    r"\bsubagents:\s*closed\b",
    r"\bsubagents:\s*retained because\b",
]


def matches_any(patterns, text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def current_turn_text(transcript_path: str | None) -> str:
    if not transcript_path:
        return ""
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return ""

    last_user_index = -1
    for index, line in enumerate(lines):
        try:
            item = json.loads(line)
        except Exception:
            continue
        payload = item.get("payload", {})
        if payload.get("type") == "message" and payload.get("role") == "user":
            content_text = str(payload.get("content", ""))
            if "<hook_prompt" not in content_text:
                last_user_index = index

    if last_user_index < 0:
        return ""
    return "\n".join(lines[last_user_index + 1:])[-120000:].lower()


def transcript_has_subagent_signal(transcript_path: str | None) -> bool:
    text = current_turn_text(transcript_path)
    if not text:
        return False
    return any(marker in text for marker in SUBAGENT_MARKERS)


def main() -> int:
    payload = json.load(sys.stdin)
    if payload.get("stop_hook_active"):
        emit_observability_event(
            payload,
            event_name="stop",
            status="allow",
            reason="stop_hook_active",
        )
        print(json.dumps({"continue": True}))
        return 0

    message = payload.get("last_assistant_message") or ""
    used_subagents = transcript_has_subagent_signal(payload.get("transcript_path"))
    has_subagent_lifecycle = matches_any(SUBAGENT_LIFECYCLE, message)

    if used_subagents and not has_subagent_lifecycle:
        emit_observability_event(
            payload,
            event_name="stop",
            status="blocked",
            reason="subagent_lifecycle_missing",
            extra={
                "has_subagent_signal": True,
                "has_verification": False,
            },
        )
        print(json.dumps({
            "decision": "block",
            "reason": (
                "Subagent usage was detected. Before finalizing, state the lifecycle outcome: "
                "'Subagents: closed' or 'Subagents: retained because ...'."
            ),
        }))
        return 0

    if len(message) < 80:
        emit_observability_event(
            payload,
            event_name="stop",
            status="allow",
            reason="short_message",
        )
        print(json.dumps({"continue": True}))
        return 0

    did_work = matches_any(WORK_DONE, message)
    has_verification = matches_any(VERIFICATION, message)
    if did_work and not has_verification:
        emit_observability_event(
            payload,
            event_name="stop",
            status="blocked",
            reason="acceptance_missing",
            extra={
                "has_work_done_signal": True,
                "has_verification_signal": False,
            },
        )
        print(json.dumps({
            "decision": "block",
            "reason": (
                "Before finalizing, add a concise acceptance note: tests/checks run or why not run, "
                "review/critique status for heavy work or why omitted, and any residual risk."
            ),
        }))
        return 0

    emit_observability_event(
        payload,
        event_name="stop",
        status="allow",
        reason="stop_gate_passed",
        text_source=message,
    )
    print(json.dumps({"continue": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
