"""Session fact extraction for orchestrator observability reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional
import re


WORK_DONE_PATTERNS = [
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

VERIFICATION_PATTERNS = [
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


WORK_DONE_RE = [re.compile(pattern, re.IGNORECASE) for pattern in WORK_DONE_PATTERNS]
VERIFICATION_RE = [re.compile(pattern, re.IGNORECASE) for pattern in VERIFICATION_PATTERNS]


def parse_timestamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def iter_jsonl_records(paths: Iterable[Path]) -> Iterator[tuple[Path, int, Optional[dict]]]:
    for path in paths:
        try:
            raw_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            yield path, 0, None
            continue

        for line_no, raw in enumerate(raw_lines, 1):
            if not raw.strip():
                continue
            try:
                yield path, line_no, json.loads(raw)
            except Exception:
                yield path, line_no, None


def scan_session_files(codex_home: Path) -> Iterable[Path]:
    sessions_root = codex_home / "sessions"
    if not sessions_root.exists():
        return []
    return sessions_root.glob("**/*.jsonl")


def scan_observability_event_files(codex_home: Path) -> Iterable[Path]:
    events_root = codex_home / "orchestrator-observability" / "events"
    if not events_root.exists():
        return []
    return events_root.glob("*.jsonl")


def classify_event(record: dict) -> str:
    if record.get("type") == "session_meta":
        return "transcript_session_meta"

    if record.get("event") == "orchestrator_observability":
        event_name = str(record.get("event_name") or "orchestrator_unknown")
        if not event_name.startswith("orchestrator_"):
            event_name = f"orchestrator_{event_name}"
        return event_name

    payload = record.get("payload")
    if not isinstance(payload, dict):
        return "other"

    payload_type = payload.get("type")
    if payload_type == "function_call":
        name = str(payload.get("name") or "unknown")
        if name in {"spawn_agent", "wait_agent", "close_agent"}:
            return f"transcript_subagent_{name}"
        return "transcript_function_call"
    if payload_type == "function_call_output":
        return "transcript_function_call_output"
    if payload_type == "custom_tool_call":
        return "transcript_custom_tool_call"
    if payload_type == "custom_tool_call_output":
        return "transcript_custom_tool_output"
    if payload_type == "token_count":
        return "transcript_token_count"
    if payload_type == "reasoning":
        return "transcript_reasoning"
    if payload_type == "agent_message":
        return "transcript_agent_message"
    if isinstance(payload, dict) and payload.get("type") == "message":
        role = payload.get("role", "unknown")
        return f"transcript_message_{role}"
    if isinstance(payload, dict) and payload.get("type") in {"tool", "tool_use"}:
        return "transcript_tool"
    return "other"


def normalize_text_length(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return len(str(value))


def stringify_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            else:
                parts.append(stringify_text(item))
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if isinstance(value.get("message"), str):
            return value["message"]
        return "\n".join(stringify_text(v) for v in value.values())
    return str(value)


def extract_token_count(payload: dict) -> int:
    info = payload.get("info")
    if not isinstance(info, dict):
        info = payload

    for key in ("total_token_count", "total_tokens"):
        value = info.get(key)
        if isinstance(value, int):
            return value

    total_usage = info.get("total_token_usage")
    if isinstance(total_usage, dict) and isinstance(total_usage.get("total_tokens"), int):
        return total_usage["total_tokens"]

    last_usage = info.get("last_token_usage")
    if isinstance(last_usage, dict) and isinstance(last_usage.get("total_tokens"), int):
        return last_usage["total_tokens"]

    return 0


def _contains_pattern(patterns: List[re.Pattern], text: str) -> bool:
    return any(regex.search(text) for regex in patterns)


def extract_source_file(codex_home: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(codex_home.resolve())).replace("\\", "/")
    except Exception:
        return path.name


def is_session_file(codex_home: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to((codex_home / "sessions").resolve())
        return True
    except Exception:
        return False


def _assistant_signal_flags(payload: dict) -> Dict[str, bool]:
    content = stringify_text(payload.get("content") or payload.get("message"))
    if not content:
        return {
            "assistant_work_done": False,
            "assistant_has_verification": False,
            "assistant_has_subagent_status": False,
        }

    content_lower = content.lower()
    assistant_work_done = any(regex.search(content) for regex in WORK_DONE_RE)
    assistant_has_verification = any(regex.search(content) for regex in VERIFICATION_RE)
    assistant_has_subagent_status = (
        "subagents: closed" in content_lower or "subagents: retained because" in content_lower
    )
    return {
        "assistant_work_done": assistant_work_done,
        "assistant_has_verification": assistant_has_verification,
        "assistant_has_subagent_status": assistant_has_subagent_status,
    }


@dataclass(frozen=True)
class SessionFact:
    timestamp: Optional[str]
    session_id: str
    cwd: str
    event_type: str
    role: str
    tool_name: str
    status: str
    input_length: int
    output_length: int
    token_count: int
    source_file: str
    source_line: int
    assistant_work_done: bool = False
    assistant_has_verification: bool = False
    assistant_has_subagent_status: bool = False


def extract_facts(paths: Iterable[Path], codex_home: Path, cutoff: datetime) -> tuple[List[SessionFact], Dict[str, int]]:
    stats = {"invalid_lines": 0, "filtered_older": 0}
    facts: List[SessionFact] = []
    session_context: Dict[str, Dict[str, str]] = {}
    for source_file, source_line, record in iter_jsonl_records(paths):
        source_key = str(source_file.resolve())
        if record is None:
            stats["invalid_lines"] += 1
            continue

        ts = parse_timestamp(record.get("created_at") or record.get("event_time") or record.get("timestamp"))
        if ts is not None and ts < cutoff:
            stats["filtered_older"] += 1
            continue

        event_type = classify_event(record)
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if not isinstance(payload, dict):
            payload = {}

        if event_type == "transcript_session_meta":
            context = session_context.setdefault(source_key, {})
            if isinstance(payload.get("id"), str):
                context["session_id"] = payload["id"]
            if isinstance(payload.get("cwd"), str):
                context["cwd"] = payload["cwd"]

        context = session_context.get(source_key, {})
        status = str(record.get("status", "unknown")).lower()
        role = str(payload.get("role", ""))
        tool_name = str(payload.get("name") or payload.get("tool_name") or "")
        token_count = 0
        input_length = 0
        output_length = 0

        if event_type == "transcript_token_count":
            token_count = extract_token_count(payload)
        elif event_type in {"transcript_function_call", "transcript_custom_tool_call"}:
            input_length = normalize_text_length(payload.get("arguments") or payload.get("input"))
            if not input_length:
                input_length = normalize_text_length(record.get("input"))
        elif event_type in {"transcript_function_call_output", "transcript_custom_tool_output"}:
            output_length = normalize_text_length(payload.get("output") or payload.get("result") or payload.get("content"))
        elif event_type.startswith("transcript_message_"):
            input_length = normalize_text_length(stringify_text(payload.get("content") or payload.get("message")))

        flags = _assistant_signal_flags(payload) if event_type == "transcript_message_assistant" else {
            "assistant_work_done": False,
            "assistant_has_verification": False,
            "assistant_has_subagent_status": False,
        }
        event_ts = None
        if ts:
            event_ts = ts.isoformat().replace("+00:00", "Z")
        else:
            raw_ts = record.get("created_at") or record.get("event_time") or record.get("timestamp")
            event_ts = str(raw_ts) if raw_ts is not None else None

        cwd = str(record.get("cwd") or payload.get("cwd") or context.get("cwd") or "unknown")
        session_id_value = (
            record.get("session_id")
            or payload.get("id")
            or context.get("session_id")
            or (source_file.stem if is_session_file(codex_home, source_file) else "")
        )
        session_id = str(session_id_value or "")

        facts.append(
            SessionFact(
                timestamp=event_ts,
                session_id=session_id,
                cwd=cwd,
                event_type=event_type,
                role=role,
                tool_name=tool_name,
                status=status,
                input_length=input_length,
                output_length=output_length,
                token_count=token_count,
                source_file=extract_source_file(codex_home, source_file),
                source_line=source_line,
                assistant_work_done=flags["assistant_work_done"],
                assistant_has_verification=flags["assistant_has_verification"],
                assistant_has_subagent_status=flags["assistant_has_subagent_status"],
            )
        )

    return facts, stats


def fact_to_dict(fact: SessionFact) -> Dict[str, object]:
    return {
        "timestamp": fact.timestamp,
        "session_id": fact.session_id,
        "cwd": fact.cwd,
        "event_type": fact.event_type,
        "role": fact.role,
        "tool_name": fact.tool_name,
        "status": fact.status,
        "input_length": fact.input_length,
        "output_length": fact.output_length,
        "token_count": fact.token_count,
        "source_file": fact.source_file,
        "source_line": fact.source_line,
        "assistant_work_done": fact.assistant_work_done,
        "assistant_has_verification": fact.assistant_has_verification,
        "assistant_has_subagent_status": fact.assistant_has_subagent_status,
    }
