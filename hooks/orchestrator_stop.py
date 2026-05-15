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

HEAVY_PATTERNS = [
    r"\bimplement\b",
    r"\bfix\b",
    r"\brefactor\b",
    r"\breview\b",
    r"\binvestigate\b",
    r"\banaly[sz]e\b",
    r"\btest\b",
    r"\bdebug\b",
    r"\bmigrate\b",
    r"\barchitecture\b",
    r"\bdesign\b",
    r"\brepo\b",
    r"\bcodebase\b",
    "实现",
    "修复",
    "代码",
    "仓库",
    "调研",
    "测试",
    "审查",
    "方案",
    "风险",
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
    r'(?im)(?:^|[,\n;])\s*"?(?P<field>verification_status|review_status|subagent_status|risk_status)"?\s*[:=：]\s*(?P<value>[^,\n;\}\]]+)'
)
ACCEPTANCE_STATUS_MAP = {
    "verification_status": {"pass", "passed", "verified", "done", "completed", "ok", "yes", "true", "1", "checked", "已完成", "已验证", "已检查", "已核对", "已执行", "完成", "通过"},
    "review_status": {"pass", "passed", "reviewed", "done", "completed", "ok", "yes", "true", "1", "not_required", "n/a", "na", "已检查", "已核对", "完成", "通过"},
    "subagent_status": {"closed", "retained because", "retained", "not used", "not_used", "none", "n/a", "na", "not_required"},
    "risk_status": {"low", "moderate", "medium", "manageable", "acceptable", "none", "ok", "yes", "true", "1", "not significant", "低", "中", "中等", "可控"},
}

VERIFICATION_EXCEPTION_TOKENS = (
    "partial",
    "not_run",
    "not run",
    "blocked",
    "read-only",
    "readonly",
    "static",
    "只读",
    "部分",
    "未运行",
    "未执行",
    "未做",
    "无法运行",
    "阻塞",
)

LOW_RISK_TOKENS = ("low", "none", "not significant", "低")


def matches_any(patterns, text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def is_status_positive(field: str, value: str) -> bool:
    normalized = str(value).strip().lower()
    if not normalized:
        return False
    if any(bad in normalized for bad in ("critical", "high", "unacceptable", "open", "unknown", "fail", "blocked")):
        return False
    head = re.split(r"[\s,，;；。:]+", normalized.replace("：", ":"), maxsplit=1)[0]
    for token in ACCEPTANCE_STATUS_MAP.get(field, set()):
        if any("\u4e00" <= char <= "\u9fff" for char in token):
            if normalized.startswith(token):
                return True
        elif " " in token:
            if normalized.startswith(token):
                return True
        elif head == token:
            return True
    return False


def has_short_note(value: str) -> bool:
    return len(str(value).strip()) >= 12


def is_low_risk(value: str) -> bool:
    normalized = str(value).strip().lower()
    if any(bad in normalized for bad in ("medium", "moderate", "high", "critical", "中", "高")):
        return False
    return any(token in normalized for token in LOW_RISK_TOKENS)


def is_verification_exception_acceptable(value: str, risk_value: str) -> bool:
    normalized = str(value).strip().lower()
    if not normalized or not has_short_note(value):
        return False
    if not any(token in normalized for token in VERIFICATION_EXCEPTION_TOKENS):
        return False
    if "partial" in normalized or "部分" in normalized:
        return not is_low_risk(risk_value)
    return True


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


def _load_turn_items(transcript_path: str | None) -> list[dict]:
    if not transcript_path:
        return []
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []

    last_user_index = -1
    parsed: list[dict | None] = []
    for index, line in enumerate(lines):
        try:
            item = json.loads(line)
        except Exception:
            parsed.append(None)
            continue
        parsed.append(item)
        payload = item.get("payload", {}) if isinstance(item, dict) else {}
        if payload.get("type") == "message" and payload.get("role") == "user":
            content_text = str(payload.get("content", ""))
            if "<hook_prompt" not in content_text:
                last_user_index = index

    if last_user_index < 0:
        return []
    return [item for item in parsed[last_user_index + 1:] if isinstance(item, dict)]


def stop_gate_result(
    payload: dict,
    *,
    reason: str,
    public_reason: str,
    acceptance_source: str,
    acceptance: dict[str, object],
    extra: dict[str, object] | None = None,
) -> int:
    emit_observability_event(
        payload,
        event_name="stop",
        status="blocked",
        reason=reason,
        acceptance_source=acceptance_source,
        acceptance=acceptance,
        extra=extra,
    )
    print(json.dumps({
        "decision": "block",
        "reason": public_reason,
        "suppressOutput": True,
    }))
    return 0


def missing_acceptance_fields(fields: dict[str, str]) -> list[str]:
    return [field for field in ACCEPTANCE_FIELDS if not str(fields.get(field, "")).strip()]


def acceptance_payload(
    source: str,
    fields: dict[str, str],
    *,
    has_work_done_signal: bool,
    has_verification_signal: bool,
    has_subagent_signal: bool,
) -> dict[str, object]:
    missing_fields = missing_acceptance_fields(fields)
    payload: dict[str, object] = {
        "source": source,
        "has_work_done_signal": has_work_done_signal,
        "has_verification_signal": has_verification_signal,
        "has_subagent_signal": has_subagent_signal,
        "footer_field_count": len(fields),
        "footer_complete": not missing_fields and len(fields) == len(ACCEPTANCE_FIELDS),
        "footer_missing_fields": missing_fields,
    }
    for field in ACCEPTANCE_FIELDS:
        payload[field] = fields.get(field, "")
    return payload


def current_turn_text(transcript_path: str | None) -> str:
    items = _load_turn_items(transcript_path)
    if not items:
        return ""
    return "\n".join(json.dumps(item, ensure_ascii=False) for item in items)[-120000:].lower()


def current_user_prompt_text(transcript_path: str | None) -> str:
    if not transcript_path:
        return ""
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return ""
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except Exception:
            continue
        payload = item.get("payload", {}) if isinstance(item, dict) else {}
        if payload.get("type") == "message" and payload.get("role") == "user":
            content_text = str(payload.get("content", ""))
            if "<hook_prompt" not in content_text:
                return content_text.lower()
    return ""


def transcript_has_subagent_signal(transcript_path: str | None) -> bool:
    text = current_turn_text(transcript_path)
    if not text:
        return False
    return any(marker in text for marker in SUBAGENT_MARKERS)


def current_turn_subagent_counts(transcript_path: str | None) -> tuple[int, int]:
    text = current_turn_text(transcript_path)
    if not text:
        return 0, 0
    spawn_count = len(re.findall(r'"name"\s*:\s*"spawn_agent"|<subagent_notification', text))
    close_count = len(re.findall(r'"name"\s*:\s*"close_agent"', text))
    return spawn_count, close_count


def current_turn_is_heavy(transcript_path: str | None) -> bool:
    prompt_text = current_user_prompt_text(transcript_path)
    if not prompt_text:
        return False
    return matches_any(HEAVY_PATTERNS, prompt_text)


def subagent_status_has_reason(value: str) -> bool:
    normalized = str(value).strip()
    if not normalized:
        return False
    head = re.split(r"[\s,，;；。:：]+", normalized.lower(), maxsplit=1)[0]
    if head not in {"not_used", "not", "none", "closed", "pass"} and "not used" not in normalized.lower():
        return True
    return has_short_note(normalized)


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
    spawn_count, close_count = current_turn_subagent_counts(payload.get("transcript_path"))
    is_heavy_turn = current_turn_is_heavy(payload.get("transcript_path"))
    has_subagent_lifecycle = matches_any(SUBAGENT_LIFECYCLE, message)
    acceptance_fields = parse_acceptance_footer(message)
    footer_missing = missing_acceptance_fields(acceptance_fields)
    if len(acceptance_fields) == len(ACCEPTANCE_FIELDS) and not footer_missing:
        acceptance_source = "structured"
    elif acceptance_fields:
        acceptance_source = "partial"
    else:
        acceptance_source = "missing"
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
                return stop_gate_result(
                    payload,
                    reason="acceptance_footer_subagent_status_reject",
                    public_reason=(
                        "Structured acceptance footer indicates subagent_status is not closed/retained. "
                        "Before finalizing, include a complete acceptance footer with verification_status, "
                        "review_status, subagent_status, and risk_status, and state the lifecycle outcome: "
                        "'Subagents: closed' or 'Subagents: retained because ...'."
                    ),
                    acceptance_source=acceptance_source,
                    acceptance=base_acceptance(acceptance_source),
                    extra={**acceptance_fields},
                )
        else:
            acceptance_source = "heuristic"

    if used_subagents and spawn_count > close_count and "subagents: retained because" not in message_lower:
        return stop_gate_result(
            payload,
            reason="subagent_close_missing",
            public_reason=(
                "Current turn spawned subagents without enough close events. Before finalizing, either close them "
                "and say 'Subagents: closed', or keep them intentionally and say 'Subagents: retained because ...'."
            ),
            acceptance_source=acceptance_source,
            acceptance=base_acceptance(acceptance_source),
            extra={"spawn_count": spawn_count, "close_count": close_count},
        )

    if used_subagents and not has_subagent_lifecycle:
        return stop_gate_result(
            payload,
            reason="subagent_lifecycle_missing",
            public_reason=(
                "Subagent usage was detected. Before finalizing, add a complete acceptance footer "
                "(verification_status, review_status, subagent_status, risk_status) and state the lifecycle "
                "outcome: 'Subagents: closed' or 'Subagents: retained because ...'."
            ),
            acceptance_source=acceptance_source,
            acceptance=base_acceptance(acceptance_source),
            extra={
                "has_subagent_signal": True,
                "has_verification": has_verification,
            },
        )

    if (
        did_work
        and is_heavy_turn
        and not used_subagents
        and acceptance_source == "structured"
        and not subagent_status_has_reason(acceptance_fields.get("subagent_status", ""))
    ):
        return stop_gate_result(
            payload,
            reason="heavy_work_not_delegated_without_reason",
            public_reason=(
                "Heavy-work turn finished without subagent use, but subagent_status does not explain why. "
                "If you intentionally stayed local, keep subagent_status=not_used and add a short reason."
            ),
            acceptance_source=acceptance_source,
            acceptance=base_acceptance(acceptance_source),
            extra={**acceptance_fields, "heavy_turn": True},
        )

    if did_work and acceptance_source == "partial":
        return stop_gate_result(
            payload,
            reason="acceptance_footer_incomplete",
            public_reason=(
                "Acceptance footer is incomplete. Add these fields before finalizing: "
                + ", ".join(footer_missing)
                + "."
            ),
            acceptance_source=acceptance_source,
            acceptance=base_acceptance(acceptance_source),
            extra={
                **acceptance_fields,
                "missing_fields": footer_missing,
            },
        )

    if did_work and acceptance_source == "missing":
        return stop_gate_result(
            payload,
            reason="acceptance_footer_missing",
            public_reason=(
                "Work completion was detected without the required acceptance footer. "
                "Before finalizing, add verification_status, review_status, subagent_status, "
                "and risk_status, plus short notes for tests/checks run or why not run."
            ),
            acceptance_source="missing",
            acceptance=base_acceptance("missing"),
            extra={
                "has_work_done_signal": True,
                "has_verification_signal": has_verification,
                "has_subagent_signal": used_subagents,
            },
        )

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
        verification_value = acceptance_fields.get("verification_status", "")
        risk_value = acceptance_fields.get("risk_status", "")
        verification_ok = (
            is_status_positive("verification_status", verification_value)
            or is_verification_exception_acceptable(verification_value, risk_value)
        )
        review_ok = is_status_positive("review_status", acceptance_fields.get("review_status", ""))
        subagent_ok = is_status_positive("subagent_status", acceptance_fields.get("subagent_status", ""))
        risk_ok = is_status_positive("risk_status", risk_value)

        if did_work and not verification_ok:
            return stop_gate_result(
                payload,
                reason="acceptance_footer_verification_fail",
                public_reason=(
                    "verification_status is not acceptable. Use pass only for complete verification. "
                    "For partial/not_run/blocked verification, keep that status but add a short note and a non-low risk_status."
                ),
                acceptance_source=acceptance_source,
                acceptance=base_acceptance(acceptance_source),
                extra={**acceptance_fields, "has_verification": False},
            )

        if did_work and not review_ok:
            return stop_gate_result(
                payload,
                reason="acceptance_footer_review_fail",
                public_reason="Structured acceptance footer indicates review is incomplete. Add review_status=pass or an explicit exception.",
                acceptance_source=acceptance_source,
                acceptance=base_acceptance(acceptance_source),
                extra={**acceptance_fields, "has_review": False},
            )

        if used_subagents and not subagent_ok:
            return stop_gate_result(
                payload,
                reason="acceptance_footer_subagent_status_reject",
                public_reason="Structured acceptance footer indicates subagent_status is not closed/retained.",
                acceptance_source=acceptance_source,
                acceptance=base_acceptance(acceptance_source),
                extra={**acceptance_fields},
            )

        if not risk_ok:
            return stop_gate_result(
                payload,
                reason="acceptance_footer_risk_fail",
                public_reason="Structured acceptance footer indicates risk status is unresolved/high.",
                acceptance_source=acceptance_source,
                acceptance=base_acceptance(acceptance_source),
                extra={**acceptance_fields},
            )

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
