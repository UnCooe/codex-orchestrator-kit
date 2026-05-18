#!/usr/bin/env python3
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orchestrator_observability import emit_observability_event


REMINDER_COOLDOWN_HOURS = 24
HIGH_ALERT_PATH = Path("orchestrator-observability") / "triage" / "alerts" / "high-risk-current.json"

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
    "\u5b9e\u73b0",
    "\u4fee\u590d",
    "\u4ee3\u7801",
    "\u4ed3\u5e93",
    "\u8c03\u7814",
    "\u6d4b\u8bd5",
    "\u5ba1\u67e5",
    "\u65b9\u6848",
    "\u98ce\u9669",
]


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _resolve_codex_home(payload: dict) -> Path:
    transcript_path = payload.get("transcript_path")
    if isinstance(transcript_path, str) and transcript_path.strip():
        try:
            path = Path(transcript_path).expanduser().resolve()
            parts = path.parts
            for index, part in enumerate(parts):
                if part == ".codex":
                    return Path(*parts[: index + 1])
                if part == "sessions" and index > 0:
                    return Path(*parts[:index])
        except Exception:
            pass

    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()


def _load_pending_high_alert(payload: dict) -> tuple[dict | None, Path | None]:
    path = _resolve_codex_home(payload) / HIGH_ALERT_PATH
    try:
        alert = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None, None

    if str(alert.get("status", "")).lower() != "pending":
        return None, None
    if str(alert.get("severity", "")).lower() != "high":
        return None, None
    return alert, path


def _claim_high_alert_reminder(payload: dict) -> dict | None:
    alert, path = _load_pending_high_alert(payload)
    if not alert or not path:
        return None

    now = datetime.now(timezone.utc)
    last_reminded_at = _parse_time(alert.get("last_reminded_at"))
    if last_reminded_at and now - last_reminded_at < timedelta(hours=REMINDER_COOLDOWN_HOURS):
        return None

    alert["last_reminded_at"] = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    alert["reminder_count"] = int(alert.get("reminder_count", 0) or 0) + 1
    try:
        path.write_text(json.dumps(alert, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        return None

    return alert


def _high_alert_context(alert: dict) -> str:
    title = str(alert.get("title") or alert.get("summary") or "Orchestrator 高风险提醒").strip()
    if len(title) > 120:
        title = title[:117] + "..."
    return (
        "ORCHESTRATOR HIGH-RISK ALERT: At the start of this turn, tell the user in one short "
        f"Chinese sentence: Orchestrator 有一个高风险提醒：{title}。回复“处理 orchestrator 提醒”查看。"
    )


def _extract_prompt(payload: dict) -> str:
    for key in ("prompt", "user_prompt", "message", "input"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _acceptance_context() -> str:
    return (
        "收尾要求：最终答复需包含 verification_status、review_status、subagent_status、risk_status；"
        "若本轮用过 subagent，还必须写 Subagents: closed 或 Subagents: retained because ..."
    )


def _heavy_context() -> str:
    return (
        "ORCHESTRATOR CHECK: 先判断是否需要 subagent；如果不委托，请简短说明原因。"
        "不要把内部 workflow/contract/role registration 文本直接展示给用户。"
    )


def main() -> int:
    payload = json.load(sys.stdin)
    prompt = _extract_prompt(payload)
    contexts = []
    alert = _claim_high_alert_reminder(payload)
    if alert:
        contexts.append(_high_alert_context(alert))

    contexts.append(_acceptance_context())

    is_heavy = any(re.search(pattern, prompt, re.IGNORECASE) for pattern in HEAVY_PATTERNS)
    if is_heavy:
        contexts.append(_heavy_context())

    status = "warn" if (alert or is_heavy) else "allow"
    if alert and not is_heavy:
        reason = "high_risk_alert_reminder"
    elif is_heavy:
        reason = "heavy_prompt_detected"
    else:
        reason = "acceptance_context"

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n\n".join(contexts),
        }
    }))
    emit_observability_event(
        payload,
        event_name="user_prompt",
        status=status,
        reason=reason,
        extra={"patterns_present": is_heavy, "high_risk_alert": bool(alert), "acceptance_context": True},
        text_source=prompt,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
