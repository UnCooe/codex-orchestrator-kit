#!/usr/bin/env python3
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orchestrator_observability import emit_observability_event


REMINDER_COOLDOWN_HOURS = 24
HIGH_ALERT_PATH = Path("orchestrator-observability") / "triage" / "alerts" / "high-risk-current.json"


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
    title = str(alert.get("title") or alert.get("summary") or "orchestrator high-risk alert").strip()
    if len(title) > 120:
        title = title[:117] + "..."
    return (
        "ORCHESTRATOR HIGH-RISK ALERT: "
        f"{title}。请先处理这个高风险提醒，再继续当前任务。"
    )


def _emit_context(context: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }))


def main() -> int:
    payload = json.load(sys.stdin)
    alert = _claim_high_alert_reminder(payload)

    if alert:
        context = _high_alert_context(alert)
        _emit_context(context)
        emit_observability_event(
            payload,
            event_name="user_prompt",
            status="warn",
            reason="high_risk_alert_reminder",
            extra={"high_risk_alert": True, "context_emitted": True},
        )
        return 0

    emit_observability_event(
        payload,
        event_name="user_prompt",
        status="allow",
        reason="silent_default",
        extra={"high_risk_alert": False, "context_emitted": False},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
