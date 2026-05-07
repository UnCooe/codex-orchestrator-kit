#!/usr/bin/env python3
"""Generate dedicated subagent workflow reports from orchestrator observability facts."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from session_facts import (
    extract_facts,
    fact_to_dict,
    scan_observability_event_files,
    scan_session_files,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Generate subagent workflow reports.")
    parser.add_argument("--codex-home", default=str(Path.home() / ".codex"), help="Path to CODEX_HOME")
    parser.add_argument("--since-days", type=int, default=14, help="Window in days for event filtering")
    parser.add_argument("--report-id", default=None, help="Optional report filename suffix")
    parser.add_argument(
        "--reports-dir",
        default=None,
        help="Optional custom reports directory (defaults under CODEX_HOME/orchestrator-observability/reports)",
    )
    parser.add_argument(
        "--include-paths",
        action="store_true",
        help="Include full local paths in JSON and Markdown reports. Default redacts paths.",
    )
    parser.add_argument("--max-samples", type=int, default=20, help="Maximum sample events for reports")
    return parser.parse_args()


def _project_name(cwd: str) -> str:
    if not cwd or cwd == "unknown":
        return "unknown"
    try:
        return Path(cwd).name
    except Exception:
        return cwd


def _cwd_label(cwd: str, include_paths: bool) -> str:
    if include_paths:
        return cwd
    project = _project_name(cwd)
    if project == "unknown":
        return "unknown"
    return f"project:{project}"


_CONTEXT_SECRET_RE = re.compile(
    r"(?i)(api[-_]?(?:key|token)|token|secret|password|bearer)\s*[:=]\s*([^\s,;]+)"
)
_OPENAI_KEY_RE = re.compile(r"(?i)\bsk-[A-Za-z0-9]{20,}\b")


def _redact_context(text: str) -> str:
    redacted = _CONTEXT_SECRET_RE.sub(r"\1=[redacted]", text)
    return _OPENAI_KEY_RE.sub("[redacted]", redacted)


def _project_context_summary(cwd: str, include_paths: bool) -> Dict[str, object]:
    if not cwd or cwd == "unknown":
        return {"available": False, "source": "", "summary": ""}

    try:
        root = Path(cwd)
    except Exception:
        return {"available": False, "source": "invalid_cwd", "summary": ""}

    context_path = root / ".codex" / "project-context.md"
    if not context_path.exists():
        source = str(context_path).replace("\\", "/")
        if not include_paths and root.name:
            source = f"project:{root.name}/.codex/project-context.md"
        return {"available": False, "source": source, "summary": ""}

    try:
        text = context_path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        source = str(context_path).replace("\\", "/")
        if not include_paths and root.name:
            source = f"project:{root.name}/.codex/project-context.md"
        return {"available": False, "source": source, "summary": ""}

    normalized = " ".join(_redact_context(text).replace("\r", "").replace("\n", " ").split())
    if len(normalized) > 240:
        normalized = normalized[:237] + "..."

    source = str(context_path).replace("\\", "/")
    if not include_paths and root.name:
        source = f"project:{root.name}/.codex/project-context.md"

    return {"available": True, "source": source, "summary": normalized}


def _safe_divide(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return round(num / den, 4)


def _action_policy(unclosed_delta: int, broad_scan_bypasses: int) -> str:
    if unclosed_delta > 0 or broad_scan_bypasses > 0:
        return "triage"
    return "observe"


def build_report(
    codex_home: Path,
    since_days: int,
    report_id: Optional[str],
    max_samples: int,
    reports_dir: Optional[str],
    include_paths: bool = False,
) -> Dict:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=since_days)

    event_files = list(scan_session_files(codex_home, cutoff=cutoff)) + list(scan_observability_event_files(codex_home))
    facts, fact_stats = extract_facts(event_files, codex_home, cutoff)

    totals = defaultdict(int, fact_stats)
    event_breakdown = Counter()
    subagents = Counter()
    tool_calls = Counter()
    sessions = set()
    samples: List[Dict] = []
    projects = Counter()
    cwd_activity = Counter()
    project_context = {}
    per_session_subagents = defaultdict(lambda: {"spawn_agent": 0, "wait_agent": 0, "close_agent": 0})
    heavy_work_delegation_signals = 0
    broad_scan_bypasses = 0
    broad_scan_blocks = 0

    for row in facts:
        row_dict = row if isinstance(row, dict) else row.__dict__
        totals["total_lines"] += 1
        session_id = str(row_dict.get("session_id") or "")
        if session_id:
            sessions.add(session_id)

        event_type = str(row_dict.get("event_type") or "")
        status = str(row_dict.get("status", "unknown")).lower()
        project = _project_name(str(row_dict.get("cwd") or "unknown"))
        projects[project] += 1
        cwd_label = _cwd_label(str(row_dict.get("cwd") or "unknown"), include_paths)
        cwd_activity[cwd_label] += 1

        if cwd_label not in project_context and str(row_dict.get("cwd") or ""):
            project_context[cwd_label] = _project_context_summary(str(row_dict.get("cwd")), include_paths)

        event_breakdown[event_type] += 1
        if event_type.startswith("transcript_subagent_"):
            subagents[event_type.replace("transcript_subagent_", "")] += 1
            if session_id:
                if event_type == "transcript_subagent_spawn_agent":
                    per_session_subagents[session_id]["spawn_agent"] += 1
                elif event_type == "transcript_subagent_wait_agent":
                    per_session_subagents[session_id]["wait_agent"] += 1
                elif event_type == "transcript_subagent_close_agent":
                    per_session_subagents[session_id]["close_agent"] += 1

        if event_type in {"transcript_function_call", "transcript_custom_tool_call"}:
            tool_calls[str(row_dict.get("tool_name") or row_dict.get("normalized_tool_name") or "unknown")] += 1

        if event_type == "transcript_token_count":
            totals["token_count_events"] += 1

        if event_type == "orchestrator_user_prompt" and status == "warn":
            heavy_work_delegation_signals += 1

        if event_type == "orchestrator_pre_tool_use" and status == "blocked" and str(row_dict.get("reason")) == "broad_scan_detected":
            broad_scan_blocks += 1
        if (
            event_type == "orchestrator_pre_tool_use"
            and status == "allow"
            and str(row_dict.get("reason")) == "delegation_signal_detected"
            and str(row_dict.get("tool_family")) == "shell_like"
        ):
            broad_scan_bypasses += 1

        if len(samples) < max_samples:
            samples.append(
                {
                    "file": row_dict.get("source_file"),
                    "line": row_dict.get("source_line"),
                    "kind": event_type,
                    "status": status,
                    "created_at": row_dict.get("timestamp"),
                    "project": project,
                }
            )

    if not report_id:
        report_id = now.strftime("%Y%m%dT%H%M%SZ")

    reports_root = Path(reports_dir) if reports_dir else (codex_home / "orchestrator-observability" / "reports")
    reports_root.mkdir(parents=True, exist_ok=True)

    spawn_count = subagents.get("spawn_agent", 0)
    wait_count = subagents.get("wait_agent", 0)
    close_count = subagents.get("close_agent", 0)
    unclosed_delta = spawn_count - close_count
    total_tool_calls = max(1, tool_calls.total())
    delegation_rate = _safe_divide(spawn_count, total_tool_calls)
    total_heavy_signals = max(1, heavy_work_delegation_signals)
    heavy_signal_delegation_rate = _safe_divide(spawn_count, total_heavy_signals)

    open_session_count = 0
    for stats in per_session_subagents.values():
        if stats["spawn_agent"] > stats["close_agent"]:
            open_session_count += 1
    action_policy = _action_policy(unclosed_delta, broad_scan_bypasses)
    project_context_available = any(
        isinstance(item, dict) and bool(item.get("available")) for item in project_context.values()
    )

    facts_for_report = []
    for fact in facts:
        row = fact_to_dict(fact)
        row["cwd"] = _cwd_label(str(row.get("cwd") or "unknown"), include_paths)
        facts_for_report.append(row)

    data = {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "codex_home": str(codex_home) if include_paths else "[redacted]",
        "since_days": since_days,
        "paths_included": include_paths,
        "report_type": "subagent_workflow",
        "reviewer_id": "subagent-workflow",
        "standard": "default-v1",
        "action_policy": action_policy,
        "project_context_available": project_context_available,
        "fact_count": len(facts),
        "totals": dict(totals),
        "window": {
            "from": cutoff.isoformat().replace("+00:00", "Z"),
            "to": now.isoformat().replace("+00:00", "Z"),
        },
        "sessions_seen": len(sessions),
        "projects": dict(projects),
        "cwds": dict(cwd_activity),
        "event_breakdown": dict(event_breakdown),
        "subagents": {
            "spawn": spawn_count,
            "wait": wait_count,
            "close": close_count,
            "unclosed_delta": unclosed_delta,
            "open_sessions": open_session_count,
            "lifecycle_delta_spawn_minus_close": unclosed_delta,
            "spawn_per_tool_call": delegation_rate,
            "spawn_per_heavy_signal": heavy_signal_delegation_rate,
        },
        "workflow_signals": {
            "delegation_rate": delegation_rate,
            "heavy_work_delegation_rate": heavy_signal_delegation_rate,
            "heavy_work_delegation_signals": heavy_work_delegation_signals,
            "broad_scan_blocks": broad_scan_blocks,
            "broad_scan_bypasses": broad_scan_bypasses,
        },
        "tool_calls": dict(tool_calls),
        "project_context": project_context,
        "samples": samples,
        "facts": facts_for_report,
    }

    json_path = reports_root / f"subagent-workflow-{report_id}.json"
    with json_path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)

    markdown_lines = [
        "# Subagent Workflow Report",
        "",
        "- Reviewer: `subagent-workflow`",
        "- Standard: `default-v1`",
        f"- Action policy: `{action_policy}`",
        f"- Project context available: {str(project_context_available).lower()}",
        f"- Scope: {codex_home if include_paths else '[redacted]'}",
        f"- Window: last {since_days} day(s)",
        f"- Report time: {data['generated_at']}",
        "",
        f"- Total lines: {totals.get('total_lines', 0)}",
        f"- Invalid lines: {totals.get('invalid_lines', 0)}",
        f"- Filtered as older: {totals.get('filtered_older', 0)}",
        f"- Sessions seen: {len(sessions)}",
        "",
        "## Subagent lifecycle",
        f"- Spawn/Wait/Close: {spawn_count}/{wait_count}/{close_count}",
        f"- Unclosed delta: {unclosed_delta}",
        f"- Open sessions with unclosed subagents: {open_session_count}",
        "",
        "## Delegation signals",
        f"- Delegation rate (spawn / tool calls): {delegation_rate}",
        f"- Delegation rate (spawn / heavy-work signals): {heavy_signal_delegation_rate}",
        f"- Heavy-work delegation signals: {heavy_work_delegation_signals}",
        f"- Broad-scan blocks: {broad_scan_blocks}",
        f"- Broad-scan bypass signals (observed): {broad_scan_bypasses}",
        "",
        "## Project context",
    ]

    for key in sorted(project_context):
        item = project_context[key]
        if isinstance(item, dict) and item.get("available"):
            markdown_lines.append(f"- {key}: available ({item.get('source', '')})")
        else:
            markdown_lines.append(f"- {key}: unavailable")

    markdown_lines.append("")
    markdown_lines.append("## Top subagent tooling")
    for name, value in subagents.most_common():
        markdown_lines.append(f"- {name}: {value}")

    if samples:
        markdown_lines.append("")
        markdown_lines.append("## Samples")
        for sample in samples:
            markdown_lines.append(
                f"- {sample['created_at']} ({sample['kind']}): {sample['status']} in {sample['project']}"
            )

    md_path = reports_root / f"subagent-workflow-{report_id}.md"
    md_path.write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")

    return {
        "json_report": str(json_path),
        "markdown_report": str(md_path),
        "totals": dict(totals),
        "subagents": data["subagents"],
        "workflow_signals": data["workflow_signals"],
    }


if __name__ == "__main__":
    args = parse_args()
    output = build_report(
        Path(args.codex_home).expanduser(),
        args.since_days,
        args.report_id,
        args.max_samples,
        args.reports_dir,
        args.include_paths,
    )
    print(json.dumps(output, indent=2, ensure_ascii=False))
