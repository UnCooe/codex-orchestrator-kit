#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

from orchestrator_observability import emit_observability_event
from workflow_profile import current_turn_capabilities


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

SUBAGENT_LIFECYCLE = [
    r"\bsubagents:\s*closed\b",
    r"\bsubagents:\s*retained because\b",
]

ACCEPTANCE_FIELDS = ("verification_status", "review_status", "subagent_status", "risk_status")
ACCEPTANCE_FIELD_RE = re.compile(
    r'(?im)(?:^|[,\n;])\s*"?(?P<field>verification_status|review_status|subagent_status|risk_status)"?\s*[:=：]\s*(?P<value>[^,\n;\}\]]+)'
)
UNSAFE_GIT_DIRECTIVE_RE = re.compile(
    r"::\s*git-(?:stage|commit|create-branch|push|create-pr)\{[^}\n]*\}"
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
FORMATTING_ONLY_REASONS = {
    "formatting_only_missing_acceptance",
    "formatting_only_partial_acceptance",
    "formatting_only_natural_acceptance",
}
BODY_NEGATIVE_VERIFICATION_PATTERNS = [
    r"\bnot run\b",
    r"\bcould not run\b",
    r"\bblocked\b",
    r"\bdeferred\b",
    r"\bstatic check",
    r"\bstatic analysis",
    "未跑",
    "未运行",
    "未执行",
    "无法运行",
    "阻塞",
    "只做静态检查",
]
BODY_REMAINING_RISK_PATTERNS = [
    r"\bstill need\b",
    r"\bremaining\b",
    r"\bnot completed\b",
    r"\bneeds validation\b",
    r"\bneeds tunnel\b",
    r"\bmissing\b",
    r"\bdeferred\b",
    "仍缺",
    "未完成",
    "待验证",
    "剩余风险",
    "还缺",
    "仍需",
]
ACCEPTANCE_BLOCK_RE = re.compile(r"(?ims)(?:^|\n)\s*ACCEPTANCE\s*:\s*(?P<body>.*)")
ACCEPTANCE_BLOCK_ITEM_RE = re.compile(r"(?im)^\s*(?:[-*•]|\d+\.)?\s*(?P<key>[A-Za-z][A-Za-z /_-]{2,40})\s*:\s*(?P<value>.+?)\s*$")


def matches_any(patterns, text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def is_status_positive(field: str, value: str) -> bool:
    normalized = str(value).strip().lower()
    if not normalized:
        return False
    if any(bad in normalized for bad in ("critical", "high", "unacceptable", "open", "unknown", "fail", "blocked")):
        return False
    head = re.split(r"[\s,，;；。:？?]+", normalized.replace("：", ":"), maxsplit=1)[0]
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


def parse_acceptance_block(message: str) -> dict[str, str]:
    if not message:
        return {}
    match = ACCEPTANCE_BLOCK_RE.search(message)
    if not match:
        return {}
    body = match.group("body").strip()
    if not body:
        return {}

    captured: dict[str, str] = {}
    for item in ACCEPTANCE_BLOCK_ITEM_RE.finditer(body):
        key = item.group("key").strip().lower().replace(" ", "_").replace("/", "_")
        captured[key] = item.group("value").strip()

    acceptance: dict[str, str] = {}
    verification_bits = " ".join(
        captured.get(key, "")
        for key in (
            "verification_result",
            "verification",
            "v6_preload_result",
            "v6_cache_result",
            "checked_target",
            "checked_es_env",
            "time_window",
        )
        if captured.get(key)
    ).strip()
    risk_bits = " ".join(
        captured.get(key, "")
        for key in ("remaining_risk", "main_remaining_latency_suspect")
        if captured.get(key)
    ).strip()
    lifecycle_bits = " ".join(
        captured.get(key, "")
        for key in ("subagent_lifecycle", "subagents", "lifecycle")
        if captured.get(key)
    ).strip()

    if verification_bits:
        acceptance["verification_status"] = "pass"
    if risk_bits:
        acceptance["risk_status"] = "medium"
    elif verification_bits:
        acceptance["risk_status"] = "low"
    if lifecycle_bits:
        normalized = lifecycle_bits.lower()
        if "retained" in normalized:
            acceptance["subagent_status"] = f"retained because {lifecycle_bits}"
        elif "closed" in normalized or "已关闭" in lifecycle_bits or "收干净" in lifecycle_bits:
            acceptance["subagent_status"] = "closed"
    else:
        acceptance["subagent_status"] = "not_used"
    acceptance["review_status"] = "not_required"
    return acceptance


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


def stop_soft_result(
    payload: dict,
    *,
    reason: str,
    acceptance_source: str,
    acceptance: dict[str, object],
    extra: dict[str, object] | None = None,
) -> int:
    emit_observability_event(
        payload,
        event_name="stop",
        status="warn",
        reason=reason,
        acceptance_source=acceptance_source,
        acceptance=acceptance,
        extra=extra,
    )
    print(json.dumps({"continue": True}))
    return 0


def contains_unsafe_git_directive(message: str) -> bool:
    if not message:
        return False
    return bool(UNSAFE_GIT_DIRECTIVE_RE.search(message))


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


def _verification_claim(fields: dict[str, str], source: str, body_text: str) -> str:
    value = str(fields.get("verification_status", "")).strip()
    if source in {"structured", "natural_block"} and value:
        if is_status_positive("verification_status", value):
            return "pass"
        if is_verification_exception_acceptable(value, str(fields.get("risk_status", ""))):
            return "exception"
        return "negative_or_unknown"
    if matches_any(VERIFICATION, body_text.lower()):
        return "mentioned"
    return "absent"


def _risk_claim(fields: dict[str, str], source: str, body_text: str) -> str:
    value = str(fields.get("risk_status", "")).strip()
    if source in {"structured", "natural_block"} and value:
        if is_low_risk(value):
            return "low"
        if is_status_positive("risk_status", value):
            return "positive_non_low"
        return "negative_or_unknown"
    if body_has_remaining_risk(body_text):
        return "remaining_risk_present"
    return "absent"


def completion_state(
    *,
    source: str,
    fields: dict[str, str],
    did_work: bool,
    has_verification: bool,
    used_subagents: bool,
    spawn_count: int,
    close_count: int,
    body_text: str,
    verification_contradiction: bool,
    risk_contradiction: bool,
) -> dict[str, object]:
    lifecycle_state = "not_used"
    if spawn_count > close_count:
        lifecycle_state = "retained_or_incomplete"
    elif used_subagents:
        lifecycle_state = "closed"
    conflicts: list[str] = []
    if spawn_count > close_count:
        conflicts.append("subagent_lifecycle_incomplete")
    if verification_contradiction:
        conflicts.append("verification_claim_conflict")
    if risk_contradiction:
        conflicts.append("risk_claim_conflict")
    return {
        "source": source,
        "work_done_detected": did_work,
        "verification_evidence": has_verification,
        "verification_claim": _verification_claim(fields, source, body_text),
        "risk_evidence": body_has_remaining_risk(body_text),
        "risk_claim": _risk_claim(fields, source, body_text),
        "subagent_spawn_count": spawn_count,
        "subagent_close_count": close_count,
        "subagent_lifecycle_state": lifecycle_state,
        "completion_conflicts": conflicts,
    }


def _body_without_footer(message: str) -> str:
    if not message:
        return ""
    lines = []
    for line in message.splitlines():
        if ACCEPTANCE_FIELD_RE.search(line):
            continue
        lines.append(line)
    return "\n".join(lines)


def body_has_negative_verification(message: str) -> bool:
    return matches_any(BODY_NEGATIVE_VERIFICATION_PATTERNS, message.lower())


def body_has_remaining_risk(message: str) -> bool:
    return matches_any(BODY_REMAINING_RISK_PATTERNS, message.lower())


def acceptance_source_and_fields(message: str) -> tuple[str, dict[str, str]]:
    structured = parse_acceptance_footer(message)
    if structured:
        missing = missing_acceptance_fields(structured)
        if len(structured) == len(ACCEPTANCE_FIELDS) and not missing:
            return "structured", structured
        return "partial", structured
    natural = parse_acceptance_block(message)
    if natural:
        return "natural_block", natural
    return "missing", {}


def current_turn_text(transcript_path: str | None) -> str:
    items = _load_turn_items(transcript_path)
    if not items:
        return ""
    return "\n".join(json.dumps(item, ensure_ascii=False) for item in items)[-120000:].lower()


def _turn_payload(item: dict) -> dict:
    payload = item.get("payload", {}) if isinstance(item, dict) else {}
    return payload if isinstance(payload, dict) else {}


def _turn_call_name(item: dict) -> str:
    payload = _turn_payload(item)
    name = payload.get("name")
    return str(name).strip() if isinstance(name, str) else ""


def transcript_has_subagent_signal(transcript_path: str | None) -> bool:
    items = _load_turn_items(transcript_path)
    for item in items:
        if _turn_call_name(item) in {"spawn_agent", "wait_agent", "close_agent"}:
            return True
    return False


def current_turn_subagent_counts(transcript_path: str | None) -> tuple[int, int]:
    spawn_count = 0
    close_count = 0
    for item in _load_turn_items(transcript_path):
        call_name = _turn_call_name(item)
        if call_name == "spawn_agent":
            spawn_count += 1
        elif call_name == "close_agent":
            close_count += 1
    return spawn_count, close_count

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
    if contains_unsafe_git_directive(message):
        emit_observability_event(
            payload,
            event_name="stop",
            status="blocked",
            reason="unsafe_git_directive",
            extra={"directive_guard": "git_directive_plain_text_required"},
        )
        print(json.dumps({
            "decision": "block",
            "reason": (
                "Do not emit ::git-* directives in the final response on this Windows desktop thread; "
                "they can crash the Codex UI renderer. Replace the directive with plain text that names "
                "the branch, commit, PR, or worktree."
            ),
        }))
        return 0

    used_subagents = transcript_has_subagent_signal(payload.get("transcript_path"))
    spawn_count, close_count = current_turn_subagent_counts(payload.get("transcript_path"))
    current_capabilities = current_turn_capabilities(payload)
    acceptance_source, acceptance_fields = acceptance_source_and_fields(message)
    has_verification = matches_any(VERIFICATION, message_lower)
    did_work = matches_any(WORK_DONE, message)
    body_text = _body_without_footer(message)
    verification_contradiction = (
        acceptance_source in {"structured", "natural_block"}
        and is_status_positive("verification_status", acceptance_fields.get("verification_status", ""))
        and body_has_negative_verification(body_text)
    )
    risk_contradiction = (
        acceptance_source in {"structured", "natural_block"}
        and is_status_positive("risk_status", acceptance_fields.get("risk_status", ""))
        and body_has_remaining_risk(body_text)
    )
    structured_verification_signal = (
        acceptance_source in {"structured", "natural_block"}
        and is_status_positive("verification_status", acceptance_fields.get("verification_status", ""))
    )
    base_acceptance = lambda source: acceptance_payload(
        source,
        acceptance_fields,
        has_work_done_signal=did_work,
        has_verification_signal=has_verification or structured_verification_signal,
        has_subagent_signal=used_subagents,
    )
    state = completion_state(
        source=acceptance_source,
        fields=acceptance_fields,
        did_work=did_work,
        has_verification=has_verification or structured_verification_signal,
        used_subagents=used_subagents,
        spawn_count=spawn_count,
        close_count=close_count,
        body_text=body_text,
        verification_contradiction=verification_contradiction,
        risk_contradiction=risk_contradiction,
    )

    if used_subagents and spawn_count > close_count and "retained because" not in message_lower:
        return stop_gate_result(
            payload,
            reason="subagent_lifecycle_incomplete",
            public_reason="Subagent lifecycle is incomplete.",
            acceptance_source=acceptance_source,
            acceptance={**base_acceptance(acceptance_source), "completion_state": state},
            extra={"spawn_count": spawn_count, "close_count": close_count, "completion_state": state},
        )

    verification_value = acceptance_fields.get("verification_status", "")
    risk_value = acceptance_fields.get("risk_status", "")
    verification_ok = (
        not verification_value
        or is_status_positive("verification_status", verification_value)
        or is_verification_exception_acceptable(verification_value, risk_value)
    )
    risk_ok = not risk_value or is_status_positive("risk_status", risk_value)

    if did_work and verification_value and (not verification_ok or verification_contradiction):
        return stop_gate_result(
            payload,
            reason="verification_claim_conflict",
            public_reason="Verification claim conflicts with the summary.",
            acceptance_source=acceptance_source,
            acceptance={**base_acceptance(acceptance_source), "completion_state": state},
            extra={**acceptance_fields, "verification_contradiction": verification_contradiction, "completion_state": state},
        )

    if risk_value and (not risk_ok or risk_contradiction):
        return stop_gate_result(
            payload,
            reason="risk_claim_conflict",
            public_reason="Risk claim conflicts with the summary.",
            acceptance_source=acceptance_source,
            acceptance={**base_acceptance(acceptance_source), "completion_state": state},
            extra={**acceptance_fields, "risk_contradiction": risk_contradiction, "completion_state": state},
        )

    if did_work and acceptance_source == "natural_block":
        return stop_soft_result(
            payload,
            reason="formatting_only_natural_acceptance",
            acceptance_source=acceptance_source,
            acceptance={**base_acceptance(acceptance_source), "completion_state": state},
            extra={**acceptance_fields, "current_turn_capabilities": sorted(current_capabilities), "completion_state": state},
        )

    if did_work and acceptance_source == "partial":
        return stop_soft_result(
            payload,
            reason="formatting_only_partial_acceptance",
            acceptance_source=acceptance_source,
            acceptance={**base_acceptance(acceptance_source), "completion_state": state},
            extra={**acceptance_fields, "missing_fields": missing_acceptance_fields(acceptance_fields), "completion_state": state},
        )

    if did_work and acceptance_source == "missing":
        return stop_soft_result(
            payload,
            reason="formatting_only_missing_acceptance",
            acceptance_source="missing",
            acceptance={**base_acceptance("missing"), "completion_state": state},
            extra={
                "has_work_done_signal": True,
                "has_verification_signal": has_verification,
                "has_subagent_signal": used_subagents,
                "completion_state": state,
            },
        )

    if len(message) < 80:
        emit_observability_event(
            payload,
            event_name="stop",
            status="allow",
            reason="short_message",
            acceptance_source=acceptance_source if acceptance_source == "structured" else "missing",
            acceptance={**base_acceptance(acceptance_source if acceptance_source == "structured" else "missing"), "completion_state": state},
            extra={"current_turn_capabilities": sorted(current_capabilities), "completion_state": state},
        )
        print(json.dumps({"continue": True}))
        return 0

    emit_observability_event(
        payload,
        event_name="stop",
        status="allow",
        reason="stop_gate_passed",
        acceptance_source=acceptance_source if acceptance_source == "structured" else "heuristic",
        acceptance={**base_acceptance(acceptance_source if acceptance_source == "structured" else "heuristic"), "completion_state": state},
        text_source=message,
        extra={"current_turn_capabilities": sorted(current_capabilities), "completion_state": state},
    )
    print(json.dumps({"continue": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
