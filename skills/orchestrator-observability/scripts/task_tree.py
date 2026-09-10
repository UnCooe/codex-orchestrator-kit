#!/usr/bin/env python3
"""Reconstruct current Codex task trees from session JSONL files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import types
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional


TASK_TREE_TOOLS = {"spawn_agent", "followup_task", "send_message", "wait_agent"}
MESSAGE_EDGE_DIRECTIONS = ("child_to_child", "root_to_child", "child_to_root", "unknown")
KNOWN_GENERIC_ROLES = frozenset(
    {
        "conflict_resolver",
        "critic",
        "deployer",
        "implementer",
        "requirements_interpreter",
        "researcher",
        "reviewer",
        "test_designer",
        "test_runner",
        "test_writer",
    }
)
KNOWN_BUILTIN_ROLES = frozenset({"default", "explorer", "worker"})
TOKEN_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
ZERO_USAGE = {key: 0 for key in TOKEN_KEYS}
UUID_RE = re.compile(
    r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b"
)
sys.modules.setdefault(__name__, types.ModuleType(__name__))


@dataclass
class Event:
    timestamp: Optional[datetime]
    sequence: int
    kind: str
    name: str = ""
    call_id: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    output: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionNode:
    session_id: str
    source_file: str
    parent_thread_id: str = ""
    depth: int = 0
    agent_path: str = ""
    role: str = ""
    task_name: str = ""
    role_source: str = "unknown"
    role_contract_id: str = "unknown"
    configured_model: str = "unknown"
    effective_model: str = "unknown"
    configured_reasoning_effort: str = "unknown"
    effective_reasoning_effort: str = "unknown"
    spawn_arguments: dict[str, Any] = field(default_factory=dict)
    spawn_sequence: int = -1
    result_consumed: bool = False
    events: list[Event] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=lambda: dict(ZERO_USAGE))
    token_sequence: int = -1
    children: list["SessionNode"] = field(default_factory=list)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _role_contracts(agents_root: Optional[Path] = None) -> dict[str, dict[str, str]]:
    contracts: dict[str, dict[str, str]] = {}
    agents_root = agents_root or (_repo_root() / "agents")
    if not agents_root.is_dir():
        return contracts
    for path in sorted(agents_root.glob("*.toml")):
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        name = data.get("name")
        instructions = data.get("developer_instructions")
        if not isinstance(name, str) or not isinstance(instructions, str):
            continue
        marker = re.search(r"Role contract ID:\s*(subagent-role-v[0-9.]+/[A-Za-z0-9_-]+)\.", instructions)
        contracts[name] = {
            "role_contract_id": marker.group(1) if marker else "unknown",
            "model": str(data.get("model") or "unknown"),
            "reasoning_effort": str(data.get("model_reasoning_effort") or "unknown"),
        }
    return contracts


def parse_timestamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iter_jsonl_records(paths: Iterable[Path]) -> Iterator[tuple[Path, int, Optional[dict[str, Any]]]]:
    decoder = json.JSONDecoder()
    for path in paths:
        try:
            raw_text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            yield path, 0, None
            continue
        cursor = 0
        line_no = 1
        while cursor < len(raw_text):
            while cursor < len(raw_text) and raw_text[cursor].isspace():
                if raw_text[cursor] == "\n":
                    line_no += 1
                cursor += 1
            if cursor >= len(raw_text):
                break
            record_line = line_no
            try:
                record, end = decoder.raw_decode(raw_text, cursor)
            except json.JSONDecodeError:
                yield path, record_line, None
                next_newline = raw_text.find("\n", cursor)
                if next_newline == -1:
                    break
                line_no += raw_text[cursor : next_newline + 1].count("\n")
                cursor = next_newline + 1
                continue
            yield path, record_line, record if isinstance(record, dict) else None
            line_no += raw_text[cursor:end].count("\n")
            cursor = end


def _session_id_from_path(path: Path) -> str:
    match = UUID_RE.search(path.name)
    return match.group(1) if match else ""


def _record_fingerprint(record: dict[str, Any]) -> str:
    normalized = dict(record)
    normalized.pop("session_id", None)
    payload = normalized.get("payload")
    if isinstance(payload, dict):
        payload = dict(payload)
        payload.pop("session_id", None)
        normalized["payload"] = payload
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_arguments(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"text": value}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {}


def _parse_output(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _normalize_tool_name(value: object) -> str:
    raw = str(value or "").strip()
    return raw.rsplit("__", 1)[-1]


def _extract_token_usage(payload: dict[str, Any]) -> dict[str, int]:
    info = payload.get("info") if isinstance(payload.get("info"), dict) else payload
    usage = info.get("total_token_usage") if isinstance(info, dict) else None
    if not isinstance(usage, dict):
        usage = info.get("last_token_usage") if isinstance(info, dict) else None
    if not isinstance(usage, dict):
        return dict(ZERO_USAGE)
    return {
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "cached_input_tokens": int(usage.get("cached_input_tokens", 0) or 0),
        "cache_write_input_tokens": int(usage.get("cache_write_input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "reasoning_output_tokens": int(usage.get("reasoning_output_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }


def _meta_fields(payload: dict[str, Any]) -> dict[str, Any]:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    subagent = source.get("subagent") if isinstance(source.get("subagent"), dict) else {}
    thread_spawn = subagent.get("thread_spawn") if isinstance(subagent.get("thread_spawn"), dict) else {}
    parent = thread_spawn.get("parent_thread_id") or payload.get("parent_thread_id") or ""
    depth = thread_spawn.get("depth", payload.get("depth", 0) or 0)
    try:
        depth_int = int(depth)
    except Exception:
        depth_int = 0
    return {
        "session_id": str(payload.get("id") or ""),
        "parent_thread_id": str(parent or ""),
        "depth": depth_int,
        "agent_path": str(thread_spawn.get("agent_path") or payload.get("agent_path") or ""),
        "role": str(thread_spawn.get("agent_role") or payload.get("agent_role") or payload.get("role") or ""),
    }


def _find_string_key(value: Any, keys: set[str]) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, str) and item:
                return item
        for item in value.values():
            found = _find_string_key(item, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_string_key(item, keys)
            if found:
                return found
    return ""


def _turn_context_model_effort(record: dict[str, Any], payload: dict[str, Any]) -> tuple[str, str]:
    record_type = str(record.get("type") or payload.get("type") or "")
    if record_type != "turn_context" and "turn_context" not in payload:
        return "", ""
    source = payload.get("turn_context") if isinstance(payload.get("turn_context"), dict) else payload
    model = _find_string_key(source, {"model", "selected_model", "effective_model"})
    effort = _find_string_key(
        source,
        {"model_reasoning_effort", "reasoning_effort", "effective_reasoning_effort", "effort"},
    )
    return model, effort


WAIT_TARGET_KEYS = ("target", "targets", "agent", "agents", "task_name", "task_names")


def _wait_target_values(arguments: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in WAIT_TARGET_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value:
            values.add(value)
        elif isinstance(value, list):
            values.update(str(item) for item in value if isinstance(item, str) and item)
    return values


def _extract_first_string(value: Any, keys: set[str]) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, str) and item:
                return item
        for item in value.values():
            found = _extract_first_string(item, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _extract_first_string(item, keys)
            if found:
                return found
    return ""


def _message_content_from_arguments(arguments: dict[str, Any]) -> str:
    return _extract_first_string(arguments, {"encrypted_content", "message", "content", "text"})


def _message_content_from_payload(payload: dict[str, Any]) -> str:
    return _extract_first_string(payload, {"encrypted_content"})


def _message_target(arguments: dict[str, Any]) -> str:
    return _extract_first_string(arguments, {"target", "recipient", "to", "task_name"})


def _activity_target(payload: dict[str, Any]) -> str:
    return _extract_first_string(payload, {"target_task_path", "target", "recipient", "to", "agent_path", "task_name"})


def _activity_call_id(payload: dict[str, Any]) -> str:
    return _extract_first_string(payload, {"event_id", "call_id"})


def _message_source_hint(payload: dict[str, Any]) -> str:
    return _extract_first_string(payload, {"from", "author", "source"})


def _message_digest(value: str) -> tuple[str, int]:
    encoded = value.encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def _node_lookup(nodes: Iterable[SessionNode]) -> dict[str, SessionNode]:
    lookup: dict[str, SessionNode] = {}
    for node in nodes:
        for key in (node.session_id, node.agent_path, node.task_name):
            if key:
                lookup.setdefault(key, node)
        if node.task_name:
            lookup.setdefault(f"/root/{node.task_name}", node)
    return lookup


def _target_node(target: str, lookup: dict[str, SessionNode]) -> Optional[SessionNode]:
    if not target:
        return None
    if target in lookup:
        return lookup[target]
    normalized = target.strip()
    if normalized in lookup:
        return lookup[normalized]
    if not normalized.startswith("/"):
        return lookup.get(f"/root/{normalized}")
    return None


def _is_root_node(node: Optional[SessionNode]) -> bool:
    return bool(node and not node.parent_thread_id and node.depth == 0 and node.agent_path.rstrip("/") in {"", "/root"})


def _message_direction(source: SessionNode, target: Optional[SessionNode]) -> str:
    if target is None:
        return "unknown"
    source_root = _is_root_node(source)
    target_root = _is_root_node(target)
    if not source_root and not target_root:
        return "child_to_child"
    if source_root and not target_root:
        return "root_to_child"
    if not source_root and target_root:
        return "child_to_root"
    return "unknown"


def _delivery_index(nodes: Iterable[SessionNode]) -> dict[tuple[str, str], list[Event]]:
    delivered: dict[tuple[str, str], list[Event]] = {}
    for node in nodes:
        for event in node.events:
            if event.kind != "agent_message":
                continue
            content = _message_content_from_payload(event.payload)
            if not content:
                continue
            digest, _size = _message_digest(content)
            delivered.setdefault((node.session_id, digest), []).append(event)
    return delivered


def _activity_index(nodes: Iterable[SessionNode]) -> dict[tuple[str, str], list[Event]]:
    activities: dict[tuple[str, str], list[Event]] = {}
    for node in nodes:
        for event in node.events:
            if event.kind != "sub_agent_activity" or not event.call_id:
                continue
            activities.setdefault((node.session_id, event.call_id), []).append(event)
    for values in activities.values():
        values.sort(key=_event_sort_key)
    return activities


def _delivery_matches_source(event: Event, source: SessionNode) -> bool:
    hint = _message_source_hint(event.payload)
    if not hint:
        return True
    return hint in {source.session_id, source.agent_path, source.task_name}


def _target_matches(activity_target: str, target: Optional[SessionNode]) -> bool:
    if not activity_target or target is None:
        return True
    return activity_target in {target.session_id, target.agent_path, target.task_name, f"/root/{target.task_name}"}


def _plaintext_scope_violation(arguments: dict[str, Any], policy: dict[str, Any]) -> bool:
    if str(policy.get("write_boundary") or "") != "read_only":
        return False
    message = arguments.get("message")
    if not isinstance(message, str) or not message:
        return False
    lowered = message.lower()
    write_terms = (" edit ", " write ", " modify ", " mutate ", " change ", " patch ", " resolve ")
    padded = f" {lowered} "
    return any(term in padded for term in write_terms)


def _message_edges(
    roots: list[SessionNode],
    all_nodes: list[SessionNode],
    *,
    peer_message_policy: Optional[dict[str, Any]] = None,
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    lookup = _node_lookup(all_nodes)
    deliveries = _delivery_index(all_nodes)
    activities = _activity_index(all_nodes)
    policy = peer_message_policy if isinstance(peer_message_policy, dict) else {}
    edges: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    root_ids = {node.session_id for node in roots}
    for node in all_nodes:
        for event in node.events:
            if event.kind != "function_call" or event.name not in {"send_message", "followup_task"}:
                continue
            content = _message_content_from_arguments(event.arguments)
            if not content:
                continue
            target = _target_node(_message_target(event.arguments), lookup)
            digest, size = _message_digest(content)
            direction = _message_direction(node, target)
            activity_events = activities.get((node.session_id, event.call_id), [])
            activity_event = activity_events[-1] if activity_events else None
            activity_target = _activity_target(activity_event.payload) if activity_event else ""
            activity_target_node = _target_node(activity_target, lookup)
            target_mismatch = bool(activity_event and not _target_matches(activity_target, target))
            if activity_event:
                delivery_status = "target_mismatch" if target_mismatch else "delivered"
                delivery_evidence = "sub_agent_activity_interacted"
            else:
                delivered_events = [
                    item
                    for item in (deliveries.get((target.session_id, digest), []) if target else [])
                    if _delivery_matches_source(item, node)
                ]
                delivery_status = "delivered" if delivered_events else "not_observed"
                delivery_evidence = "receiver_agent_message_encrypted_content" if delivered_events else "none"
            edge = {
                "edge_id": f"message-edge-{len(edges) + 1}",
                "source_session_id": node.session_id,
                "target_session_id": target.session_id if target else None,
                "source_task_path": node.agent_path,
                "target_task_path": target.agent_path if target else _message_target(event.arguments),
                "source_agent_path": node.agent_path,
                "target_agent_path": target.agent_path if target else _message_target(event.arguments),
                "direction": direction,
                "tool_name": event.name,
                "tool_type": event.name,
                "order": event.sequence,
                "delivery_status": delivery_status,
                "delivery_evidence": delivery_evidence,
                "activity_target_task_path": activity_target or None,
                "activity_target_session_id": activity_target_node.session_id if activity_target_node else None,
                "target_mismatch": target_mismatch,
                "encrypted_content_sha256": digest,
                "encrypted_content_bytes": size,
                "payload_sha256": digest,
                "payload_bytes": size,
            }
            edges.append(edge)
            if _plaintext_scope_violation(event.arguments, policy):
                violations.append(
                    {
                        "violation_id": "peer_message_changed_write_boundary",
                        "source_session_id": node.session_id,
                        "target_session_id": target.session_id if target else None,
                        "source_task_path": node.agent_path,
                        "target_task_path": target.agent_path if target else _message_target(event.arguments),
                        "tool_name": event.name,
                        "order": event.sequence,
                    }
                )
    edges.sort(key=lambda item: int(item.get("order", 0) or 0))
    counts = {direction: 0 for direction in MESSAGE_EDGE_DIRECTIONS}
    for edge in edges:
        direction = str(edge.get("direction") or "unknown")
        counts[direction if direction in counts else "unknown"] += 1
    root_relay_count = sum(
        1
        for edge in edges
        if edge.get("direction") == "root_to_child" and edge.get("source_session_id") in root_ids
    )
    counts["root_relay_count"] = root_relay_count
    return edges, counts, violations


def _wait_output_succeeded(parent: SessionNode, call_id: str) -> bool:
    if not call_id:
        return False
    for event in parent.events:
        if event.kind != "function_call_output" or event.call_id != call_id:
            continue
        parsed = _parse_output(event.output)
        return parsed.get("timed_out") is False
    return False


def _event_sort_key(event: Event) -> tuple[datetime, int]:
    return (event.timestamp or datetime.max.replace(tzinfo=timezone.utc), event.sequence)


def _status_and_timing(node: SessionNode) -> tuple[str, dict[str, Any]]:
    lifecycle = [
        event
        for event in node.events
        if event.kind == "lifecycle" and event.name in {"task_started", "task_complete", "close_agent"}
    ]
    lifecycle.sort(key=_event_sort_key)
    latest = lifecycle[-1] if lifecycle else None
    status = "unknown"
    timing = {
        "source": "unknown",
        "duration_ms": None,
        "time_to_first_token_ms": None,
        "wall_clock_seconds": None,
    }
    if latest and latest.name == "task_complete":
        status = "completed"
        duration = latest.payload.get("duration_ms")
        first_token = latest.payload.get("time_to_first_token_ms")
        timing = {
            "source": "task_complete",
            "duration_ms": duration if isinstance(duration, int) else None,
            "time_to_first_token_ms": first_token if isinstance(first_token, int) else None,
            "wall_clock_seconds": round(duration / 1000, 3) if isinstance(duration, int) else None,
        }
    elif latest and latest.name == "close_agent":
        status = "completed_legacy_close"
        timing["source"] = "legacy_close_agent"
    elif latest and latest.name == "task_started":
        status = "open"
    return status, timing


def _active_interval(node: SessionNode) -> tuple[Optional[datetime], Optional[datetime]]:
    lifecycle = [event for event in node.events if event.kind == "lifecycle"]
    lifecycle.sort(key=_event_sort_key)
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    for event in lifecycle:
        if event.name == "task_started":
            start = event.timestamp
            end = None
        elif event.name in {"task_complete", "close_agent"} and start is not None:
            end = event.timestamp
    return start, end


def _count_descendant_events(node: SessionNode) -> dict[str, int]:
    counts = {name: 0 for name in TASK_TREE_TOOLS}
    for event in node.events:
        if event.kind == "function_call" and event.name in counts:
            counts[event.name] += 1
    for child in node.children:
        child_counts = _count_descendant_events(child)
        for name, value in child_counts.items():
            counts[name] += value
    return counts


def _count_own_events(nodes: Iterable[SessionNode]) -> dict[str, int]:
    counts = {name: 0 for name in TASK_TREE_TOOLS}
    for node in nodes:
        for event in node.events:
            if event.kind == "function_call" and event.name in counts:
                counts[event.name] += 1
    return counts


def _max_depth(node: SessionNode) -> int:
    if not node.children:
        return node.depth
    return max(_max_depth(child) for child in node.children)


def _descendants(node: SessionNode) -> Iterator[SessionNode]:
    for child in node.children:
        yield child
        yield from _descendants(child)


def _peak_active_descendants(node: SessionNode) -> int:
    points: list[tuple[datetime, int]] = []
    fallback_time = datetime.min.replace(tzinfo=timezone.utc)
    for descendant in _descendants(node):
        start, end = _active_interval(descendant)
        start_time = start or fallback_time
        points.append((start_time, 1))
        if end is not None:
            points.append((end, -1))
    active = 0
    peak = 0
    for _ts, delta in sorted(points, key=lambda item: (item[0], -item[1])):
        active += delta
        peak = max(peak, active)
    return peak


def _node_to_dict(node: SessionNode) -> dict[str, Any]:
    status, timing = _status_and_timing(node)
    direct_counts = {name: 0 for name in TASK_TREE_TOOLS}
    for event in node.events:
        if event.kind == "function_call" and event.name in direct_counts:
            direct_counts[event.name] += 1
    return {
        "session_id": node.session_id,
        "parent_thread_id": node.parent_thread_id or None,
        "task_name": node.task_name,
        "depth": node.depth,
        "max_depth": _max_depth(node),
        "agent_path": node.agent_path,
        "role": node.role,
        "agent_role": node.role,
        "role_source": node.role_source,
        "role_contract_id": node.role_contract_id,
        "configured_model": node.configured_model,
        "effective_model": node.effective_model,
        "configured_reasoning_effort": node.configured_reasoning_effort,
        "effective_reasoning_effort": node.effective_reasoning_effort,
        "spawn_contract": {
            "fork_turns": node.spawn_arguments.get("fork_turns"),
            "model": node.spawn_arguments.get("model"),
            "reasoning_effort": node.spawn_arguments.get("reasoning_effort"),
            "agent_type": node.spawn_arguments.get("agent_type"),
            "task_name": node.spawn_arguments.get("task_name"),
        },
        "spawn_sequence": node.spawn_sequence if node.spawn_sequence >= 0 else None,
        "status": status,
        "terminal_state": status,
        "spawned": direct_counts["spawn_agent"],
        "followups": direct_counts["followup_task"],
        "result_consumed": node.result_consumed,
        "own_counts": direct_counts,
        "counts": _count_descendant_events(node),
        "peak_active_children": _peak_active_descendants(node),
        "timing": timing,
        "token_usage": dict(node.token_usage),
        "own_token_usage": dict(node.token_usage),
        "children": [_node_to_dict(child) for child in sorted(node.children, key=lambda item: item.agent_path or item.session_id)],
    }


def _sum_usage(nodes: Iterable[SessionNode]) -> dict[str, int]:
    totals = dict(ZERO_USAGE)
    for node in nodes:
        for key in TOKEN_KEYS:
            totals[key] += int(node.token_usage.get(key, 0) or 0)
    return totals


def _flatten(nodes: Iterable[SessionNode]) -> Iterator[SessionNode]:
    for node in nodes:
        yield node
        yield from _flatten(node.children)


def analyze_session_tree(
    paths: Iterable[Path | str],
    *,
    agents_root: Optional[Path | str] = None,
    peer_message_policy: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    path_list = sorted((Path(path) for path in paths), key=lambda item: str(item))
    nodes: dict[str, SessionNode] = {}
    file_node: dict[str, SessionNode] = {}
    pending_outputs: dict[str, tuple[SessionNode, dict[str, Any], int]] = {}
    path_parent: dict[str, str] = {}
    ambiguous_spawn_paths: set[str] = set()
    path_spawn_arguments: dict[tuple[str, str], dict[str, Any]] = {}
    path_spawn_sequence: dict[tuple[str, str], int] = {}
    legacy_agent_parent: dict[str, str] = {}
    legacy_spawn_arguments: dict[str, dict[str, Any]] = {}
    legacy_spawn_sequence: dict[str, int] = {}
    sequence = 0
    contracts = _role_contracts(Path(agents_root) if agents_root is not None else None)
    session_started_at: dict[str, datetime] = {}
    seen_records: set[str] = set()
    record_stats = {
        "raw_records": 0,
        "fork_history_records_skipped": 0,
        "duplicate_records_skipped": 0,
    }

    for path, _line_no, record in iter_jsonl_records(path_list):
        record_stats["raw_records"] += 1
        if record is None:
            continue
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        file_id = _session_id_from_path(path)
        source_key = str(path.resolve())
        current = file_node.get(source_key)
        record_type = str(record.get("type") or payload.get("type") or "")
        if record_type == "session_meta":
            meta = _meta_fields(payload)
            meta_id = meta["session_id"]
            if file_id and meta_id and meta_id != file_id:
                continue
            session_id = meta_id or file_id or path.stem
            current = nodes.setdefault(session_id, SessionNode(session_id=session_id, source_file=str(path)))
            file_node[source_key] = current
            if meta["parent_thread_id"]:
                current.parent_thread_id = meta["parent_thread_id"]
            current.depth = meta["depth"]
            if meta["agent_path"]:
                current.agent_path = meta["agent_path"]
            if meta["role"]:
                current.role = meta["role"]
            meta_ts = parse_timestamp(record.get("timestamp") or record.get("created_at") or record.get("event_time"))
            if meta_ts is not None:
                session_started_at[source_key] = meta_ts
            continue

        record_ts = parse_timestamp(record.get("created_at") or record.get("event_time") or record.get("timestamp"))
        started_at = session_started_at.get(source_key)
        if record_ts is not None and started_at is not None and record_ts < started_at:
            record_stats["fork_history_records_skipped"] += 1
            continue
        fingerprint = _record_fingerprint(record)
        if fingerprint in seen_records:
            record_stats["duplicate_records_skipped"] += 1
            continue
        seen_records.add(fingerprint)

        session_id = str(
            record.get("session_id")
            or (current.session_id if current else "")
            or file_id
            or path.stem
        )
        current = nodes.setdefault(session_id, SessionNode(session_id=session_id, source_file=str(path)))
        file_node[source_key] = current
        model, effort = _turn_context_model_effort(record, payload)
        if model:
            current.effective_model = model
        if effort:
            current.effective_reasoning_effort = effort
        if model or effort:
            sequence += 1
            continue
        ts = parse_timestamp(record.get("created_at") or record.get("event_time") or record.get("timestamp"))
        payload_type = str(payload.get("type") or "")
        event_payload = payload if isinstance(payload, dict) else {}

        if record_type == "event_msg":
            name = str(payload.get("type") or "")
            if name in {"task_started", "task_complete"}:
                current.events.append(Event(ts, sequence, "lifecycle", name=name, payload=dict(event_payload)))
            elif name == "token_count":
                usage = _extract_token_usage(payload)
                current.token_usage = usage
                current.token_sequence = sequence
                current.events.append(Event(ts, sequence, "token_count", name=name, payload=dict(event_payload)))
            elif name == "sub_agent_activity" and str(payload.get("kind") or "") == "interacted":
                current.events.append(
                    Event(
                        ts,
                        sequence,
                        "sub_agent_activity",
                        name="interacted",
                        call_id=_activity_call_id(payload),
                        payload=dict(event_payload),
                    )
                )
        elif payload_type == "function_call":
            name = _normalize_tool_name(payload.get("name"))
            args = _parse_arguments(payload.get("arguments") or payload.get("input"))
            call_id = str(payload.get("call_id") or "")
            current.events.append(Event(ts, sequence, "function_call", name=name, call_id=call_id, arguments=args))
            if name == "spawn_agent" and call_id:
                pending_outputs[call_id] = (current, args, sequence)
        elif payload_type == "function_call_output":
            call_id = str(payload.get("call_id") or "")
            output_text = str(payload.get("output") or payload.get("result") or payload.get("content") or "")
            current.events.append(Event(ts, sequence, "function_call_output", call_id=call_id, output=output_text))
            if call_id in pending_outputs:
                parent, args, spawn_sequence = pending_outputs[call_id]
                parsed = _parse_output(output_text)
                task_name = str(parsed.get("task_name") or "")
                if task_name:
                    if not task_name.startswith("/"):
                        base = parent.agent_path or "/root"
                        task_name = f"{base.rstrip('/')}/{task_name}"
                    if task_name in path_parent and path_parent[task_name] != parent.session_id:
                        path_parent.pop(task_name)
                        ambiguous_spawn_paths.add(task_name)
                    elif task_name not in ambiguous_spawn_paths:
                        path_parent[task_name] = parent.session_id
                    path_spawn_arguments[(parent.session_id, task_name)] = dict(args)
                    path_spawn_sequence[(parent.session_id, task_name)] = spawn_sequence
                legacy_id = str(parsed.get("agent_id") or parsed.get("id") or "")
                if legacy_id:
                    legacy_agent_parent[legacy_id] = parent.session_id
                    legacy_spawn_arguments[legacy_id] = dict(args)
                    legacy_spawn_sequence[legacy_id] = spawn_sequence
        elif payload_type == "agent_message":
            current.events.append(Event(ts, sequence, "agent_message", name="agent_message", payload=dict(event_payload)))
        sequence += 1

    for node in nodes.values():
        if not node.parent_thread_id:
            node.parent_thread_id = path_parent.get(node.agent_path, "") or legacy_agent_parent.get(
                node.session_id, ""
            )
        if node.agent_path:
            node.task_name = Path(node.agent_path.rstrip("/")).name or "root"
        spawn_key = (node.parent_thread_id, node.agent_path)
        if node.parent_thread_id and spawn_key in path_spawn_arguments:
            node.spawn_arguments = dict(path_spawn_arguments[spawn_key])
            node.spawn_sequence = path_spawn_sequence.get(spawn_key, -1)
        elif node.session_id in legacy_spawn_arguments:
            node.spawn_arguments = dict(legacy_spawn_arguments[node.session_id])
            node.spawn_sequence = legacy_spawn_sequence.get(node.session_id, -1)
        if not node.agent_path:
            node.agent_path = (
                "/root"
                if not node.parent_thread_id and node.depth == 0
                else f"/unknown/{node.session_id}"
            )
        if not node.role:
            role_from_path = Path(node.agent_path).name.replace("-", "_")
            true_root = (
                not node.parent_thread_id
                and node.depth == 0
                and node.agent_path.rstrip("/") in {"", "/root"}
            )
            if true_root:
                node.role = "root"
            elif role_from_path in KNOWN_GENERIC_ROLES:
                node.role = role_from_path
            else:
                node.role = "unknown"
        if not node.task_name:
            node.task_name = Path(node.agent_path.rstrip("/")).name or "root"
        if not node.parent_thread_id and node.depth == 0 and node.agent_path.rstrip("/") in {"", "/root"}:
            node.role_source = "root"
            node.role_contract_id = "root"
        else:
            requested_type = str(node.spawn_arguments.get("agent_type") or "")
            # Codex omits an optional agent_type when it spawns the built-in default role.
            # Only infer that default when this node is tied to a concrete spawn call.
            if not requested_type and node.spawn_arguments and node.role == "unknown":
                requested_type = "default"
            if requested_type and requested_type in contracts:
                # A requested template is not evidence that Codex injected it at runtime.
                node.role_source = "requested_template"
                node.role_contract_id = contracts[requested_type]["role_contract_id"]
                node.configured_model = str(node.spawn_arguments.get("model") or "unknown")
                node.configured_reasoning_effort = str(node.spawn_arguments.get("reasoning_effort") or "unknown")
            elif requested_type in KNOWN_BUILTIN_ROLES:
                node.role_source = "builtin"
                if requested_type == "default":
                    node.role = "default"
                    node.role_contract_id = "subagent-role-v4.2/default"
            else:
                node.role_source = "unknown"
            if node.configured_model == "unknown" and isinstance(node.spawn_arguments.get("model"), str):
                node.configured_model = str(node.spawn_arguments["model"])
            if node.configured_reasoning_effort == "unknown" and isinstance(
                node.spawn_arguments.get("reasoning_effort"), str
            ):
                node.configured_reasoning_effort = str(node.spawn_arguments["reasoning_effort"])

    for node in nodes.values():
        if node.parent_thread_id and node.parent_thread_id in nodes and node.parent_thread_id != node.session_id:
            parent = nodes[node.parent_thread_id]
            if node not in parent.children:
                parent.children.append(node)

    for node in nodes.values():
        if not node.parent_thread_id or node.parent_thread_id not in nodes:
            continue
        parent = nodes[node.parent_thread_id]
        targets = {node.agent_path, node.task_name, f"/root/{node.task_name}"}
        direct_children = list(parent.children)
        terminal_direct_children = [
            child for child in direct_children if _status_and_timing(child)[0].startswith("completed")
        ]
        can_attribute_unscoped_wait = (
            len(direct_children) == 1
            and len(terminal_direct_children) == 1
            and terminal_direct_children[0] is node
        )
        for event in parent.events:
            if event.kind != "function_call" or event.name != "wait_agent":
                continue
            values = _wait_target_values(event.arguments)
            if values & targets:
                node.result_consumed = True
                break
            if not values and can_attribute_unscoped_wait and _wait_output_succeeded(parent, event.call_id):
                node.result_consumed = True
                break

    roots = [node for node in nodes.values() if not node.parent_thread_id or node.parent_thread_id not in nodes]
    roots.sort(key=lambda item: item.agent_path or item.session_id)
    all_nodes = list(nodes.values())
    root_dicts = [_node_to_dict(node) for node in roots]
    non_roots = [node for node in all_nodes if node not in roots]
    message_edges, message_edge_counts, scope_violations = _message_edges(
        roots,
        all_nodes,
        peer_message_policy=peer_message_policy,
    )
    completed_children = 0
    open_children = 0
    for node in non_roots:
        status, _timing = _status_and_timing(node)
        if status.startswith("completed"):
            completed_children += 1
        else:
            open_children += 1

    aggregate_counts = {name: 0 for name in TASK_TREE_TOOLS}
    for root in roots:
        counts = _count_descendant_events(root)
        for name, value in counts.items():
            aggregate_counts[name] += value
    aggregate = {
        "roots": len(roots),
        "sessions": len(all_nodes),
        "max_depth": max((_max_depth(node) for node in roots), default=0),
        "peak_active_children": max((_peak_active_descendants(node) for node in roots), default=0),
        "open_children": open_children,
        "completed_children": completed_children,
        "counts": aggregate_counts,
        "root_counts": _count_own_events(roots),
        "child_counts": _count_own_events(non_roots),
        "token_usage": _sum_usage(all_nodes),
        "root_token_usage": _sum_usage(roots),
        "child_token_usage": _sum_usage(non_roots),
        "root_wall_clock_seconds": [
            _status_and_timing(node)[1].get("wall_clock_seconds") for node in roots
        ],
        "child_wall_clock_seconds": [
            _status_and_timing(node)[1].get("wall_clock_seconds") for node in non_roots
        ],
        "message_edges": {direction: message_edge_counts[direction] for direction in MESSAGE_EDGE_DIRECTIONS},
        "root_relay_count": message_edge_counts["root_relay_count"],
        "scope_violation_count": len(scope_violations),
    }
    return {
        "schema_version": "task-tree-v1",
        "record_normalization": record_stats,
        "roots": root_dicts,
        "aggregate": aggregate,
        "message_edges": message_edges,
        "scope_violations": scope_violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Codex task trees from session JSONL.")
    parser.add_argument("paths", nargs="+", help="Session JSONL files")
    parser.add_argument("--agents-root", help="Agents directory used by the analyzed runtime")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    report = analyze_session_tree(
        [Path(path) for path in args.paths],
        agents_root=Path(args.agents_root) if args.agents_root else None,
    )
    print(json.dumps(report, indent=2 if args.pretty else None, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
