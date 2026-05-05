#!/usr/bin/env python3
import json
import sys
from pathlib import Path

from orchestrator_observability import (
    emit_observability_event,
    normalize_tool_family,
    normalize_tool_name,
    text_length,
)


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
    tool_name = normalize_tool_name(payload.get("tool_name"))
    tool_family = normalize_tool_family(tool_name)
    tool_input = payload.get("tool_input", {})
    tool_response = payload.get("tool_response", "")

    input_length = text_length(tool_input)
    response_text = all_text(tool_response)
    response_length = len(response_text)

    if tool_family != "shell_like" or response_length < 60000 or has_delegation_signal(payload.get("transcript_path")):
        emit_observability_event(
            payload,
            event_name="post_tool_use",
            status="allow",
            reason="non_shell_like_or_small_output_or_subagent_signal",
            tool_name=tool_name,
            tool_family=tool_family,
            normalized_tool_name=tool_name,
            input_length=input_length,
            output_length=response_length,
            text_source=tool_response,
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
        tool_name=tool_name,
        tool_family=tool_family,
        normalized_tool_name=tool_name,
        input_length=input_length,
        output_length=response_length,
        text_source=tool_response,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
