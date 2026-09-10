"""Session fact extraction for orchestrator observability reports."""

from __future__ import annotations

import hashlib
import json
import calendar
from dataclasses import dataclass
from datetime import datetime, time, timezone
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

ACCEPTANCE_FIELDS = ("verification_status", "review_status", "subagent_status", "risk_status")
ACCEPTANCE_FIELD_RE = re.compile(
    r'(?im)(?:^|[,\n;])\s*"?(?P<field>verification_status|review_status|subagent_status|risk_status)"?\s*[:=：]\s*(?P<value>[^,\n;\}\]]+)'
)

WORK_DONE_RE = [re.compile(pattern, re.IGNORECASE) for pattern in WORK_DONE_PATTERNS]
VERIFICATION_RE = [re.compile(pattern, re.IGNORECASE) for pattern in VERIFICATION_PATTERNS]
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
FILE_TIMESTAMP_PATTERNS = [
    re.compile(r"\b(?P<ts>\d{4}-\d{2}-\d{2}T\d{6})\b"),
    re.compile(r"\b(?P<ts>\d{4}_\d{2}_\d{2}T\d{6})\b"),
    re.compile(r"\b(?P<ts>\d{8}T\d{6})\b"),
    re.compile(r"\b(?P<ts>\d{4}-\d{2}-\d{2})\b"),
    re.compile(r"\b(?P<ts>\d{4}_\d{2}_\d{2})\b"),
    re.compile(r"\b(?P<ts>\d{8})\b"),
]

_NORMALIZED_TOOL_NAMES = {
    "bash": "bash",
    "sh": "bash",
    "zsh": "bash",
    "shell": "bash",
    "shell_command": "shell_command",
    "powershell": "powershell",
    "pwsh": "powershell",
    "command": "powershell",
    "cmd": "powershell",
    "spawn_agent": "spawn_agent",
    "wait_agent": "wait_agent",
    "close_agent": "close_agent",
    "followup_task": "followup_task",
    "send_message": "send_message",
}


def parse_timestamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def iter_jsonl_records(paths: Iterable[Path]) -> Iterator[tuple[Path, int, Optional[object]]]:
    for path in paths:
        try:
            raw_text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            yield path, 0, None
            continue

        decoder = json.JSONDecoder()
        cursor = 0
        text_len = len(raw_text)
        line_no = 1
        while cursor < text_len:
            while cursor < text_len and raw_text[cursor].isspace():
                if raw_text[cursor] == "\n":
                    line_no += 1
                cursor += 1
            if cursor >= text_len:
                break

            record_line = line_no
            try:
                record, end = decoder.raw_decode(raw_text, cursor)
                yield path, record_line, record
                line_no += raw_text[cursor:end].count("\n")
                cursor = end
            except json.JSONDecodeError:
                yield path, record_line, None
                next_newline = raw_text.find("\n", cursor)
                if next_newline == -1:
                    break
                line_no += raw_text[cursor:next_newline + 1].count("\n")
                cursor = next_newline + 1


def _record_fingerprint(record: dict) -> str:
    normalized = dict(record)
    normalized.pop("session_id", None)
    payload = normalized.get("payload")
    if isinstance(payload, dict):
        payload = dict(payload)
        payload.pop("session_id", None)
        normalized["payload"] = payload
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _end_of_day(year: int, month: int, day: int) -> Optional[datetime]:
    try:
        return datetime(year, month, day, time.max.hour, time.max.minute, time.max.second, time.max.microsecond, tzinfo=timezone.utc)
    except ValueError:
        return None


def _end_of_month(year: int, month: int) -> Optional[datetime]:
    try:
        last_day = calendar.monthrange(year, month)[1]
    except calendar.IllegalMonthError:
        return None
    return _end_of_day(year, month, last_day)


def _end_of_year(year: int) -> Optional[datetime]:
    return _end_of_day(year, 12, 31)


def _is_fixed_width_number(value: str, width: int) -> bool:
    return len(value) == width and value.isdigit()


def _iter_jsonl_recursive(path: Path) -> Iterator[Path]:
    if path.is_file() and path.suffix == ".jsonl":
        yield path
    elif path.is_dir():
        yield from path.glob("**/*.jsonl")


def _iter_cutoff_session_files(sessions_root: Path, cutoff: datetime) -> Iterator[Path]:
    for year_entry in sessions_root.iterdir():
        if year_entry.is_file():
            if year_entry.suffix == ".jsonl":
                yield year_entry
            continue
        if not year_entry.is_dir():
            continue

        year_name = year_entry.name
        if not _is_fixed_width_number(year_name, 4):
            yield from _iter_jsonl_recursive(year_entry)
            continue
        year = int(year_name)
        year_end = _end_of_year(year)
        if year_end is not None and year_end < cutoff:
            continue

        for month_entry in year_entry.iterdir():
            if month_entry.is_file():
                if month_entry.suffix == ".jsonl":
                    yield month_entry
                continue
            if not month_entry.is_dir():
                continue

            month_name = month_entry.name
            if not _is_fixed_width_number(month_name, 2):
                yield from _iter_jsonl_recursive(month_entry)
                continue
            month = int(month_name)
            month_end = _end_of_month(year, month)
            if month_end is not None and month_end < cutoff:
                continue

            for day_entry in month_entry.iterdir():
                if day_entry.is_file():
                    if day_entry.suffix == ".jsonl":
                        yield day_entry
                    continue
                if not day_entry.is_dir():
                    continue

                day_name = day_entry.name
                if not _is_fixed_width_number(day_name, 2):
                    yield from _iter_jsonl_recursive(day_entry)
                    continue
                day = int(day_name)
                day_end = _end_of_day(year, month, day)
                if day_end is not None and day_end < cutoff:
                    continue
                yield from _iter_jsonl_recursive(day_entry)


def scan_session_files(codex_home: Path, cutoff: Optional[datetime] = None) -> Iterable[Path]:
    sessions_root = codex_home / "sessions"
    if not sessions_root.exists():
        return []
    if cutoff is not None:
        return _iter_cutoff_session_files(sessions_root, cutoff)
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
        if name in {"spawn_agent", "wait_agent", "close_agent", "followup_task", "send_message"}:
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


def extract_token_usage(payload: dict) -> Dict[str, int]:
    info = payload.get("info")
    if not isinstance(info, dict):
        info = payload
    usage = info.get("total_token_usage")
    if not isinstance(usage, dict):
        usage = info.get("last_token_usage")
    if not isinstance(usage, dict):
        total = extract_token_count(payload)
        return {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
            "total_tokens": total,
        }
    return {
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "cached_input_tokens": int(usage.get("cached_input_tokens", 0) or 0),
        "cache_write_input_tokens": int(usage.get("cache_write_input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "reasoning_output_tokens": int(usage.get("reasoning_output_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }


def _contains_pattern(patterns: List[re.Pattern], text: str) -> bool:
    return any(regex.search(text) for regex in patterns)


def normalize_tool_name(tool_name: object) -> str:
    raw = str(tool_name).strip().lower() if tool_name is not None else ""
    if not raw:
        return ""
    return _NORMALIZED_TOOL_NAMES.get(raw, raw)


def normalize_tool_family(tool_name: str) -> str:
    normalized = normalize_tool_name(tool_name)
    if normalized in {"bash", "powershell", "shell_command"}:
        return "shell_like"
    if normalized in {"spawn_agent", "wait_agent", "close_agent", "followup_task", "send_message"}:
        return "subagent"
    return "generic"


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _acceptance_status_positive(field: str, value: object) -> bool:
    normalized = str(value or "").strip().lower()
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


def _has_short_note(value: object) -> bool:
    return len(str(value or "").strip()) >= 12


def _risk_status_low(value: object) -> bool:
    normalized = str(value or "").strip().lower()
    if any(bad in normalized for bad in ("medium", "moderate", "high", "critical", "中", "高")):
        return False
    return any(token in normalized for token in LOW_RISK_TOKENS)


def _verification_exception_acceptable(value: object, risk_value: object) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized or not _has_short_note(value):
        return False
    if not any(token in normalized for token in VERIFICATION_EXCEPTION_TOKENS):
        return False
    if "partial" in normalized or "部分" in normalized:
        return not _risk_status_low(risk_value)
    return True


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
            "assistant_has_acceptance_footer": False,
            "assistant_acceptance_footer_complete": False,
            "assistant_acceptance_footer_field_count": 0,
        }

    content_lower = content.lower()
    footer = parse_acceptance_footer(content)
    assistant_work_done = any(regex.search(content) for regex in WORK_DONE_RE)
    assistant_has_verification = any(regex.search(content) for regex in VERIFICATION_RE) or bool(footer.get("verification_status"))
    assistant_has_subagent_status = (
        "subagents: closed" in content_lower or "subagents: retained because" in content_lower or bool(footer.get("subagent_status"))
    )
    return {
        "assistant_work_done": assistant_work_done,
        "assistant_has_verification": assistant_has_verification,
        "assistant_has_subagent_status": assistant_has_subagent_status,
        "assistant_has_acceptance_footer": bool(footer),
        "assistant_acceptance_footer_complete": len(footer) == len(ACCEPTANCE_FIELDS),
        "assistant_acceptance_footer_field_count": len(footer),
    }


def parse_acceptance_footer(message: str) -> Dict[str, str]:
    if not message:
        return {}
    footer = message[-2200:]
    values: Dict[str, str] = {}
    for match in ACCEPTANCE_FIELD_RE.finditer(footer):
        key = match.group("field")
        value = match.group("value").strip().strip('\'"`')
        values[key] = value
    return values


def _parse_token_timestamp(token: str) -> Optional[datetime]:
    formats = (
        ("%Y%m%dT%H%M%S", False),
        ("%Y-%m-%dT%H%M%S", False),
        ("%Y_%m_%dT%H%M%S", False),
        ("%Y%m%d", True),
        ("%Y-%m-%d", True),
        ("%Y_%m_%d", True),
    )
    for fmt, date_only in formats:
        try:
            parsed = datetime.strptime(token, fmt)
        except ValueError:
            continue
        if date_only:
            parsed = datetime.combine(parsed.date(), time.max)
        return parsed.replace(tzinfo=timezone.utc)
    return None


def _path_timestamp_hints(path: Path) -> List[datetime]:
    hints: List[datetime] = []
    search_targets = [path.name, str(path)]
    for target in search_targets:
        for pattern in FILE_TIMESTAMP_PATTERNS:
            for match in pattern.finditer(target):
                parsed = _parse_token_timestamp(match.group("ts"))
                if parsed is not None:
                    hints.append(parsed)

    parts = list(path.parts)
    for index in range(len(parts) - 2):
        year, month, day = parts[index:index + 3]
        if year.isdigit() and month.isdigit() and day.isdigit() and len(year) == 4 and len(month) == 2 and len(day) == 2:
            parsed = _parse_token_timestamp(f"{year}-{month}-{day}")
            if parsed is not None:
                hints.append(parsed)
    return hints


def _parse_path_timestamp_hint(path: Path) -> Optional[datetime]:
    hints = _path_timestamp_hints(path)
    if not hints:
        return None
    return max(hints)


def _file_likely_outside_cutoff(path: Path, cutoff: datetime) -> bool:
    hints = []
    hint_ts = _parse_path_timestamp_hint(path)
    if hint_ts is not None:
        hints.append(hint_ts)
    try:
        hints.append(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))
    except OSError:
        pass

    if not hints:
        return False
    latest_hint = max(hints)
    return latest_hint < cutoff


def _filtered_session_paths(paths: Iterable[Path], cutoff: datetime, stats: Optional[Dict[str, int]] = None) -> Iterator[Path]:
    for path in paths:
        if _file_likely_outside_cutoff(path, cutoff):
            if stats is not None:
                stats["filtered_files"] = stats.get("filtered_files", 0) + 1
            continue
        yield path


@dataclass(frozen=True)
class SessionFact:
    timestamp: Optional[str]
    session_id: str
    cwd: str
    event_type: str
    role: str
    reason: str
    tool_name: str
    normalized_tool_name: str
    tool_family: str
    status: str
    input_length: int
    output_length: int
    token_count: int
    token_input_tokens: int = 0
    token_cached_input_tokens: int = 0
    token_cache_write_input_tokens: int = 0
    token_output_tokens: int = 0
    token_reasoning_output_tokens: int = 0
    source_file: str = ""
    source_line: int = 0
    assistant_work_done: bool = False
    assistant_has_verification: bool = False
    assistant_has_subagent_status: bool = False
    assistant_has_acceptance_footer: bool = False
    assistant_acceptance_footer_complete: bool = False
    assistant_acceptance_footer_field_count: int = 0
    acceptance_source: str = ""
    verification_status: str = ""
    review_status: str = ""
    subagent_status: str = ""
    risk_status: str = ""
    acceptance_has_work_done_signal: bool = False
    acceptance_has_verification_signal: bool = False
    acceptance_has_subagent_signal: bool = False
    acceptance_is_structured_complete: bool = False
    completion_state: Dict[str, object] | None = None
    acceptance_footer_verification_fail: bool = False
    acceptance_footer_review_fail: bool = False
    acceptance_footer_subagent_fail: bool = False
    acceptance_footer_risk_fail: bool = False


def extract_facts(paths: Iterable[Path], codex_home: Path, cutoff: datetime) -> tuple[List[SessionFact], Dict[str, int]]:
    stats = {
        "invalid_lines": 0,
        "filtered_older": 0,
        "filtered_files": 0,
        "raw_records": 0,
        "fork_history_records_skipped": 0,
        "duplicate_records_skipped": 0,
    }
    facts: List[SessionFact] = []
    session_context: Dict[str, Dict[str, str]] = {}
    session_started_at: Dict[str, datetime] = {}
    seen_session_records: set[str] = set()
    filtered_paths = sorted(_filtered_session_paths(paths, cutoff, stats), key=lambda item: str(item))
    for source_file, source_line, record in iter_jsonl_records(filtered_paths):
        stats["raw_records"] += 1
        source_key = str(source_file.resolve())
        if record is None:
            stats["invalid_lines"] += 1
            continue
        if not isinstance(record, dict):
            stats["non_dict_records"] = stats.get("non_dict_records", 0) + 1
            continue

        ts = parse_timestamp(record.get("created_at") or record.get("event_time") or record.get("timestamp"))
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
            if ts is not None:
                session_started_at[source_key] = ts

        if event_type != "transcript_session_meta" and is_session_file(codex_home, source_file):
            started_at = session_started_at.get(source_key)
            if ts is not None and started_at is not None and ts < started_at:
                stats["fork_history_records_skipped"] += 1
                continue
            fingerprint = _record_fingerprint(record)
            if fingerprint in seen_session_records:
                stats["duplicate_records_skipped"] += 1
                continue
            seen_session_records.add(fingerprint)

        if ts is not None and ts < cutoff:
            stats["filtered_older"] += 1
            continue

        context = session_context.get(source_key, {})
        status = str(record.get("status", "unknown")).lower()
        role = str(payload.get("role", ""))
        tool_name = str(payload.get("name") or payload.get("tool_name") or "")
        normalized_tool_name = normalize_tool_name(tool_name)
        tool_family = normalize_tool_family(normalized_tool_name)
        token_count = 0
        token_usage = {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
            "total_tokens": 0,
        }
        input_length = 0
        output_length = 0
        event_reason = str(record.get("reason", ""))

        if event_type == "transcript_token_count":
            token_count = extract_token_count(payload)
            token_usage = extract_token_usage(payload)
        elif event_type in {"transcript_function_call", "transcript_custom_tool_call"}:
            normalized_tool_name = normalize_tool_name(payload.get("name") or tool_name)
            tool_family = normalize_tool_family(normalized_tool_name)
            input_length = normalize_text_length(payload.get("arguments") or payload.get("input"))
            if not input_length:
                input_length = normalize_text_length(record.get("input"))
        elif event_type in {"transcript_function_call_output", "transcript_custom_tool_output"}:
            output_length = normalize_text_length(payload.get("output") or payload.get("result") or payload.get("content"))
        elif event_type.startswith("transcript_message_"):
            input_length = normalize_text_length(stringify_text(payload.get("content") or payload.get("message")))
        elif event_type.startswith("orchestrator_"):
            input_length = int(record.get("input_length", 0) or 0)
            if not input_length:
                input_length = normalize_text_length(record.get("reason"))
            output_length = int(record.get("output_length", 0) or 0)
            if not output_length:
                output_length = int(record.get("payload_size", 0) or 0)
            if record.get("tool_name"):
                tool_name = str(record.get("tool_name") or "")
                normalized_tool_name = normalize_tool_name(tool_name)
                tool_family = normalize_tool_family(normalized_tool_name)

            extra = record.get("extra")
            if not isinstance(extra, dict):
                extra = {}
            if not tool_name and not payload.get("tool_name"):
                potential_tool = str(extra.get("tool_name", ""))
                if potential_tool:
                    tool_name = potential_tool
                    normalized_tool_name = normalize_tool_name(potential_tool)
                    tool_family = normalize_tool_family(normalized_tool_name)

        flags = _assistant_signal_flags(payload) if event_type == "transcript_message_assistant" else {
            "assistant_work_done": False,
            "assistant_has_verification": False,
            "assistant_has_subagent_status": False,
            "assistant_has_acceptance_footer": False,
            "assistant_acceptance_footer_complete": False,
            "assistant_acceptance_footer_field_count": 0,
        }
        acceptance = {}
        if isinstance(payload.get("acceptance"), dict):
            acceptance.update({str(k): v for k, v in payload["acceptance"].items() if isinstance(k, str)})
        if isinstance(record.get("acceptance"), dict):
            acceptance.update({str(k): v for k, v in record["acceptance"].items() if isinstance(k, str)})
        if isinstance(record.get("acceptance_source"), str):
            acceptance["source"] = record["acceptance_source"]
        for key in ("verification_status", "review_status", "subagent_status", "risk_status"):
            if isinstance(record.get(key), str):
                acceptance[key] = record[key]
        for key in ("has_work_done_signal", "has_verification_signal", "has_subagent_signal"):
            if key in record:
                acceptance[key] = record[key]
        if event_type.startswith("orchestrator_") and isinstance(record.get("extra"), dict):
            acceptance.update({str(k): v for k, v in record["extra"].items() if isinstance(k, str) and k.startswith("has_")})

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
        acceptance_source = str(acceptance.get("source") or "")
        verification_status = str(acceptance.get("verification_status") or "")
        review_status = str(acceptance.get("review_status") or "")
        subagent_status = str(acceptance.get("subagent_status") or "")
        risk_status = str(acceptance.get("risk_status") or "")
        structured_complete = (
            acceptance_source == "structured"
            and all(acceptance.get(field) for field in ACCEPTANCE_FIELDS)
        )
        completion_state = None
        if isinstance(record.get("completion_state"), dict):
            completion_state = {str(k): v for k, v in record["completion_state"].items() if isinstance(k, str)}
        elif isinstance(record.get("extra"), dict) and isinstance(record["extra"].get("completion_state"), dict):
            completion_state = {
                str(k): v for k, v in record["extra"]["completion_state"].items() if isinstance(k, str)
            }
        elif isinstance(acceptance.get("completion_state"), dict):
            completion_state = {str(k): v for k, v in acceptance["completion_state"].items() if isinstance(k, str)}

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
                token_input_tokens=token_usage["input_tokens"],
                token_cached_input_tokens=token_usage["cached_input_tokens"],
                token_cache_write_input_tokens=token_usage["cache_write_input_tokens"],
                token_output_tokens=token_usage["output_tokens"],
                token_reasoning_output_tokens=token_usage["reasoning_output_tokens"],
                reason=event_reason,
                normalized_tool_name=normalized_tool_name,
                tool_family=tool_family,
                source_file=extract_source_file(codex_home, source_file),
                source_line=source_line,
                assistant_work_done=flags["assistant_work_done"],
                assistant_has_verification=flags["assistant_has_verification"],
                assistant_has_subagent_status=flags["assistant_has_subagent_status"],
                assistant_has_acceptance_footer=flags["assistant_has_acceptance_footer"],
                assistant_acceptance_footer_complete=flags["assistant_acceptance_footer_complete"],
                assistant_acceptance_footer_field_count=int(flags["assistant_acceptance_footer_field_count"]),
                acceptance_source=acceptance_source,
                verification_status=verification_status,
                review_status=review_status,
                subagent_status=subagent_status,
                risk_status=risk_status,
                acceptance_has_work_done_signal=_to_bool(acceptance.get("has_work_done_signal")),
                acceptance_has_verification_signal=_to_bool(acceptance.get("has_verification_signal")),
                acceptance_has_subagent_signal=_to_bool(acceptance.get("has_subagent_signal")),
                acceptance_is_structured_complete=structured_complete,
                completion_state=completion_state,
                acceptance_footer_verification_fail=structured_complete and not (
                    _acceptance_status_positive("verification_status", verification_status)
                    or _verification_exception_acceptable(verification_status, risk_status)
                ),
                acceptance_footer_review_fail=structured_complete and not _acceptance_status_positive("review_status", review_status),
                acceptance_footer_subagent_fail=structured_complete and not _acceptance_status_positive("subagent_status", subagent_status),
                acceptance_footer_risk_fail=structured_complete and not _acceptance_status_positive("risk_status", risk_status),
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
        "reason": fact.reason,
        "normalized_tool_name": fact.normalized_tool_name,
        "tool_family": fact.tool_family,
        "tool_name": fact.tool_name,
        "status": fact.status,
        "input_length": fact.input_length,
        "output_length": fact.output_length,
        "token_count": fact.token_count,
        "token_usage": {
            "input_tokens": fact.token_input_tokens,
            "cached_input_tokens": fact.token_cached_input_tokens,
            "cache_write_input_tokens": fact.token_cache_write_input_tokens,
            "output_tokens": fact.token_output_tokens,
            "reasoning_output_tokens": fact.token_reasoning_output_tokens,
            "total_tokens": fact.token_count,
        },
        "source_file": fact.source_file,
        "source_line": fact.source_line,
        "assistant_work_done": fact.assistant_work_done,
        "assistant_has_verification": fact.assistant_has_verification,
        "assistant_has_subagent_status": fact.assistant_has_subagent_status,
        "assistant_has_acceptance_footer": fact.assistant_has_acceptance_footer,
        "assistant_acceptance_footer_complete": fact.assistant_acceptance_footer_complete,
        "assistant_acceptance_footer_field_count": fact.assistant_acceptance_footer_field_count,
        "acceptance": {
            "source": fact.acceptance_source,
            "verification_status": fact.verification_status,
            "review_status": fact.review_status,
            "subagent_status": fact.subagent_status,
            "risk_status": fact.risk_status,
            "has_work_done_signal": fact.acceptance_has_work_done_signal,
            "has_verification_signal": fact.acceptance_has_verification_signal,
            "has_subagent_signal": fact.acceptance_has_subagent_signal,
            "is_structured_complete": fact.acceptance_is_structured_complete,
            "completion_state": fact.completion_state or {},
            "footer_verification_fail": fact.acceptance_footer_verification_fail,
            "footer_review_fail": fact.acceptance_footer_review_fail,
            "footer_subagent_fail": fact.acceptance_footer_subagent_fail,
            "footer_risk_fail": fact.acceptance_footer_risk_fail,
        },
        "tool_surface": {
            "normalized_tool_name": fact.normalized_tool_name,
            "tool_family": fact.tool_family,
            "input_length": fact.input_length,
            "output_length": fact.output_length,
        },
    }
