#!/usr/bin/env python3
"""Generate value-oriented subagent workflow reviews from Codex session JSONL.

This reviewer complements subagent_workflow.py.  The older report answers
"did the lifecycle discipline fire"; this report tries to reconstruct
delegation episodes and judge whether each subagent produced net value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from session_facts import (
    iter_jsonl_records,
    scan_session_files,
    scan_observability_event_files,
    stringify_text,
)


SCHEMA_VERSION = "subagent-value-review-v2"
MAX_SUMMARY_CHARS = 500
MAX_PACKET_TEXT = 6000
POST_WAIT_EVENT_WINDOW = 14
LARGE_OUTPUT_CHARS = 60000
VERBOSE_RESULT_CHARS = 12000
COMPACT_RESULT_CHARS = 4000

SUBAGENT_TOOLS = {"spawn_agent", "wait_agent", "close_agent"}
SHELL_TOOLS = {"bash", "zsh", "sh", "shell", "shell_command", "exec_command", "powershell", "cmd"}

BROAD_SCAN_RE = re.compile(
    r"(?is)(\brg\s+--files\b|\bfind\s+\.\b|\bgit\s+grep\b|\bls\b.*\s-r\b|\btree\b|\bgrep\b.*\s-r\b)"
)
PATH_RE = re.compile(r"\b(?:[\w.-]+/)+[\w.-]+\b")
IDENT_RE = re.compile(r"\b(?:session|trace|request|message|agent|call|id)[_-]?[a-z]*[:=]?[A-Za-z0-9_.:-]{4,}\b", re.I)

GOAL_TERMS = ("goal", "task", "目标", "任务", "结论", "answer", "find", "inspect", "review", "implement")
SCOPE_TERMS = ("scope", "only", "limit", "不要", "只", "边界", "范围", "owned", "read-only", "只读")
EVIDENCE_TERMS = ("evidence", "file", "line", "依据", "证据", "路径", "line", "risk", "风险")
OUTPUT_TERMS = ("output", "return", "format", "输出", "返回", "结构", "json", "markdown", "concise", "简洁")
DONE_TERMS = ("done", "complete", "acceptance", "验收", "完成", "conclusion", "结论")
RESULT_GOOD_TERMS = ("结论", "evidence", "依据", "risk", "风险", "missing", "未找到", "recommend", "建议", "file", "line")


@dataclass
class Event:
    session_id: str
    cwd: str
    timestamp: str
    source_file: str
    source_line: int
    kind: str
    role: str = ""
    tool_name: str = ""
    call_id: str = ""
    text: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    output_length: int = 0

    @property
    def ref(self) -> str:
        return f"{self.source_file}:{self.source_line}"


@dataclass
class Episode:
    delegation_id: str
    session_id: str
    cwd: str
    role: str
    spawn_ref: str
    spawn_ts: str
    spawn_call_id: str
    brief: str
    brief_chars: int
    task_context: str = ""
    agent_id: str = ""
    wait_refs: list[str] = field(default_factory=list)
    result_refs: list[str] = field(default_factory=list)
    result_summary: str = ""
    result_chars: int = 0
    close_ref: str = ""
    close_reason: str = ""
    consumed: bool = False
    consumed_refs: list[str] = field(default_factory=list)
    post_wait_tool_names: list[str] = field(default_factory=list)
    duplicate_signals: list[str] = field(default_factory=list)
    failure_signals: list[str] = field(default_factory=list)
    scores: dict[str, int] = field(default_factory=dict)
    raw_scores: dict[str, int] = field(default_factory=dict)
    confidence_penalties: list[dict[str, Any]] = field(default_factory=list)
    final_score: int = 3
    classification: str = "ineffective_use"
    classification_reason: str = ""
    rationale: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate value-oriented subagent workflow reports.")
    parser.add_argument("--codex-home", default=str(Path.home() / ".codex"), help="Path to CODEX_HOME")
    parser.add_argument("--since-days", type=int, default=7, help="Window in days for event filtering")
    parser.add_argument(
        "--since-ts",
        default=None,
        help="Exact window start timestamp. Example: 2026-05-20T00:00:00+08:00. Overrides --since-days.",
    )
    parser.add_argument("--report-id", default=None, help="Optional report filename suffix")
    parser.add_argument("--reports-dir", default=None, help="Optional reports directory")
    parser.add_argument("--include-paths", action="store_true", help="Include full paths in reports")
    parser.add_argument("--max-packets", type=int, default=20, help="Maximum episode packets in the report")
    return parser.parse_args()


def parse_timestamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_since_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = parse_timestamp(value)
    if parsed is None:
        raise ValueError(f"invalid --since-ts value: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_arguments(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"text": value}
        except json.JSONDecodeError:
            return {"text": value}
    return {}


def normalize_tool_name(name: object) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    tail = raw.rsplit("__", 1)[-1]
    mapping = {"zsh": "bash", "sh": "bash", "shell": "bash", "cmd": "powershell"}
    return mapping.get(tail, tail)


def summarize_text(text: str, max_chars: int = MAX_SUMMARY_CHARS) -> str:
    clean = " ".join((text or "").replace("\r", " ").replace("\n", " ").split())
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 3] + "..."


def redact_path(path: str, codex_home: Path, include_paths: bool) -> str:
    if include_paths:
        return path
    try:
        p = Path(path)
        return str(p.resolve().relative_to(codex_home.resolve())).replace("\\", "/")
    except Exception:
        return Path(path).name


def source_label(path: Path, codex_home: Path, include_paths: bool) -> str:
    if include_paths:
        return str(path)
    try:
        return str(path.resolve().relative_to(codex_home.resolve())).replace("\\", "/")
    except Exception:
        return path.name


def all_text_from_args(args: dict[str, Any]) -> str:
    return stringify_text(args)


def extract_brief(args: dict[str, Any]) -> str:
    for key in ("message", "task", "prompt", "input", "instructions", "text"):
        value = args.get(key)
        if value:
            return stringify_text(value)
    return all_text_from_args(args)


def extract_targets(args: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("target", "agent_id", "id"):
        if args.get(key):
            values.append(str(args[key]))
    targets = args.get("targets")
    if isinstance(targets, list):
        values.extend(str(item) for item in targets if item)
    elif targets:
        values.append(str(targets))
    return values


def extract_agent_id(output_text: str, fallback: str) -> str:
    try:
        parsed = json.loads(output_text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        for key in ("agent_id", "id", "target", "agent", "target_agent_id"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    match = re.search(r"\b(?:agent_id|target|id)[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_.:-]{3,80})", output_text)
    if match:
        return match.group(1)
    return fallback


def iter_events(codex_home: Path, cutoff: datetime, include_paths: bool = False) -> tuple[list[Event], dict[str, int]]:
    stats = {"invalid_lines": 0, "filtered_older": 0, "filtered_files": 0}
    paths = list(scan_session_files(codex_home, cutoff=cutoff)) + list(scan_observability_event_files(codex_home))
    events: list[Event] = []
    seen_event_keys: set[tuple[str, str, str, str, str, str, str]] = set()
    session_context: dict[str, dict[str, str]] = defaultdict(dict)

    def add_event(event: Event) -> None:
        fingerprint_source = json.dumps(event.arguments, ensure_ascii=False, sort_keys=True) if event.arguments else event.text
        fingerprint = hashlib.sha1(fingerprint_source.encode("utf-8", errors="ignore")).hexdigest()
        timestamp_key = "" if event.kind in {"function_call", "function_call_output"} and event.call_id else event.timestamp
        key = (
            event.session_id,
            timestamp_key,
            event.kind,
            event.role,
            event.tool_name,
            event.call_id,
            fingerprint,
        )
        if key in seen_event_keys:
            stats["deduped_events"] = stats.get("deduped_events", 0) + 1
            return
        seen_event_keys.add(key)
        events.append(event)

    for path, line_no, record in iter_jsonl_records(paths):
        source_key = str(path.resolve())
        if record is None:
            stats["invalid_lines"] += 1
            continue
        if not isinstance(record, dict):
            stats["non_dict_records"] = stats.get("non_dict_records", 0) + 1
            continue
        ts = parse_timestamp(record.get("created_at") or record.get("event_time") or record.get("timestamp"))
        if ts is not None and ts < cutoff:
            stats["filtered_older"] += 1
            continue

        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if record.get("type") == "session_meta":
            if isinstance(payload.get("id"), str):
                session_context[source_key]["session_id"] = payload["id"]
            if isinstance(payload.get("cwd"), str):
                session_context[source_key]["cwd"] = payload["cwd"]

        context = session_context[source_key]
        timestamp = ts.isoformat().replace("+00:00", "Z") if ts else str(record.get("created_at") or record.get("timestamp") or "")
        session_id = str(record.get("session_id") or payload.get("id") or context.get("session_id") or path.stem)
        cwd = str(record.get("cwd") or payload.get("cwd") or context.get("cwd") or "unknown")
        source = source_label(path, codex_home, include_paths)

        if record.get("event") == "orchestrator_observability":
            add_event(
                Event(
                    session_id=session_id,
                    cwd=cwd,
                    timestamp=timestamp,
                    source_file=source,
                    source_line=line_no,
                    kind=f"orchestrator_{record.get('event_name') or 'unknown'}",
                    tool_name=normalize_tool_name(record.get("tool_name")),
                    text=str(record.get("reason") or ""),
                    arguments={str(k): v for k, v in record.items() if k in {"reason", "status", "tool_name", "tool_family"}},
                    output_length=int(record.get("output_length", 0) or record.get("payload_size", 0) or 0),
                )
            )
            continue

        payload_type = payload.get("type")
        if payload_type == "message":
            role = str(payload.get("role") or "")
            add_event(
                Event(
                    session_id=session_id,
                    cwd=cwd,
                    timestamp=timestamp,
                    source_file=source,
                    source_line=line_no,
                    kind=f"message_{role}",
                    role=role,
                    text=stringify_text(payload.get("content") or payload.get("message")),
                )
            )
            continue

        if payload_type in {"function_call", "custom_tool_call"}:
            args = parse_arguments(payload.get("arguments") or payload.get("input"))
            tool = normalize_tool_name(payload.get("name") or payload.get("tool_name"))
            add_event(
                Event(
                    session_id=session_id,
                    cwd=cwd,
                    timestamp=timestamp,
                    source_file=source,
                    source_line=line_no,
                    kind="function_call",
                    tool_name=tool,
                    call_id=str(payload.get("call_id") or payload.get("id") or f"{source}:{line_no}"),
                    arguments=args,
                    text=all_text_from_args(args),
                )
            )
            continue

        if payload_type in {"function_call_output", "custom_tool_call_output"}:
            text = stringify_text(payload.get("output") or payload.get("result") or payload.get("content"))
            add_event(
                Event(
                    session_id=session_id,
                    cwd=cwd,
                    timestamp=timestamp,
                    source_file=source,
                    source_line=line_no,
                    kind="function_call_output",
                    tool_name=normalize_tool_name(payload.get("name") or payload.get("tool_name")),
                    call_id=str(payload.get("call_id") or payload.get("id") or ""),
                    text=text,
                    output_length=len(text),
                )
            )
            continue

        if payload_type == "token_count":
            add_event(
                Event(
                    session_id=session_id,
                    cwd=cwd,
                    timestamp=timestamp,
                    source_file=source,
                    source_line=line_no,
                    kind="token_count",
                    output_length=0,
                    text=json.dumps(payload, ensure_ascii=False),
                )
            )

    events.sort(key=lambda item: (item.timestamp or "", item.source_file, item.source_line))
    return events, stats


def brief_feature_hits(text: str) -> set[str]:
    lower = text.lower()
    hits = set()
    if any(term in lower for term in GOAL_TERMS):
        hits.add("goal")
    if any(term in lower for term in SCOPE_TERMS):
        hits.add("scope")
    if any(term in lower for term in EVIDENCE_TERMS):
        hits.add("evidence")
    if any(term in lower for term in OUTPUT_TERMS):
        hits.add("output_contract")
    if any(term in lower for term in DONE_TERMS):
        hits.add("done_condition")
    return hits


def score_handoff(brief: str) -> int:
    hits = brief_feature_hits(brief)
    if len(brief.strip()) < 40:
        return 1
    return max(1, min(5, 1 + len(hits)))


def score_result(text: str) -> int:
    if not text:
        return 1
    lower = text.lower()
    feature_count = sum(1 for term in RESULT_GOOD_TERMS if term in lower)
    if len(text) > VERBOSE_RESULT_CHARS:
        return 2 if feature_count else 1
    if feature_count >= 4 and len(text) <= COMPACT_RESULT_CHARS:
        return 5
    if feature_count >= 2:
        return 4
    if len(text) <= COMPACT_RESULT_CHARS:
        return 3
    return 2


def score_compression(result_chars: int, result_text: str) -> int:
    if result_chars <= 0:
        return 1
    if result_chars <= 2500 and any(term in result_text.lower() for term in ("evidence", "依据", "结论", "risk", "风险")):
        return 5
    if result_chars <= COMPACT_RESULT_CHARS:
        return 4
    if result_chars <= VERBOSE_RESULT_CHARS:
        return 3
    if result_chars <= LARGE_OUTPUT_CHARS:
        return 2
    return 1


def score_context_savings(episode: Episode) -> int:
    if not episode.result_chars:
        return 1
    if episode.result_chars > VERBOSE_RESULT_CHARS:
        return 1 if episode.duplicate_signals else 2
    if episode.duplicate_signals:
        return 2
    if episode.result_chars <= 2500 and episode.consumed:
        return 5
    if episode.result_chars <= COMPACT_RESULT_CHARS:
        return 4
    return 3


def score_delegation_fit(episode: Episode) -> int:
    text = f"{episode.task_context}\n{episode.brief}".lower()
    complexity = 0
    if any(term in text for term in ("implement", "review", "research", "调研", "实现", "审查", "复杂", "multi", "多个")):
        complexity += 1
    if any(term in text for term in ("test", "验证", "risk", "风险", "architecture", "架构")):
        complexity += 1
    if len(episode.brief) > 300:
        complexity += 1
    return min(5, 3 + complexity)


def extract_keywords(text: str) -> set[str]:
    words = set()
    for match in PATH_RE.finditer(text or ""):
        words.add(match.group(0).lower())
    for match in IDENT_RE.finditer(text or ""):
        words.add(match.group(0).lower())
    for word in re.findall(r"\b[A-Za-z][A-Za-z0-9_-]{5,}\b", text or ""):
        if word.lower() not in {"output", "result", "message", "assistant", "subagent"}:
            words.add(word.lower())
        if len(words) >= 30:
            break
    return words


def infer_consumption(episode: Episode, followup_events: list[Event]) -> None:
    keywords = extract_keywords(episode.result_summary or episode.brief)
    consumed_refs: list[str] = []
    post_tool_names: list[str] = []
    duplicate_signals: list[str] = []

    for event in followup_events:
        if event.kind == "message_assistant":
            lower = event.text.lower()
            explicit = any(token in lower for token in ("subagent", "explorer", "researcher", "reviewer", "worker", "子 agent", "子agent", "委托"))
            keyword_hit = any(keyword and keyword in lower for keyword in list(keywords)[:20])
            if explicit or keyword_hit:
                consumed_refs.append(event.ref)
        elif event.kind == "function_call":
            post_tool_names.append(event.tool_name)
            text = event.text.lower()
            if event.tool_name in SHELL_TOOLS and BROAD_SCAN_RE.search(text):
                duplicate_signals.append(f"main_broad_scan_after_wait:{event.ref}")
            elif any(keyword and keyword in text for keyword in list(keywords)[:10]):
                duplicate_signals.append(f"main_rechecked_subagent_evidence:{event.ref}")

    episode.consumed = bool(consumed_refs)
    episode.consumed_refs = consumed_refs[:5]
    episode.post_wait_tool_names = post_tool_names[:8]
    episode.duplicate_signals = duplicate_signals[:5]


def classify_episode(episode: Episode) -> None:
    result_text = episode.result_summary
    raw_scores = {
        "delegation_fit": score_delegation_fit(episode),
        "handoff_quality": score_handoff(episode.brief),
        "result_quality": score_result(result_text),
        "compression_value": score_compression(episode.result_chars, result_text),
        "decision_impact": 4 if episode.consumed else (2 if episode.result_chars else 1),
        "context_savings": score_context_savings(episode),
        "duplication_cost": 5 if not episode.duplicate_signals else 2,
        "closure_quality": 5 if episode.close_ref else 2,
        "failure_recoverability": 4 if not episode.failure_signals else (3 if episode.post_wait_tool_names else 1),
    }
    raw_scores["net_utility"] = round_int(sum(raw_scores.values()) / len(raw_scores))

    penalties: list[dict[str, Any]] = []
    if not episode.result_refs:
        penalties.append({"reason": "missing_result_event", "delta": -1})
    elif not episode.consumed:
        penalties.append({"reason": "no_consumption_evidence", "delta": -1})
    if not episode.close_ref:
        penalties.append({"reason": "missing_close_event", "delta": -1})
    if episode.result_chars > VERBOSE_RESULT_CHARS:
        penalties.append({"reason": "overlong_result", "delta": -1})
    if episode.duplicate_signals:
        penalties.append({"reason": "duplicate_evidence_recheck", "delta": -1})

    final_score = round_int(raw_scores["net_utility"] + sum(int(item["delta"]) for item in penalties))
    has_observability_gap = not episode.result_refs or not episode.close_ref
    strong_harm = (
        episode.result_chars > VERBOSE_RESULT_CHARS and (not episode.consumed or bool(episode.duplicate_signals))
    ) or (bool(episode.duplicate_signals) and not episode.consumed and episode.result_chars > COMPACT_RESULT_CHARS)

    if final_score >= 4 and episode.consumed and episode.close_ref and episode.result_refs:
        episode.classification = "good_use"
        episode.classification_reason = "委派结果短且可用，主线程有消费证据并已闭环。"
    elif strong_harm:
        episode.classification = "harmful_use"
        final_score = min(final_score, 2)
        episode.classification_reason = "存在长输出、重复检查或未整合等强信号，调度成本可能大于收益。"
    elif has_observability_gap and final_score <= 2:
        episode.classification = "insufficient_evidence"
        episode.classification_reason = "缺少结果、消费或关闭事件，不能把不可观测直接归因为无效使用。"
    else:
        episode.classification = "ineffective_use"
        episode.classification_reason = "委派可能合理，但结果消费、压缩价值或闭环证据不足。"

    scores = dict(raw_scores)
    scores["net_utility"] = final_score
    episode.raw_scores = raw_scores
    episode.confidence_penalties = penalties
    episode.final_score = final_score
    episode.scores = scores

    reasons = []
    if episode.consumed:
        reasons.append("主 Agent 有明确消费结果信号")
    else:
        reasons.append("未观察到下游消费证据")
    if episode.result_chars > VERBOSE_RESULT_CHARS:
        reasons.append("subagent 输出过长")
    if episode.duplicate_signals:
        reasons.append("主线程疑似重复检查已委派证据")
    if not episode.close_ref:
        reasons.append("缺少 close 事件或窗口内未观察到关闭")
    episode.rationale = "; ".join(reasons)


def round_int(value: float) -> int:
    return max(1, min(5, int(round(value))))


def build_episodes(events: list[Event]) -> tuple[list[Episode], list[dict[str, Any]]]:
    by_session: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        by_session[event.session_id].append(event)

    episodes: list[Episode] = []
    missed_candidates: list[dict[str, Any]] = []

    for session_id, session_events in by_session.items():
        pending_tool: dict[str, tuple[str, Event]] = {}
        episodes_by_call: dict[str, Episode] = {}
        episodes_by_agent: dict[str, Episode] = {}
        last_user = ""
        session_has_spawn = False
        shell_call_count = 0
        shell_refs: list[str] = []
        large_output_refs: list[str] = []
        broad_scan_refs: list[str] = []

        for index, event in enumerate(session_events):
            if event.kind == "message_user":
                last_user = summarize_text(event.text, 800)
                continue

            if event.kind == "function_call":
                pending_tool[event.call_id] = (event.tool_name, event)
                if event.tool_name in SHELL_TOOLS:
                    shell_call_count += 1
                    shell_refs.append(event.ref)
                    if BROAD_SCAN_RE.search(event.text):
                        broad_scan_refs.append(event.ref)

                if event.tool_name == "spawn_agent":
                    session_has_spawn = True
                    brief = extract_brief(event.arguments)
                    role = str(event.arguments.get("agent_type") or event.arguments.get("role") or event.arguments.get("name") or "default")
                    delegation_id = f"{session_id}:spawn:{len(episodes_by_call) + 1}"
                    episode = Episode(
                        delegation_id=delegation_id,
                        session_id=session_id,
                        cwd=event.cwd,
                        role=role,
                        spawn_ref=event.ref,
                        spawn_ts=event.timestamp,
                        spawn_call_id=event.call_id,
                        brief=brief,
                        brief_chars=len(brief),
                        task_context=last_user,
                    )
                    episodes_by_call[event.call_id] = episode
                    episodes.append(episode)
                elif event.tool_name == "wait_agent":
                    for target in extract_targets(event.arguments):
                        episode = episodes_by_agent.get(target)
                        if episode:
                            episode.wait_refs.append(event.ref)
                    pending_tool[event.call_id] = ("wait_agent", event)
                elif event.tool_name == "close_agent":
                    for target in extract_targets(event.arguments):
                        episode = episodes_by_agent.get(target)
                        if episode:
                            episode.close_ref = event.ref
                            episode.close_reason = "closed"
                continue

            if event.kind == "function_call_output":
                if event.output_length >= LARGE_OUTPUT_CHARS:
                    large_output_refs.append(event.ref)
                tool_name, call_event = pending_tool.pop(event.call_id, ("", event))
                if tool_name == "spawn_agent":
                    episode = episodes_by_call.get(event.call_id)
                    if episode:
                        agent_id = extract_agent_id(event.text, episode.delegation_id)
                        episode.agent_id = agent_id
                        episodes_by_agent[agent_id] = episode
                        episodes_by_agent[episode.delegation_id] = episode
                elif tool_name == "wait_agent":
                    targets = extract_targets(call_event.arguments)
                    target_episodes = [episodes_by_agent[target] for target in targets if target in episodes_by_agent]
                    if not target_episodes and len(episodes_by_agent) == 1:
                        target_episodes = list({id(ep): ep for ep in episodes_by_agent.values()}.values())
                    for episode in target_episodes:
                        episode.result_refs.append(event.ref)
                        episode.result_chars += len(event.text)
                        if episode.result_summary:
                            episode.result_summary = summarize_text(f"{episode.result_summary} {event.text}", 900)
                        else:
                            episode.result_summary = summarize_text(event.text, 900)
                        followup = session_events[index + 1 : index + 1 + POST_WAIT_EVENT_WINDOW]
                        infer_consumption(episode, followup)
                continue

        if not session_has_spawn:
            refs = []
            reason = ""
            if broad_scan_refs:
                reason = "broad_scan_without_delegation"
                refs = broad_scan_refs[:5]
            elif large_output_refs:
                reason = "large_tool_output_without_delegation"
                refs = large_output_refs[:5]
            elif shell_call_count >= 5:
                reason = "manual_research_loop"
                refs = [event.ref for event in session_events if event.kind == "function_call" and event.tool_name in SHELL_TOOLS][:5]
            if reason:
                missed_score = 0
                missed_score += 2 if len(broad_scan_refs) >= 2 else (1 if broad_scan_refs else 0)
                missed_score += 2 if shell_call_count >= 5 else 0
                missed_score += 1 if shell_call_count >= 8 else 0
                missed_score += 2 if large_output_refs else 0
                if missed_score >= 4:
                    confidence = "high"
                elif missed_score >= 3:
                    confidence = "medium"
                elif missed_score >= 2:
                    confidence = "low"
                else:
                    confidence = "false_positive_candidate"
                missed_candidates.append(
                    {
                        "session_id": session_id,
                        "cwd": session_events[0].cwd if session_events else "unknown",
                        "reason": reason,
                        "evidence_refs": refs,
                        "recommended_subagent_type": "researcher",
                        "confidence": confidence,
                        "missed_score": missed_score,
                        "broad_scan_count": len(broad_scan_refs),
                        "shell_call_count": shell_call_count,
                        "large_output_count": len(large_output_refs),
                        "shell_refs": shell_refs[:5],
                    }
                )

    for episode in episodes:
        if not episode.agent_id:
            episode.agent_id = episode.delegation_id
        if not episode.result_summary:
            episode.failure_signals.append("missing_wait_result")
        classify_episode(episode)

    return episodes, missed_candidates


def task_type_for_text(text: str) -> str:
    lower = text.lower()
    if any(term in lower for term in ("review", "审查", "复盘")):
        return "review"
    if any(term in lower for term in ("implement", "fix", "修改", "实现", "修复")):
        return "implementation"
    if any(term in lower for term in ("debug", "排查", "日志", "trace")):
        return "debug"
    if any(term in lower for term in ("research", "architecture", "调研", "架构")):
        return "research"
    return "unknown"


def episode_to_json(episode: Episode) -> dict[str, Any]:
    evidence_refs = [episode.spawn_ref] + episode.wait_refs + episode.result_refs + episode.consumed_refs
    if episode.close_ref:
        evidence_refs.append(episode.close_ref)
    return {
        "agent_id": episode.agent_id,
        "delegation_id": episode.delegation_id,
        "session_id": episode.session_id,
        "role": episode.role,
        "task_type": task_type_for_text(f"{episode.task_context}\n{episode.brief}"),
        "brief_summary": summarize_text(episode.brief, 300),
        "result_summary": episode.result_summary,
        "brief_chars": episode.brief_chars,
        "result_chars": episode.result_chars,
        **episode.scores,
        "raw_scores": episode.raw_scores,
        "confidence_penalties": episode.confidence_penalties,
        "final_score": episode.final_score,
        "classification": episode.classification,
        "classification_reason": episode.classification_reason,
        "consumed": episode.consumed,
        "consumed_refs": episode.consumed_refs,
        "duplicate_signals": episode.duplicate_signals,
        "failure_signals": episode.failure_signals,
        "close_reason": episode.close_reason or ("missing_close" if not episode.close_ref else "closed"),
        "evidence_refs": unique(evidence_refs),
        "rationale": episode.rationale,
    }


def unique(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def classify_overall(episodes: list[Episode], missed_candidates: list[dict[str, Any]]) -> tuple[str, int, str]:
    if not episodes and not missed_candidates:
        return "not_applicable", 3, "medium"
    if missed_candidates and not episodes:
        return "missed_use", 1, "medium"
    counts = Counter(ep.classification for ep in episodes)
    average = round_int(sum(ep.scores.get("net_utility", 1) for ep in episodes) / max(1, len(episodes)))
    harmful_count = counts.get("harmful_use", 0)
    good_count = counts.get("good_use", 0)
    harmful_ratio = harmful_count / max(1, len(episodes))
    good_ratio = good_count / max(1, len(episodes))
    missed_ratio = len(missed_candidates) / max(1, len(episodes))

    if harmful_count and harmful_ratio >= 0.25:
        return "harmful_use", min(average, 2), "medium"
    if missed_candidates and missed_ratio >= 0.5:
        return "missed_use", min(average, 2), "medium"
    if good_ratio >= 0.6:
        return "good_use", max(average, 4), "medium"
    if missed_candidates:
        return "missed_use", min(average, 2), "medium"
    return "ineffective_use", average, "medium"


def classify_overall_from_issues(episodes: list[Episode], issue_groups: list[dict[str, Any]]) -> tuple[str, int, str]:
    if not episodes and not issue_groups:
        return "not_applicable", 3, "medium"
    if not issue_groups:
        return "good_use", 5, "high"

    priority_counts = Counter(str(item.get("priority")) for item in issue_groups)
    penalty = priority_counts.get("P0", 0) * 2.0 + priority_counts.get("P1", 0) * 1.0 + priority_counts.get("P2", 0) * 0.5 + priority_counts.get("P3", 0) * 0.25
    score = round_int(5 - min(4, penalty))
    top_type = str(issue_groups[0].get("type", ""))
    confidence = str(issue_groups[0].get("confidence", "medium"))
    harmful_triggers = sum(int(item.get("trigger_count", 0)) for item in issue_groups if item.get("type") == "harmful_delegation")
    missed_triggers = sum(int(item.get("trigger_count", 0)) for item in issue_groups if item.get("type") == "missed_delegation")
    episode_count = max(1, len(episodes))

    if harmful_triggers / episode_count >= 0.25:
        classification = "harmful_use"
    elif top_type == "harmful_delegation":
        classification = "harmful_use"
    elif missed_triggers:
        classification = "missed_use"
    elif top_type == "observability_gap":
        classification = "insufficient_evidence"
    else:
        classification = "ineffective_use"
    return classification, score, confidence


def root_causes_from_issues(issue_groups: list[dict[str, Any]]) -> list[str]:
    mapping = {
        "observability_gap": "observability_gap",
        "missing_close": "observability_gap",
        "unconsumed_result": "result_synthesis_issue",
        "poor_integration": "result_synthesis_issue",
        "overlong_result": "subagent_execution_issue",
        "low_compression_value": "subagent_execution_issue",
        "weak_handoff": "handoff_issue",
        "duplicate_work": "tool_issue",
        "missed_delegation": "planner_issue",
        "false_positive_missed_delegation": "observability_gap",
        "harmful_delegation": "planner_issue",
    }
    return sorted({mapping.get(str(item.get("type")), "observability_gap") for item in issue_groups})


def recommended_actions_from_issues(issue_groups: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions = []
    for item in issue_groups:
        if item.get("priority") not in {"P0", "P1"}:
            continue
        actions.append(
            {
                "priority": str(item.get("priority")),
                "action": str(item.get("recommended_fix")),
                "owner_layer": "workflow" if item.get("type") not in {"observability_gap", "false_positive_missed_delegation"} else "reviewer",
                "issue_type": str(item.get("type")),
            }
        )
    if actions:
        return actions[:5]
    return [
        {
            "priority": "P3",
            "action": "继续收集 value packets，并用人工复核样本校准阈值。",
            "owner_layer": "automation",
            "issue_type": "calibration",
        }
    ]


def build_report_summary(
    classification: str,
    score: int,
    confidence: str,
    issue_groups: list[dict[str, Any]],
    episodes: list[Episode],
) -> dict[str, Any]:
    if score <= 2:
        health = "差"
    elif score == 3:
        health = "一般"
    else:
        health = "良好"
    top_issue = issue_groups[0] if issue_groups else None
    high_conf = sum(1 for item in issue_groups if item.get("confidence") == "high")
    unknown = sum(1 for item in issue_groups if item.get("evidence_strength") in {"candidate", "unknown"} or item.get("type") == "observability_gap")
    return {
        "health_label": health,
        "score": score,
        "confidence": confidence,
        "core_problem": str(top_issue.get("title")) if top_issue else "未发现需要优先处理的 subagent 工作流问题。",
        "should_optimize": bool(issue_groups and any(item.get("priority") in {"P0", "P1"} for item in issue_groups)),
        "first_fix_direction": str(top_issue.get("recommended_fix")) if top_issue else "保持现有流程，继续采集样本。",
        "high_confidence_issue_count": high_conf,
        "unknown_issue_count": unknown,
        "top_priorities": [item["type"] for item in issue_groups if item.get("priority") in {"P0", "P1"}][:5],
        "good_use_count": sum(1 for ep in episodes if ep.classification == "good_use"),
        "classification": classification,
    }


def root_causes_for(episodes: list[Episode], missed_candidates: list[dict[str, Any]]) -> list[str]:
    causes = set()
    if missed_candidates:
        causes.add("planner_issue")
    for ep in episodes:
        if ep.scores.get("handoff_quality", 0) <= 2:
            causes.add("handoff_issue")
        if ep.scores.get("result_quality", 0) <= 2:
            causes.add("subagent_execution_issue")
        if not ep.consumed:
            causes.add("result_synthesis_issue")
        if ep.duplicate_signals:
            causes.add("tool_issue")
        if not ep.result_refs:
            causes.add("observability_gap")
    return sorted(causes)


def recommended_actions(episodes: list[Episode], missed_candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if missed_candidates:
        actions.append(
            {
                "priority": "P1",
                "action": "为 broad scan 和大输出场景生成漏用 packet，先看证据包再升级 coverage 指标。",
                "owner_layer": "reviewer",
            }
        )
    if any(ep.scores.get("handoff_quality", 0) <= 2 for ep in episodes):
        actions.append(
            {
                "priority": "P1",
                "action": "要求 delegation brief 至少包含目标、范围、证据要求、输出契约和完成条件。",
                "owner_layer": "workflow",
            }
        )
    if any(not ep.consumed for ep in episodes):
        actions.append(
            {
                "priority": "P2",
                "action": "wait_agent 后要求主线程写一条短 integration note，说明结果被采用、忽略或需要复查。",
                "owner_layer": "workflow",
            }
        )
    if any(ep.duplicate_signals for ep in episodes):
        actions.append(
            {
                "priority": "P2",
                "action": "把主线程重复检查已委派证据标记为潜在 context-savings 退化。",
                "owner_layer": "reviewer",
            }
        )
    if not actions:
        actions.append(
            {
                "priority": "P3",
                "action": "继续收集 value packets，并用人工复核样本校准阈值。",
                "owner_layer": "automation",
            }
        )
    return actions


CLASSIFICATION_LABELS = {
    "good_use": "有效使用：subagent 带来明确净收益",
    "ineffective_use": "低效使用：完成了部分工作但净收益不清楚",
    "harmful_use": "有害使用：引入污染、重复或未闭环成本",
    "missed_use": "漏用：存在适合委派但未委派的场景",
    "not_applicable": "不适用：窗口内没有足够 delegation 场景",
    "insufficient_evidence": "证据不足：无法从轨迹可靠判断",
}

ROOT_CAUSE_LABELS = {
    "planner_issue": "规划问题：该拆分/委派时没有合理委派，或委派时机不佳",
    "handoff_issue": "交接问题：brief 缺目标、范围、证据要求或输出契约",
    "subagent_execution_issue": "执行问题：subagent 输出质量低、跑偏或缺证据",
    "tool_issue": "工具/重复劳动问题：主线程或多个 worker 疑似重复检查同一证据",
    "result_synthesis_issue": "结果整合问题：主 Agent 没有明确消费或转化 subagent 结果",
    "observability_gap": "可观测性缺口：缺 wait result、close、consumption 或结构化 delegation id",
}

REASON_LABELS = {
    "broad_scan_without_delegation": "主线程发生 broad scan，但没有创建 subagent",
    "large_tool_output_without_delegation": "主线程吸收大输出，但没有创建 subagent",
    "manual_research_loop": "主线程出现多轮手工搜索/分析循环，但没有创建 subagent",
}

DIMENSION_LABELS = {
    "delegation_fit": "委派适配度",
    "handoff_quality": "交接质量",
    "result_quality": "结果质量",
    "compression_value": "压缩价值",
    "decision_impact": "决策影响",
    "context_savings": "上下文节省",
    "duplication_cost": "重复劳动控制",
    "closure_quality": "关闭质量",
    "failure_recoverability": "失败恢复",
    "net_utility": "净效用",
}

OWNER_LABELS = {
    "reviewer": "复盘器",
    "workflow": "工作流",
    "automation": "自动化",
    "hook": "Hook",
    "project-profile": "项目画像",
}

ISSUE_META = {
    "observability_gap": {
        "title": "可观测性缺口影响价值判断",
        "impact": "缺少结果、消费或关闭证据时，复盘器无法可靠判断 subagent 是否真正有用。",
        "recommended_fix": "补充 delegation_id、wait result、consumption evidence、close reason 等结构化事件；报告中将此类问题与真实 workflow 问题分开。",
        "expected_benefit": "减少误判，把优化焦点从猜测转向可验证证据。",
        "fixability": 3,
    },
    "unconsumed_result": {
        "title": "Subagent 结果缺少可观测消费证据",
        "impact": "即使 subagent 产出了结果，也无法确认主 Agent 是否吸收并用于后续决策。",
        "recommended_fix": "wait_agent 后要求主线程写短 integration note，说明采用、忽略或需要复查的结果字段。",
        "expected_benefit": "让 decision impact 可追踪，避免结果悬空。",
        "fixability": 3,
    },
    "missing_close": {
        "title": "Subagent 生命周期关闭信号不足",
        "impact": "缺少 close 事件会让 retained、失败、窗口截断和真实未闭环混在一起。",
        "recommended_fix": "要求 close_agent 或 retained reason 必须结构化记录，窗口截断时标记为 unknown。",
        "expected_benefit": "降低生命周期误报，明确哪些 worker 需要回收或解释。",
        "fixability": 3,
    },
    "overlong_result": {
        "title": "Subagent 输出过长，压缩价值不足",
        "impact": "主 Agent 仍需吸收大量文本，未实现节省上下文的核心目标。",
        "recommended_fix": "为 subagent final 增加硬性输出契约：结论、证据、风险、下一步，限制原始展开。",
        "expected_benefit": "减少主上下文污染，提高结果可决策性。",
        "fixability": 3,
    },
    "weak_handoff": {
        "title": "委派 brief 边界或输出契约不足",
        "impact": "subagent 不知道目标、范围、证据要求或完成条件时，容易跑偏或返回不可决策内容。",
        "recommended_fix": "委派模板强制包含 goal、scope、evidence、output contract、done condition。",
        "expected_benefit": "提高一次委派成功率，减少返工。",
        "fixability": 3,
    },
    "duplicate_work": {
        "title": "主线程疑似重复检查已委派证据",
        "impact": "看似委派，实际主 Agent 仍重读原始材料，context savings 被抵消。",
        "recommended_fix": "wait_agent 后优先基于子结果决策；若必须复查，只复查代表证据并记录原因。",
        "expected_benefit": "恢复 subagent 对主上下文的压缩价值。",
        "fixability": 2,
    },
    "low_compression_value": {
        "title": "Subagent 没有形成足够压缩的决策摘要",
        "impact": "结果不够短、干净、结构化，主 Agent 仍需做大量二次整理。",
        "recommended_fix": "把输出限制为 verdict-first packet，禁止粘贴长原始日志或无裁剪文件内容。",
        "expected_benefit": "降低主线程认知负担。",
        "fixability": 3,
    },
    "missed_delegation": {
        "title": "复杂搜索/阅读任务疑似漏用 subagent",
        "impact": "主线程承担了本可局部化的搜索、阅读或分析工作，增加上下文占用。",
        "recommended_fix": "对多轮搜索、broad scan、大输出吸收场景触发委派提醒，但只升级高/中置信候选。",
        "expected_benefit": "把噪声留在子线程，提高主 Agent 调度效率。",
        "fixability": 2,
    },
    "false_positive_missed_delegation": {
        "title": "漏用判断存在规则误报候选",
        "impact": "单次 broad scan 或低噪声搜索不一定值得委派，过度提醒会干扰工作流。",
        "recommended_fix": "保留为 debug 信号，不在主流程强提醒；用人工样本校准 missed_use 阈值。",
        "expected_benefit": "避免为了评分而滥用 subagent。",
        "fixability": 2,
    },
    "poor_integration": {
        "title": "主 Agent 缺少结果整合动作",
        "impact": "subagent 结果没有被转成计划、代码动作、验证动作或结论时，委派价值无法闭环。",
        "recommended_fix": "要求主线程在 wait 后输出 integration note，并把引用证据连接到后续动作。",
        "expected_benefit": "提高 delegation 的 decision impact 可见性。",
        "fixability": 3,
    },
    "harmful_delegation": {
        "title": "少量委派呈现负收益强信号",
        "impact": "长输出、重复检查或无整合叠加时，subagent 可能制造更多上下文污染和调度成本。",
        "recommended_fix": "优先审查这些 episode 的 handoff 与输出 contract，必要时降低触发或改为更窄角色。",
        "expected_benefit": "减少有害委派，保护主上下文窗口。",
        "fixability": 2,
    },
}


def zh_classification(value: str) -> str:
    return CLASSIFICATION_LABELS.get(value, value)


def zh_root_cause(value: str) -> str:
    return ROOT_CAUSE_LABELS.get(value, value)


def zh_reason(value: str) -> str:
    return REASON_LABELS.get(value, value)


def bar(count: int, scale: int = 3) -> str:
    return "█" * max(1, min(12, (count + scale - 1) // scale)) if count else ""


def confidence_rank(confidence: str) -> int:
    return {"high": 3, "medium": 2, "low": 1, "unknown": 0}.get(confidence, 1)


def confidence_weight(confidence: str) -> float:
    return {"high": 1.0, "medium": 0.75, "low": 0.45, "unknown": 0.3}.get(confidence, 0.45)


def severity_weight(severity: str) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(severity, 1)


def frequency_weight(count: int) -> float:
    if count >= 20:
        return 3.0
    if count >= 8:
        return 2.2
    if count >= 3:
        return 1.5
    return 1.0


def priority_from_score(score: float, confidence: str) -> str:
    if confidence in {"low", "unknown"}:
        return "P3" if score < 5 else "P2"
    if confidence == "high" and score >= 18:
        return "P0"
    if score >= 9:
        return "P1"
    if score >= 4:
        return "P2"
    return "P3"


def evidence_strength(confidence: str) -> str:
    return {"high": "confirmed", "medium": "likely", "low": "candidate", "unknown": "unknown"}.get(confidence, "candidate")


def representative_episode_example(episode: Episode, why: str) -> dict[str, str]:
    evidence = (episode.result_refs or episode.wait_refs or episode.consumed_refs or [episode.spawn_ref])[0]
    return {
        "session": episode.session_id,
        "agent": episode.agent_id or episode.delegation_id,
        "evidence": evidence,
        "why_it_matters": why,
    }


def representative_missed_example(candidate: dict[str, Any], why: str) -> dict[str, str]:
    refs = candidate.get("evidence_refs") or candidate.get("shell_refs") or []
    return {
        "session": str(candidate.get("session_id", "")),
        "agent": "-",
        "evidence": str(refs[0]) if refs else "",
        "why_it_matters": why,
    }


def build_issue(
    issue_type: str,
    trigger_count: int,
    sessions: Iterable[str],
    confidence: str,
    severity: str,
    examples: list[dict[str, str]],
    evidence_summary: str,
    needs_human_review: bool = False,
) -> dict[str, Any]:
    meta = ISSUE_META[issue_type]
    score = severity_weight(severity) * frequency_weight(trigger_count) * confidence_weight(confidence) * float(meta["fixability"])
    priority = priority_from_score(score, confidence)
    return {
        "type": issue_type,
        "title": meta["title"],
        "severity": severity,
        "trigger_count": trigger_count,
        "affected_sessions": len(set(sessions)),
        "confidence": confidence,
        "evidence_strength": evidence_strength(confidence),
        "impact": meta["impact"],
        "evidence_summary": evidence_summary,
        "representative_examples": examples[:2],
        "recommended_fix": meta["recommended_fix"],
        "priority": priority,
        "priority_score": round(score, 2),
        "expected_benefit": meta["expected_benefit"],
        "needs_human_review": needs_human_review or confidence in {"low", "unknown"},
        "owner_decision": "建议优先修复" if priority in {"P0", "P1"} else ("暂缓，先观察或抽检" if priority == "P3" else "可排入下一轮修复"),
    }


def build_issue_groups(episodes: list[Episode], missed_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    total_episodes = max(1, len(episodes))

    missing_result = [ep for ep in episodes if not ep.result_refs]
    missing_consumption = [ep for ep in episodes if ep.result_refs and not ep.consumed]
    missing_close = [ep for ep in episodes if not ep.close_ref]
    observability_eps = unique_by_episode(missing_result + missing_close + missing_consumption)
    if observability_eps:
        confidence = "high" if len(missing_result) >= 3 or len(observability_eps) / total_episodes >= 0.35 else "medium"
        issues.append(
            build_issue(
                "observability_gap",
                len(observability_eps),
                (ep.session_id for ep in observability_eps),
                confidence,
                "high" if len(observability_eps) / total_episodes >= 0.35 else "medium",
                [representative_episode_example(ep, "缺少结果、消费或关闭信号，不能可靠判断真实价值。") for ep in observability_eps],
                f"{len(missing_result)} 个 episode 缺少 wait result，{len(missing_close)} 个缺少 close，{len(missing_consumption)} 个缺少消费证据。",
                needs_human_review=True,
            )
        )

    if missing_consumption:
        issues.append(
            build_issue(
                "unconsumed_result",
                len(missing_consumption),
                (ep.session_id for ep in missing_consumption),
                "medium",
                "medium",
                [representative_episode_example(ep, "有结果输出，但只观察到缺少消费引用，不能确认是否被吸收。") for ep in missing_consumption],
                "存在 subagent 输出后未观察到主线程引用、计划转化或后续动作的情况。",
                needs_human_review=True,
            )
        )

    if missing_close:
        issues.append(
            build_issue(
                "missing_close",
                len(missing_close),
                (ep.session_id for ep in missing_close),
                "medium",
                "medium" if len(missing_close) / total_episodes < 0.4 else "high",
                [representative_episode_example(ep, "缺少 close 事件，可能是窗口截断、埋点缺失或真实未闭环。") for ep in missing_close],
                "观察到较多 episode 没有 close 事件，需要先区分窗口截断与真实生命周期泄漏。",
                needs_human_review=True,
            )
        )

    overlong = [ep for ep in episodes if ep.result_chars > VERBOSE_RESULT_CHARS]
    if overlong:
        issues.append(
            build_issue(
                "overlong_result",
                len(overlong),
                (ep.session_id for ep in overlong),
                "high" if any(ep.duplicate_signals or not ep.consumed for ep in overlong) else "medium",
                "high",
                [representative_episode_example(ep, "输出超过长结果阈值，主线程可能仍需吸收大量文本。") for ep in overlong],
                "多个 subagent 输出超过阈值，部分还伴随重复检查或缺少消费证据。",
            )
        )

    weak_handoff = [ep for ep in episodes if ep.raw_scores.get("handoff_quality", ep.scores.get("handoff_quality", 0)) <= 2]
    if weak_handoff:
        issues.append(
            build_issue(
                "weak_handoff",
                len(weak_handoff),
                (ep.session_id for ep in weak_handoff),
                "high",
                "medium",
                [representative_episode_example(ep, "brief 缺少足够目标、范围、证据或输出契约。") for ep in weak_handoff],
                "低质量 handoff 会直接降低 subagent 独立完成任务的概率。",
            )
        )

    duplicate = [ep for ep in episodes if ep.duplicate_signals]
    if duplicate:
        issues.append(
            build_issue(
                "duplicate_work",
                len(duplicate),
                (ep.session_id for ep in duplicate),
                "medium",
                "medium",
                [representative_episode_example(ep, "wait 后主线程疑似重新执行 broad scan 或复查同一证据。") for ep in duplicate],
                "存在主线程复查已委派证据的信号，context savings 可能被抵消。",
                needs_human_review=True,
            )
        )

    low_compression = [ep for ep in episodes if ep.result_refs and ep.scores.get("compression_value", 0) <= 2]
    if low_compression:
        issues.append(
            build_issue(
                "low_compression_value",
                len(low_compression),
                (ep.session_id for ep in low_compression),
                "medium",
                "high" if len(low_compression) >= 5 else "medium",
                [representative_episode_example(ep, "结果没有压缩成短小、结构化、可决策摘要。") for ep in low_compression],
                "低压缩值说明 subagent 可能把噪声重新带回主上下文。",
            )
        )

    poor_integration = [ep for ep in missing_consumption if ep.post_wait_tool_names or ep.result_chars > 0]
    if poor_integration:
        issues.append(
            build_issue(
                "poor_integration",
                len(poor_integration),
                (ep.session_id for ep in poor_integration),
                "medium",
                "medium",
                [representative_episode_example(ep, "wait 后没有可观测的整合说明或结果引用。") for ep in poor_integration],
                "主线程等待结果后缺少明确的 plan/action/conclusion 连接。",
                needs_human_review=True,
            )
        )

    harmful = [ep for ep in episodes if ep.classification == "harmful_use"]
    if harmful:
        issues.append(
            build_issue(
                "harmful_delegation",
                len(harmful),
                (ep.session_id for ep in harmful),
                "high",
                "high",
                [representative_episode_example(ep, "长输出、重复检查或无整合叠加，委派可能为负收益。") for ep in harmful],
                "少量 episode 具备较强负收益信号，应作为抽检入口而不是扩大成全局结论。",
                needs_human_review=True,
            )
        )

    high_medium_missed = [item for item in missed_candidates if item.get("confidence") in {"high", "medium"}]
    if high_medium_missed:
        confidence = "high" if any(item.get("confidence") == "high" for item in high_medium_missed) else "medium"
        issues.append(
            build_issue(
                "missed_delegation",
                len(high_medium_missed),
                (str(item.get("session_id", "")) for item in high_medium_missed),
                confidence,
                "high" if len(high_medium_missed) >= 8 else "medium",
                [representative_missed_example(item, "多轮搜索、broad scan 或大输出吸收满足高/中置信漏用条件。") for item in high_medium_missed],
                "高/中置信漏用候选已从原始 broad scan 列表中过滤出来。",
                needs_human_review=True,
            )
        )

    low_missed = [item for item in missed_candidates if item.get("confidence") not in {"high", "medium"}]
    if low_missed:
        issues.append(
            build_issue(
                "false_positive_missed_delegation",
                len(low_missed),
                (str(item.get("session_id", "")) for item in low_missed),
                "low",
                "low",
                [representative_missed_example(item, "信号不足，可能只是一次低成本搜索，不应直接判定漏用。") for item in low_missed],
                "低置信漏用候选只保留在 debug artifact 中，不作为主流程强结论。",
                needs_human_review=True,
            )
        )

    return sorted(issues, key=lambda item: (-float(item["priority_score"]), item["priority"], item["type"]))


def unique_by_episode(episodes: list[Episode]) -> list[Episode]:
    seen = set()
    result = []
    for episode in episodes:
        key = episode.delegation_id
        if key in seen:
            continue
        seen.add(key)
        result.append(episode)
    return result


def packet_for_episode(episode: Episode) -> dict[str, Any]:
    packet = episode_to_json(episode)
    encoded = json.dumps(packet, ensure_ascii=False)
    if len(encoded) <= MAX_PACKET_TEXT:
        return packet
    packet["result_summary"] = summarize_text(packet.get("result_summary", ""), 220)
    packet["brief_summary"] = summarize_text(packet.get("brief_summary", ""), 180)
    return packet


def build_report(
    codex_home: Path,
    since_days: int,
    report_id: Optional[str],
    reports_dir: Optional[str],
    include_paths: bool = False,
    max_packets: int = 20,
    since_ts: Optional[datetime] = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = since_ts.astimezone(timezone.utc) if since_ts else now - timedelta(days=since_days)
    events, stats = iter_events(codex_home, cutoff, include_paths=include_paths)
    episodes, missed_candidates = build_episodes(events)
    issue_groups = build_issue_groups(episodes, missed_candidates)
    classification, overall_score, confidence = classify_overall_from_issues(episodes, issue_groups)
    report_summary = build_report_summary(classification, overall_score, confidence, issue_groups, episodes)
    session_ids = sorted({event.session_id for event in events if event.session_id})
    projects = Counter(Path(event.cwd).name if event.cwd and event.cwd != "unknown" else "unknown" for event in events)
    class_counts = Counter(ep.classification for ep in episodes)

    packets = [packet_for_episode(ep) for ep in sorted(episodes, key=lambda item: item.scores.get("net_utility", 0))[:max_packets]]
    data = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "report_name": f"subagent-value-{report_id or now.strftime('%Y%m%dT%H%M%SZ')}.json",
        "codex_home": str(codex_home) if include_paths else "[redacted]",
        "window": {"from": cutoff.isoformat().replace("+00:00", "Z"), "to": now.isoformat().replace("+00:00", "Z")},
        "since_days": since_days,
        "since_ts": since_ts.isoformat().replace("+00:00", "Z") if since_ts else None,
        "paths_included": include_paths,
        "session_id": "window",
        "sessions_seen": len(session_ids),
        "projects": dict(projects),
        "task_type": "mixed" if len(projects) > 1 else "unknown",
        "overall_classification": classification,
        "overall_score": overall_score,
        "confidence": confidence,
        "report_summary": report_summary,
        "totals": {
            **stats,
            "events": len(events),
            "episodes": len(episodes),
            "missed_use_candidates": len(missed_candidates),
            "consumed_episodes": sum(1 for ep in episodes if ep.consumed),
            "closed_episodes": sum(1 for ep in episodes if ep.close_ref),
        },
        "classification_counts": dict(class_counts),
        "issue_groups": issue_groups,
        "subagents": [episode_to_json(ep) for ep in episodes],
        "missed_use_candidates": missed_candidates,
        "selected_packets": packets,
        "debug_artifact": {
            "episodes": [episode_to_json(ep) for ep in episodes],
            "missed_candidates": missed_candidates,
            "heuristic_hits": {
                "overlong_result": sum(1 for ep in episodes if ep.result_chars > VERBOSE_RESULT_CHARS),
                "duplicate_work": sum(1 for ep in episodes if ep.duplicate_signals),
                "missing_result_event": sum(1 for ep in episodes if not ep.result_refs),
                "missing_close_event": sum(1 for ep in episodes if not ep.close_ref),
                "unconsumed_result": sum(1 for ep in episodes if ep.result_refs and not ep.consumed),
                "high_or_medium_missed_delegation": sum(1 for item in missed_candidates if item.get("confidence") in {"high", "medium"}),
            },
            "raw_score_details": [
                {
                    "delegation_id": ep.delegation_id,
                    "agent_id": ep.agent_id,
                    "raw_scores": ep.raw_scores,
                    "confidence_penalties": ep.confidence_penalties,
                    "final_score": ep.final_score,
                    "classification": ep.classification,
                    "classification_reason": ep.classification_reason,
                }
                for ep in episodes
            ],
            "observability_gaps": [
                {
                    "delegation_id": ep.delegation_id,
                    "session_id": ep.session_id,
                    "missing_result": not bool(ep.result_refs),
                    "missing_close": not bool(ep.close_ref),
                    "missing_consumption_evidence": bool(ep.result_refs) and not ep.consumed,
                    "evidence_refs": unique([ep.spawn_ref] + ep.wait_refs + ep.result_refs + ep.consumed_refs),
                }
                for ep in episodes
                if not ep.result_refs or not ep.close_ref or (ep.result_refs and not ep.consumed)
            ],
            "artifact_notes": [
                "Markdown 主报告只展示聚合问题和代表证据。",
                "完整 episode、missed_use、评分和 heuristic 明细保留在本 debug_artifact 中。",
                "缺少 result/close/consumption 的场景默认按可观测性缺口处理，不直接归因为真实无效。",
            ],
        },
        "root_causes": root_causes_from_issues(issue_groups),
        "recommended_actions": recommended_actions_from_issues(issue_groups),
        "needs_human_review": classification in {"harmful_use", "missed_use", "ineffective_use"},
    }

    reports_root = Path(reports_dir) if reports_dir else codex_home / "orchestrator-observability" / "reports"
    reports_root.mkdir(parents=True, exist_ok=True)
    stem = data["report_name"].removesuffix(".json")
    json_path = reports_root / data["report_name"]
    md_path = reports_root / f"{stem}.md"
    data["json_report"] = str(json_path)
    data["markdown_report"] = str(md_path)
    with json_path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
    md_path.write_text(render_markdown(data), encoding="utf-8")
    return data


def render_markdown(data: dict[str, Any]) -> str:
    summary = data["report_summary"]
    issue_groups = data.get("issue_groups", [])
    totals = data["totals"]
    high_or_medium_missed = sum(1 for item in data.get("missed_use_candidates", []) if item.get("confidence") in {"high", "medium"})
    issue_count = sum(int(item.get("trigger_count", 0)) for item in issue_groups if item.get("confidence") in {"high", "medium"})
    unknown_count = sum(int(item.get("trigger_count", 0)) for item in issue_groups if item.get("confidence") in {"low", "unknown"} or item.get("type") == "observability_gap")

    lines = [
        "# Subagent 工作流复盘摘要",
        "",
        "## 1. 总体判断",
        f"- 健康度：{summary['health_label']}",
        f"- 总分：{summary['score']} / 5",
        f"- 本轮最核心问题：{summary['core_problem']}",
        f"- 是否建议继续优化：{'是' if summary['should_optimize'] else '否'}",
        f"- 首要优化方向：{summary['first_fix_direction']}",
        f"- 时间窗口：{data['window']['from']} -> {data['window']['to']}",
        "",
        "## 2. 关键指标",
        "| 指标 | 数值 | 解读 |",
        "|---|---:|---|",
        f"| 覆盖 session | {data['sessions_seen']} | 本轮复盘观察到的 session 范围。 |",
        f"| 委派片段 | {totals['episodes']} | 只作为工作量背景，不直接代表质量。 |",
        f"| 有消费证据 | {totals['consumed_episodes']} | 说明有多少结果能被观察到进入主线程决策链。 |",
        f"| 已关闭片段 | {totals['closed_episodes']} | 缺失 close 会先视为可观测性问题。 |",
        f"| 高/中置信问题触发 | {issue_count} | 已聚合为问题类型，优先看 P0/P1。 |",
        f"| 高/中置信漏用 | {high_or_medium_missed} | 已过滤单次 broad scan 等低置信误报。 |",
        f"| 不可观测/候选问题 | {unknown_count} | 需要人工抽检或补埋点，不能强行归因。 |",
        "",
        "## 3. 问题优先级看板",
        "| 优先级 | 问题类型 | 触发次数 | 严重程度 | 证据强度 | 建议 |",
        "|---|---|---:|---|---|---|",
    ]

    for issue in issue_groups[:7]:
        lines.append(
            "| {priority} | {title} | {bar} {count} | {severity} | {strength} / {confidence} | {fix} |".format(
                priority=issue["priority"],
                title=issue["title"],
                bar=bar(int(issue["trigger_count"])),
                count=issue["trigger_count"],
                severity=issue["severity"],
                strength=issue["evidence_strength"],
                confidence=issue["confidence"],
                fix=summarize_text(str(issue["recommended_fix"]), 90).replace("|", "/"),
            )
        )
    if not issue_groups:
        lines.append("| - | 未发现需要升级的问题 | 0 | - | - | 继续观察 |")

    lines.extend(["", "## 4. Top 问题详情"])
    for issue in issue_groups[:5]:
        lines.extend(
            [
                f"### {issue['priority']}: {issue['title']}",
                f"- 触发次数：{issue['trigger_count']}，影响 session：{issue['affected_sessions']}，置信度：{issue['confidence']}（{issue['evidence_strength']}）",
                f"- 影响：{issue['impact']}",
                f"- 证据摘要：{issue['evidence_summary']}",
            ]
        )
        examples = issue.get("representative_examples") or []
        if examples:
            example_text = "；".join(
                f"{item.get('session', '')} / {item.get('agent', '')}: {item.get('evidence', '')}"
                for item in examples[:2]
            )
            lines.append(f"- 代表证据：{example_text}")
        else:
            lines.append("- 代表证据：详见 JSON debug_artifact")
        lines.extend(
            [
                f"- 本质判断：{issue['owner_decision']}",
                f"- 建议修复：{issue['recommended_fix']}",
                f"- 预期收益：{issue['expected_benefit']}",
                f"- 是否需要人工确认：{'是' if issue['needs_human_review'] else '否'}",
            ]
        )

    lines.extend(
        [
            "",
            "## 5. 建议本轮修复清单",
            "| 顺序 | 修复项 | 解决的问题 | 预期收益 | 成本 | 风险 |",
            "|---:|---|---|---|---|---|",
        ]
    )
    priority_issues = [item for item in issue_groups if item["priority"] in {"P0", "P1"}][:5]
    for index, issue in enumerate(priority_issues, start=1):
        cost = "低" if ISSUE_META.get(issue["type"], {}).get("fixability", 1) >= 3 else "中"
        risk = "中" if issue["needs_human_review"] else "低"
        lines.append(
            f"| {index} | {issue['recommended_fix']} | {issue['title']} | {issue['expected_benefit']} | {cost} | {risk} |"
        )
    if not priority_issues:
        lines.append("| 1 | 先做人工抽检与阈值校准 | 当前没有 P0/P1 确认问题 | 避免误调工作流 | 低 | 低 |")

    deferred = [item for item in issue_groups if item["priority"] in {"P2", "P3"}][:4]
    lines.extend(["", "## 6. 暂不建议处理"])
    if deferred:
        for issue in deferred:
            lines.append(f"- {issue['title']}：{issue['owner_decision']}。原因：优先级 {issue['priority']}，证据强度 {issue['evidence_strength']}。")
    else:
        lines.append("- 暂无明确暂缓项。")

    sample_refs = []
    for issue in issue_groups[:3]:
        for example in issue.get("representative_examples", [])[:1]:
            if example.get("evidence"):
                sample_refs.append(example["evidence"])
    lines.extend(
        [
            "",
            "## 7. 附录入口",
            f"- 完整 JSON：{data.get('json_report', '')}",
            "- 原始 subagent 明细：JSON 字段 `debug_artifact.episodes` / `subagents`",
            "- missed_use 候选明细：JSON 字段 `debug_artifact.missed_candidates` / `missed_use_candidates`",
            "- 中间评分与惩罚：JSON 字段 `debug_artifact.raw_score_details`",
            "- 可观测性缺口：JSON 字段 `debug_artifact.observability_gaps`",
            f"- 代表证据引用：{'; '.join(sample_refs[:3]) if sample_refs else '无'}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    since_ts = parse_since_ts(args.since_ts)
    output = build_report(
        Path(args.codex_home).expanduser(),
        since_days=args.since_days,
        report_id=args.report_id,
        reports_dir=args.reports_dir,
        include_paths=args.include_paths,
        max_packets=args.max_packets,
        since_ts=since_ts,
    )
    print(
        json.dumps(
            {
                "json_report": output["json_report"],
                "markdown_report": output["markdown_report"],
                "overall_classification": output["overall_classification"],
                "overall_score": output["overall_score"],
                "health_label": output["report_summary"]["health_label"],
                "top_issue_types": output["report_summary"]["top_priorities"],
                "debug_artifact_available": "debug_artifact" in output,
                "totals": output["totals"],
                "root_causes": output["root_causes"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
