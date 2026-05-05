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

ACCEPTANCE_FIELDS = ("verification_status", "review_status", "subagent_status", "risk_status")
ACCEPTANCE_FIELD_RE = re.compile(
    r'(?im)(?:^|[,\n;])\s*"?(?P<field>verification_status|review_status|subagent_status|risk_status)"?\s*[:=]\s*(?P<value>[^,\n;\}\]]+)'
)
ACCEPTANCE_STATUS_MAP = {
    "verification_status": {"pass", "passed", "verified", "done", "completed", "ok", "yes", "true", "1", "validation"},
    "review_status": {"pass", "passed", "reviewed", "done", "completed", "ok", "yes", "true", "1", "not_required", "n/a", "na"},
    "subagent_status": {"closed", "retained because", "retained", "not used", "none", "n/a", "na", "not_required"},
    "risk_status": {"low", "moderate", "medium", "manageable", "acceptable", "none", "ok", "yes", "true", "1", "not significant"},
}


def matches_any(patterns, text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def is_status_positive(field: str, value: str) -> bool:
    normalized = str(value).strip().lower()
    if not normalized:
        return False
    if any(bad in normalized for bad in ("critical", "high", "unacceptable", "open", "unknown", "fail", "blocked")):
        return False
    return any(token in normalized for token in ACCEPTANCE_STATUS_MAP.get(field, set()))


def parse_acceptance_footer(message: str) -> dict[str, str]:
    if not message:
        return {}
    footer = message[-2200:]
    values: dict[str, str] = {}
    for match in ACCEPTANCE_FIELD_RE.finditer(footer):
        key = match.group("field")
        value = match.group("value").strip().strip('\'"`')
        values[key] = value
    return values


def acceptance_payload(
    source: str,
    fields: dict[str, str],
    *,
    has_work_done_signal: bool,
    has_verification_signal: bool,
    has_subagent_signal: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "source": source,
        "has_work_done_signal": has_work_done_signal,
        "has_verification_signal": has_verification_signal,
        "has_subagent_signal": has_subagent_signal,
    }
    for field in ACCEPTANCE_FIELDS:
        payload[field] = fields.get(field, "")
    return payload


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
            acceptance_source="missing",
        )
        print(json.dumps({"continue": True}))
        return 0

    message = payload.get("last_assistant_message") or ""
    message_lower = str(message).lower()
    used_subagents = transcript_has_subagent_signal(payload.get("transcript_path"))
    has_subagent_lifecycle = matches_any(SUBAGENT_LIFECYCLE, message)
    acceptance_fields = parse_acceptance_footer(message)
    acceptance_source = "structured" if len(acceptance_fields) == len(ACCEPTANCE_FIELDS) else "missing"
    has_verification = matches_any(VERIFICATION, message_lower)
    did_work = matches_any(WORK_DONE, message)
    structured_verification_signal = (
        acceptance_source == "structured"
        and is_status_positive("verification_status", acceptance_fields.get("verification_status", ""))
    )
    base_acceptance = lambda source: acceptance_payload(
        source,
        acceptance_fields,
        has_work_done_signal=did_work,
        has_verification_signal=has_verification or structured_verification_signal,
        has_subagent_signal=used_subagents,
    )

    if used_subagents and not has_subagent_lifecycle:
        if acceptance_source == "structured":
            subagent_ok = is_status_positive("subagent_status", acceptance_fields.get("subagent_status", ""))
            if subagent_ok:
                has_subagent_lifecycle = True
            else:
                emit_observability_event(
                    payload,
                    event_name="stop",
                    status="blocked",
                    reason="acceptance_footer_subagent_status_reject",
                    acceptance_source=acceptance_source,
                    acceptance=base_acceptance(acceptance_source),
                    extra={**acceptance_fields},
                )
                print(json.dumps({
                    "decision": "block",
                    "reason": (
                        "Structured acceptance footer indicates subagent_status is not closed/retained. "
                        "Before finalizing, state the lifecycle outcome: 'Subagents: closed' "
                        "or 'Subagents: retained because ...'."
                    ),
                }))
                return 0
        else:
            acceptance_source = "heuristic"

    if used_subagents and not has_subagent_lifecycle:
        emit_observability_event(
            payload,
            event_name="stop",
            status="blocked",
            reason="subagent_lifecycle_missing",
            acceptance_source=acceptance_source,
            acceptance=base_acceptance(acceptance_source),
            extra={
                "has_subagent_signal": True,
                "has_verification": has_verification,
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
            acceptance_source=acceptance_source if acceptance_source == "structured" else "missing",
            acceptance=base_acceptance(acceptance_source if acceptance_source == "structured" else "missing"),
        )
        print(json.dumps({"continue": True}))
        return 0

    if acceptance_source == "structured":
        verification_ok = is_status_positive("verification_status", acceptance_fields.get("verification_status", ""))
        review_ok = is_status_positive("review_status", acceptance_fields.get("review_status", ""))
        subagent_ok = is_status_positive("subagent_status", acceptance_fields.get("subagent_status", ""))
        risk_ok = is_status_positive("risk_status", acceptance_fields.get("risk_status", ""))

        if did_work and not verification_ok:
            emit_observability_event(
                payload,
                event_name="stop",
                status="blocked",
                reason="acceptance_footer_verification_fail",
                acceptance_source=acceptance_source,
                acceptance=base_acceptance(acceptance_source),
                extra={**acceptance_fields, "has_verification": False},
            )
            print(json.dumps({
                "decision": "block",
                "reason": "Structured acceptance footer indicates verification is incomplete. Add verification_status=pass and a short note why."
            }))
            return 0

        if did_work and not review_ok:
            emit_observability_event(
                payload,
                event_name="stop",
                status="blocked",
                reason="acceptance_footer_review_fail",
                acceptance_source=acceptance_source,
                acceptance=base_acceptance(acceptance_source),
                extra={**acceptance_fields, "has_review": False},
            )
            print(json.dumps({
                "decision": "block",
                "reason": "Structured acceptance footer indicates review is incomplete. Add review_status=pass or an explicit exception."
            }))
            return 0

        if used_subagents and not subagent_ok:
            emit_observability_event(
                payload,
                event_name="stop",
                status="blocked",
                reason="acceptance_footer_subagent_status_reject",
                acceptance_source=acceptance_source,
                acceptance=base_acceptance(acceptance_source),
                extra={**acceptance_fields},
            )
            print(json.dumps({
                "decision": "block",
                "reason": "Structured acceptance footer indicates subagent_status is not closed/retained."
            }))
            return 0

        if not risk_ok:
            emit_observability_event(
                payload,
                event_name="stop",
                status="blocked",
                reason="acceptance_footer_risk_fail",
                acceptance_source=acceptance_source,
                acceptance=base_acceptance(acceptance_source),
                extra={**acceptance_fields},
            )
            print(json.dumps({
                "decision": "block",
                "reason": "Structured acceptance footer indicates risk status is unresolved/high.",
            }))
            return 0

        emit_observability_event(
            payload,
            event_name="stop",
            status="allow",
            reason="acceptance_footer_passed",
            acceptance_source=acceptance_source,
            acceptance=base_acceptance(acceptance_source),
            extra={**acceptance_fields},
        )
        print(json.dumps({"continue": True}))
        return 0

    if did_work and not has_verification:
        emit_observability_event(
            payload,
            event_name="stop",
            status="blocked",
            reason="acceptance_missing",
            acceptance_source="heuristic",
            acceptance=base_acceptance("heuristic"),
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
        acceptance_source=acceptance_source if acceptance_source == "structured" else "heuristic",
        acceptance=base_acceptance(acceptance_source if acceptance_source == "structured" else "heuristic"),
        text_source=message,
    )
    print(json.dumps({"continue": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
