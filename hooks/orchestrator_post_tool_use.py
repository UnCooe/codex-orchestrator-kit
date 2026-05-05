#!/usr/bin/env python3
import json
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


def main() -> int:
    payload = json.load(sys.stdin)
    if str(payload.get("tool_name", "")).lower() != "bash":
        emit_observability_event(
            payload,
            event_name="post_tool_use",
            status="allow",
            reason="non_bash_tool_call",
            text_source=payload.get("tool_name"),
        )
        return 0

    response_text = all_text(payload.get("tool_response", ""))
    if len(response_text) < 60000 or has_delegation_signal(payload.get("transcript_path")):
        emit_observability_event(
            payload,
            event_name="post_tool_use",
            status="allow",
            reason="output_below_threshold_or_subagent_signal",
            text_source=payload.get("tool_response"),
        )
        return 0

    reason = (
        "The previous tool produced large output. Do not carry raw logs or broad scan output in "
        "the main context. Summarize only conclusion, evidence, risks, alternatives, and next action. "
        "If more exploration is needed, delegate it to a researcher/reviewer subagent."
    )
    print(json.dumps({
        "decision": "block",
        "reason": reason,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": reason,
        },
        "continue": False,
        "stopReason": reason,
    }))

    emit_observability_event(
        payload,
        event_name="post_tool_use",
        status="blocked",
        reason="large_output_threshold_hit",
        extra={
            "tool_name": str(payload.get("tool_name", "")).lower(),
            "response_length": len(response_text),
        },
        text_source=payload.get("tool_response"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
