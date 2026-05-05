#!/usr/bin/env python3
"""Shared lightweight observability helpers for orchestrator hooks."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


_MAX_TEXT_LENGTH = int(os.environ.get("ORCHESTRATOR_OBSERVABILITY_MAX_TEXT", "600"))
_CAPTURE_TEXT = os.environ.get("ORCHESTRATOR_OBSERVABILITY_CAPTURE_TEXT", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_SECRET_KEY = r"(?:api[-_]?(?:key|token)|token|secret|password|bearer)"
_REDACTION_PATTERNS = [
    (
        re.compile(r"(?i)\bsk-[A-Za-z0-9]{20,}\b"),
        "[redacted]",
    ),
    (
        re.compile(rf"(?i)([\"']{_SECRET_KEY}[\"']\s*:\s*[\"'])([^\"'\r\n]*)([\"'])"),
        r"\1[redacted]\3",
    ),
    (
        re.compile(rf"(?i)([\"']{_SECRET_KEY}[\"']\s*:\s*)([^,\}}\]\s]+)"),
        r"\1[redacted]",
    ),
    (
        re.compile(rf"(?i)(\b{_SECRET_KEY}\b\s*[:=]\s*)([^\s,}}\"'\]]+)"),
        r"\1[redacted]",
    ),
]


def _flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_flatten_text(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(_flatten_text(v) for v in value)
    return "" if value is None else str(value)


def _redact_text(text: str) -> str:
    redacted = text
    for pattern, replacement in _REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _capture_text(value: Any) -> Optional[str]:
    if not _CAPTURE_TEXT:
        return None
    text = _flatten_text(value).strip()
    if not text:
        return None
    text = _redact_text(text)
    if len(text) > _MAX_TEXT_LENGTH:
        text = text[: _MAX_TEXT_LENGTH - 3] + "..."
    return text


def _detect_session_id(transcript_path: str | None) -> Optional[str]:
    if not transcript_path:
        return None
    try:
        path = Path(transcript_path)
    except Exception:
        return None
    if path.suffix == ".jsonl":
        return path.stem
    return None


def _resolve_codex_home(payload: Dict[str, Any]) -> Path:
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

    codex_home = os.environ.get("CODEX_HOME")
    if not codex_home:
        codex_home = str(Path.home() / ".codex")
    return Path(codex_home).expanduser()


def _resolve_output_path(payload: Dict[str, Any]) -> Path:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _resolve_codex_home(payload) / "orchestrator-observability" / "events" / f"{day}.jsonl"


def _build_event(payload: Dict[str, Any], event_name: str, status: str, reason: str = "", extra: Optional[Dict[str, Any]] = None, text_source: Optional[Any] = None) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "event": "orchestrator_observability",
        "event_name": event_name,
        "status": status,
        "source": "codex-orchestrator-kit",
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "session_id": _detect_session_id(payload.get("transcript_path")) if isinstance(payload.get("transcript_path"), str) else None,
        "transcript_path": payload.get("transcript_path") if isinstance(payload.get("transcript_path"), str) else None,
        "cwd": payload.get("cwd") if isinstance(payload.get("cwd"), str) else None,
        "hook": payload.get("hook") or "orchestrator",
        "script": payload.get("script") or __name__,
        "reason": reason[:400],
        "payload_size": len(json.dumps(payload, ensure_ascii=False, default=str)),
    }
    if extra:
        event.update(extra)

    if captured := _capture_text(text_source):
        event["captured_text"] = captured
    return event


def emit_observability_event(
    payload: Dict[str, Any],
    event_name: str,
    status: str,
    reason: str = "",
    extra: Optional[Dict[str, Any]] = None,
    text_source: Optional[Any] = None,
    **kwargs: Any,
) -> None:
    output_path = _resolve_output_path(payload)
    event = _build_event(payload, event_name, status, reason, extra, text_source)
    if kwargs:
        event.update(kwargs)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass
