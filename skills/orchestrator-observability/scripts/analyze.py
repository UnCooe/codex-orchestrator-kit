#!/usr/bin/env python3
"""Offline analyzer for orchestrator-observability telemetry."""

from __future__ import annotations

import argparse
import json
import re
import sys
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
from task_tree import analyze_session_tree


MAX_EMBEDDED_FACTS = 2000


def parse_args():
    parser = argparse.ArgumentParser(description="Analyze orchestrator-observability telemetry.")
    parser.add_argument("--codex-home", default=str(Path.home() / ".codex"), help="Path to CODEX home")
    parser.add_argument("--since-days", type=int, default=14, help="Window in days for event filtering")
    parser.add_argument("--report-id", default=None, help="Optional report filename suffix")
    parser.add_argument(
        "--reports-dir",
        default=None,
        help="Optional custom reports directory (defaults under CODEX_HOME/orchestrator-observability/reports)",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=20,
        help="Maximum sample events for reports",
    )
    parser.add_argument(
        "--include-paths",
        action="store_true",
        help="Include full local codex_home/cwd paths in JSON and Markdown reports. Default redacts paths.",
    )
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


def _date_bucket(timestamp: Optional[str]) -> str:
    if not timestamp:
        return "unknown"
    if "T" not in timestamp:
        return timestamp
    return timestamp.split("T", 1)[0]


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

    candidates = [
        (root / ".codex" / "project-context.md", ".codex/project-context.md"),
        (root / "AGENTS.md", "AGENTS.md"),
    ]
    context_path = None
    display_source = ""
    for path, label in candidates:
        if path.exists():
            context_path = path
            display_source = str(path).replace("\\", "/") if include_paths else f"project:{root.name}/{label}"
            break

    if context_path is None:
        display_source = f"project:{root.name}/.codex/project-context.md" if not include_paths and root.name else str(candidates[0][0]).replace("\\", "/")
        return {"available": False, "source": display_source, "summary": ""}

    try:
        text = context_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {"available": False, "source": display_source, "summary": ""}

    normalized = " ".join(_redact_context(text).strip().replace("\r", "").replace("\n", " ").split())
    if not normalized:
        return {"available": True, "source": display_source, "summary": ""}
    if len(normalized) > 240:
        normalized = normalized[:237] + "..."
    return {"available": True, "source": display_source, "summary": normalized}


def _build_suggestions(
    totals: Dict[str, int],
    acceptance: Dict[str, int],
    noise: Dict[str, int],
    subagents: Dict[str, int],
    event_breakdown: Dict[str, int],
    sessions_seen: int,
) -> List[str]:
    suggestions: List[str] = []
    blocked = totals.get("blocked_orchestrator_events", 0)
    orchestrator_total = totals.get("orchestrator_events", 0)

    if orchestrator_total and blocked >= max(1, orchestrator_total // 5):
        suggestions.append("Consider reviewing matcher coverage for frequently blocked orchestrator hooks.")
    if acceptance.get("work_done_missing_verification", 0) > 0:
        suggestions.append("Require explicit verification in final assistant messages before Stop.")
    if acceptance.get("work_done_missing_subagent_status", 0) > 0:
        suggestions.append("Require subagent lifecycle status in final assistant messages when subagents were used.")
    if acceptance.get("work_done_missing_acceptance_signal", 0) > 0:
        suggestions.append("Require a user-visible verification summary before Stop; do not require a machine-only footer.")
    if acceptance.get("acceptance_footer_partial", 0) > 0:
        suggestions.append("Acceptance footers are present but incomplete; fill all four footer fields.")
    if acceptance.get("structured_acceptance_missing_verification", 0) > 0:
        suggestions.append("Structured acceptance from stop hook reports verification was requested but not present.")
    if acceptance.get("footer_subagent_fail", 0) > 0:
        suggestions.append("Subagent lifecycle text and subagent_status disagree in the acceptance footer.")
    if subagents.get("lifecycle_delta_spawn_minus_close", 0) > 0:
        suggestions.append("Legacy close_agent-derived spawn/close delta is positive; use task_tree.open_children for current terminal state.")
    if noise.get("large_tool_outputs", 0) > 0:
        suggestions.append("Reduce large tool outputs by enabling summaries or truncation.")
    if event_breakdown.get("transcript_message_assistant", 0) > 0 and sessions_seen == 0:
        suggestions.append("Assistants are visible but no sessions were detected; verify session id extraction.")
    if not suggestions:
        suggestions.append("No critical suggestions generated from the selected window.")

    return suggestions


def _safe_get_int(source: Dict[str, object], key: str) -> int:
    try:
        return int(source.get(key, 0) or 0)
    except Exception:
        return 0


def _load_previous_report(reports_root: Path, report_id: Optional[str]) -> Optional[Dict[str, object]]:
    candidates = []
    for path in reports_root.glob("orchestrator-observability-*.json"):
        if report_id and path.name == f"orchestrator-observability-{report_id}.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        generated_at = payload.get("generated_at")
        candidates.append((str(generated_at or ""), path.stat().st_mtime, payload))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _compare_metric(current: int, previous: Optional[int], *, bigger_is_worse: bool = True) -> Dict[str, object]:
    if previous is None:
        return {"current": current, "previous": None, "delta": None, "status": "first_observation"}
    delta = current - previous
    if delta == 0:
        status = "unchanged"
    elif bigger_is_worse:
        status = "worsened" if delta > 0 else "improved"
    else:
        status = "improved" if delta > 0 else "worsened"
    return {"current": current, "previous": previous, "delta": delta, "status": status}


def _build_trend_summary(current_report: Dict[str, object], previous_report: Optional[Dict[str, object]]) -> Dict[str, object]:
    current_totals = current_report.get("totals", {}) if isinstance(current_report.get("totals"), dict) else {}
    current_subagents = current_report.get("subagents", {}) if isinstance(current_report.get("subagents"), dict) else {}
    current_acceptance = current_report.get("acceptance", {}) if isinstance(current_report.get("acceptance"), dict) else {}

    previous_totals = previous_report.get("totals", {}) if previous_report and isinstance(previous_report.get("totals"), dict) else {}
    previous_subagents = previous_report.get("subagents", {}) if previous_report and isinstance(previous_report.get("subagents"), dict) else {}
    previous_acceptance = previous_report.get("acceptance", {}) if previous_report and isinstance(previous_report.get("acceptance"), dict) else {}

    current_orchestrator = _safe_get_int(current_totals, "orchestrator_events")
    previous_orchestrator = _safe_get_int(previous_totals, "orchestrator_events") if previous_report else None
    current_blocked_ratio = round(
        (_safe_get_int(current_totals, "blocked_orchestrator_events") / current_orchestrator) if current_orchestrator else 0.0,
        4,
    )
    previous_blocked_ratio = None
    if previous_report:
        prev_total = _safe_get_int(previous_totals, "orchestrator_events")
        previous_blocked_ratio = round(
            (_safe_get_int(previous_totals, "blocked_orchestrator_events") / prev_total) if prev_total else 0.0,
            4,
        )

    metrics = {
        "blocked_ratio_x10000": _compare_metric(
            int(current_blocked_ratio * 10000),
            int(previous_blocked_ratio * 10000) if previous_blocked_ratio is not None else None,
        ),
        "lifecycle_delta_spawn_minus_close": _compare_metric(
            _safe_get_int(current_subagents, "lifecycle_delta_spawn_minus_close"),
            _safe_get_int(previous_subagents, "lifecycle_delta_spawn_minus_close") if previous_report else None,
        ),
        "work_done_missing_verification": _compare_metric(
            _safe_get_int(current_acceptance, "work_done_missing_verification"),
            _safe_get_int(previous_acceptance, "work_done_missing_verification") if previous_report else None,
        ),
        "work_done_missing_subagent_status": _compare_metric(
            _safe_get_int(current_acceptance, "work_done_missing_subagent_status"),
            _safe_get_int(previous_acceptance, "work_done_missing_subagent_status") if previous_report else None,
        ),
        "work_done_missing_acceptance_footer": _compare_metric(
            _safe_get_int(current_acceptance, "work_done_missing_acceptance_footer"),
            _safe_get_int(previous_acceptance, "work_done_missing_acceptance_footer") if previous_report else None,
        ),
        "work_done_missing_acceptance_signal": _compare_metric(
            _safe_get_int(current_acceptance, "work_done_missing_acceptance_signal"),
            _safe_get_int(previous_acceptance, "work_done_missing_acceptance_signal") if previous_report else None,
        ),
        "acceptance_footer_partial": _compare_metric(
            _safe_get_int(current_acceptance, "acceptance_footer_partial"),
            _safe_get_int(previous_acceptance, "acceptance_footer_partial") if previous_report else None,
        ),
    }

    worsened = [name for name, item in metrics.items() if item["status"] == "worsened"]
    improved = [name for name, item in metrics.items() if item["status"] == "improved"]

    if previous_report is None:
        overall = "first_run"
    elif worsened and improved:
        overall = "mixed"
    elif worsened:
        overall = "worsening"
    elif improved:
        overall = "improving"
    else:
        overall = "stable"

    signals = []
    for name, item in metrics.items():
        if name == "blocked_ratio_x10000":
            current_value = current_blocked_ratio
            previous_value = previous_blocked_ratio
        else:
            current_value = item["current"]
            previous_value = item["previous"]
        signals.append(
            {
                "metric": name,
                "status": item["status"],
                "current": current_value,
                "previous": previous_value,
                "delta": item["delta"] if name != "blocked_ratio_x10000" else (
                    None if previous_value is None else round(current_value - previous_value, 4)
                ),
            }
        )

    return {
        "baseline_report": str(previous_report.get("report_name") or previous_report.get("generated_at") or "") if previous_report else "",
        "overall": overall,
        "signals": signals,
    }


def _append_daily_summary(markdown_lines: List[str], daily: Dict[str, int], since_days: int) -> None:
    markdown_lines.append(f"## Daily activity ({since_days}-day-friendly summary)")
    if not daily:
        markdown_lines.append("- No daily events in window.")
        return

    for date, count in sorted(daily.items(), reverse=True):
        markdown_lines.append(f"- {date}: {count}")


def _safe_ratio(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return round(num / den, 4)


def _footer_adoption_status(coverage: float) -> str:
    if coverage >= 0.8:
        return "healthy"
    if coverage >= 0.4:
        return "partial"
    return "weak"


def _build_footer_adoption_summary(acceptance: Dict[str, int], trend: Dict[str, object]) -> Dict[str, object]:
    work_done = _safe_get_int(acceptance, "work_done_messages")
    complete = _safe_get_int(acceptance, "acceptance_footer_complete")
    partial = _safe_get_int(acceptance, "acceptance_footer_partial")
    missing = _safe_get_int(acceptance, "work_done_missing_acceptance_footer")
    coverage = _safe_ratio(complete, work_done)
    partial_rate = _safe_ratio(partial, work_done)
    missing_rate = _safe_ratio(missing, work_done)
    trend_signals = trend.get("signals", []) if isinstance(trend, dict) else []
    footer_signal = next(
        (item for item in trend_signals if isinstance(item, dict) and item.get("metric") == "work_done_missing_acceptance_footer"),
        {},
    )
    summary = (
        f"footer_adoption={complete}/{work_done} ({coverage}); "
        f"partial={partial} ({partial_rate}); missing={missing} ({missing_rate})"
    )
    return {
        "work_done_messages": work_done,
        "complete": complete,
        "partial": partial,
        "missing": missing,
        "coverage_rate": coverage,
        "partial_rate": partial_rate,
        "missing_rate": missing_rate,
        "status": _footer_adoption_status(coverage),
        "trend_status": str(footer_signal.get("status") or "unknown"),
        "summary": summary,
    }


def _build_acceptance_signal_summary(acceptance: Dict[str, int], trend: Dict[str, object]) -> Dict[str, object]:
    work_done = _safe_get_int(acceptance, "work_done_messages")
    complete = _safe_get_int(acceptance, "work_done_acceptance_signal_complete")
    missing = _safe_get_int(acceptance, "work_done_missing_acceptance_signal")
    coverage = _safe_ratio(complete, work_done)
    missing_rate = _safe_ratio(missing, work_done)
    trend_signals = trend.get("signals", []) if isinstance(trend, dict) else []
    signal = next(
        (item for item in trend_signals if isinstance(item, dict) and item.get("metric") == "work_done_missing_acceptance_signal"),
        {},
    )
    summary = f"acceptance_signal={complete}/{work_done} ({coverage}); missing={missing} ({missing_rate})"
    return {
        "work_done_messages": work_done,
        "complete": complete,
        "missing": missing,
        "coverage_rate": coverage,
        "missing_rate": missing_rate,
        "status": _footer_adoption_status(coverage),
        "trend_status": str(signal.get("status") or "unknown"),
        "summary": summary,
    }


def _build_visible_summary(
    totals: Dict[str, int],
    acceptance: Dict[str, int],
    subagents: Dict[str, int],
    trend: Dict[str, object],
    footer_adoption: Dict[str, object],
) -> Dict[str, str]:
    blocked = _safe_get_int(totals, "blocked_orchestrator_events")
    total = _safe_get_int(totals, "orchestrator_events")
    blocked_ratio = _safe_ratio(blocked, total)
    lifecycle_delta = _safe_get_int(subagents, "lifecycle_delta_spawn_minus_close")
    acceptance_status = str(footer_adoption.get("status") or "unknown")
    acceptance_missing = _safe_get_int(acceptance, "work_done_missing_acceptance_signal")
    trend_overall = str(trend.get("overall") or "unknown")
    first_line = "Orchestrator 自动复盘：high" if (
        lifecycle_delta > 0 or acceptance_missing > 0 or blocked > 0
    ) else "Orchestrator 自动复盘：none"
    why_it_matters = (
        f"acceptance={acceptance_status} {footer_adoption.get('complete', 0)}/{footer_adoption.get('work_done_messages', 0)}; "
        f"lifecycle_delta={lifecycle_delta}; blocked_ratio={blocked_ratio}; trend={trend_overall}"
    )
    next_action = "tighten workflow closure and visible verification" if first_line.endswith("high") else "continue observe"
    return {
        "first_line": first_line,
        "why_it_matters": why_it_matters,
        "next_action": next_action,
    }


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

    session_files = list(scan_session_files(codex_home, cutoff=cutoff))
    event_files = session_files + list(scan_observability_event_files(codex_home))
    facts, fact_stats = extract_facts(event_files, codex_home, cutoff)
    task_tree_summary = analyze_session_tree(session_files)

    totals = defaultdict(int, fact_stats)
    event_breakdown: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "blocked": 0, "allowed": 0})
    transcript_breakdown: Dict[str, int] = defaultdict(int)
    subagents = Counter()
    tool_calls = Counter()
    acceptance = Counter()
    structured_acceptance = Counter()
    noise = Counter()
    large_output_by_project = Counter()
    large_output_by_tool = Counter()
    token_snapshots: List[Dict] = []
    sessions = set()
    samples: List[Dict] = []
    projects = Counter()
    cwd_activity = Counter()
    daily_activity = defaultdict(int)
    project_context = {}
    tool_surface = {
        "families": Counter(),
        "normalized_tool_names": Counter(),
        "events_with_input_length": 0,
        "events_with_output_length": 0,
    }

    for row in facts:
        row_dict = row if isinstance(row, dict) else row.__dict__
        totals["total_lines"] += 1
        if row_dict.get("session_id"):
            sessions.add(str(row_dict["session_id"]))

        event_type = row_dict["event_type"]
        project = _project_name(row_dict["cwd"])
        projects[project] += 1
        cwd_key = _cwd_label(str(row_dict["cwd"] or "unknown"), include_paths)
        cwd_activity[cwd_key] += 1
        daily_activity[_date_bucket(row_dict["timestamp"])] += 1
        if cwd_key not in project_context and row_dict["cwd"]:
            project_context[cwd_key] = _project_context_summary(str(row_dict["cwd"]), include_paths)
        if row_dict.get("tool_family"):
            tool_surface["families"][str(row_dict.get("tool_family"))] += 1
        if row_dict.get("normalized_tool_name"):
            tool_surface["normalized_tool_names"][str(row_dict.get("normalized_tool_name"))] += 1
        if int(row_dict.get("input_length", 0) or 0) > 0:
            tool_surface["events_with_input_length"] += 1
        if int(row_dict.get("output_length", 0) or 0) > 0:
            tool_surface["events_with_output_length"] += 1
        structured = fact_to_dict(row).get("acceptance", {}) if not isinstance(row, dict) else row_dict.get("acceptance", {})
        if isinstance(structured, dict) and structured.get("source"):
            structured_acceptance[f"source_{structured.get('source')}"] += 1
            if structured.get("is_structured_complete"):
                structured_acceptance["structured_acceptance_complete"] += 1
            if structured.get("has_work_done_signal"):
                structured_acceptance["work_done_signals"] += 1
            if structured.get("source") == "structured" and structured.get("has_verification_signal"):
                structured_acceptance["structured_acceptance_verified"] += 1
            if structured.get("has_work_done_signal") and not structured.get("has_verification_signal"):
                structured_acceptance["structured_acceptance_missing_verification"] += 1
            if structured.get("has_subagent_signal"):
                structured_acceptance["subagent_signals"] += 1
            if structured.get("footer_verification_fail"):
                structured_acceptance["footer_verification_fail"] += 1
            if structured.get("footer_review_fail"):
                structured_acceptance["footer_review_fail"] += 1
            if structured.get("footer_subagent_fail"):
                structured_acceptance["footer_subagent_fail"] += 1
            if structured.get("footer_risk_fail"):
                structured_acceptance["footer_risk_fail"] += 1

        if event_type.startswith("orchestrator_"):
            totals["orchestrator_events"] += 1
            status = str(row_dict.get("status", "unknown")).lower()
            bucket = event_breakdown[event_type]
            bucket["total"] += 1
            if status == "blocked":
                bucket["blocked"] += 1
                totals["blocked_orchestrator_events"] += 1
            elif status in {"allow", "ok", "pass", "warn"}:
                bucket["allowed"] += 1
            if len(samples) < max_samples:
                samples.append(
                    {
                        "file": row_dict["source_file"],
                        "line": row_dict["source_line"],
                        "kind": event_type,
                        "status": status,
                        "created_at": row_dict["timestamp"],
                        "project": project,
                    }
                )
            continue

        totals["transcript_events"] += 1
        transcript_breakdown[event_type] += 1

        if event_type.startswith("transcript_subagent_"):
            subagents[event_type.replace("transcript_subagent_", "")] += 1
        if event_type in {"transcript_function_call", "transcript_custom_tool_call"}:
            tool_calls[str(row_dict.get("normalized_tool_name") or row_dict.get("tool_name") or "unknown")] += 1
        if event_type in {"transcript_function_call_output", "transcript_custom_tool_output"}:
            output_len = int(row_dict.get("output_length", 0) or 0)
            if output_len >= 60000:
                noise["large_tool_outputs"] += 1
                large_output_by_project[project] += 1
                large_output_by_tool[str(row_dict.get("normalized_tool_name") or row_dict.get("tool_name") or "unknown")] += 1
                if event_type == "transcript_function_call_output":
                    noise["large_function_outputs"] += 1
                else:
                    noise["large_custom_tool_outputs"] += 1
        if event_type == "transcript_token_count":
            totals["token_count_events"] += 1
            token_count = int(row_dict.get("token_count", 0) or 0)
            token_usage = row_dict.get("token_usage") if isinstance(row_dict.get("token_usage"), dict) else {}
            if token_count > totals.get("token_count_max", 0):
                totals["token_count_max"] = token_count
            if len(token_snapshots) < max_samples:
                token_snapshots.append(
                    {
                        "token_count": row_dict.get("token_count"),
                        "token_usage": token_usage,
                        "file": row_dict["source_file"],
                        "line": row_dict["source_line"],
                    }
                )
        if event_type == "transcript_reasoning":
            totals["reasoning_events"] += 1
        if event_type == "transcript_message_assistant":
            if row_dict.get("assistant_work_done", False):
                acceptance["work_done_messages"] += 1
            if row_dict.get("assistant_work_done", False) and not row_dict.get("assistant_has_verification", False):
                acceptance["work_done_missing_verification"] += 1
            if (
                row_dict.get("assistant_work_done", False)
                and row_dict.get("acceptance_has_subagent_signal", False)
                and not row_dict.get("assistant_has_subagent_status", False)
            ):
                acceptance["work_done_missing_subagent_status"] += 1
            if row_dict.get("assistant_has_acceptance_footer", False):
                acceptance["acceptance_footer_present"] += 1
            if row_dict.get("assistant_acceptance_footer_complete", False):
                acceptance["acceptance_footer_complete"] += 1
            footer_field_count = int(row_dict.get("assistant_acceptance_footer_field_count", 0) or 0)
            if row_dict.get("assistant_has_acceptance_footer", False) and not row_dict.get("assistant_acceptance_footer_complete", False):
                acceptance["acceptance_footer_partial"] += 1
                acceptance["acceptance_footer_missing_fields"] += max(0, 4 - footer_field_count)
            if row_dict.get("assistant_work_done", False) and not row_dict.get("assistant_has_acceptance_footer", False):
                acceptance["work_done_missing_acceptance_footer"] += 1
            if row_dict.get("assistant_work_done", False) and row_dict.get("assistant_has_verification", False) and not row_dict.get("assistant_acceptance_footer_complete", False):
                acceptance["heuristic_only_completion"] += 1
            if row_dict.get("assistant_work_done", False):
                has_visible_verification = bool(row_dict.get("assistant_has_verification", False))
                needs_subagent_status = bool(row_dict.get("acceptance_has_subagent_signal", False))
                has_required_subagent_status = not needs_subagent_status or bool(row_dict.get("assistant_has_subagent_status", False))
                if has_visible_verification and has_required_subagent_status:
                    acceptance["work_done_acceptance_signal_complete"] += 1
                else:
                    acceptance["work_done_missing_acceptance_signal"] += 1
        if len(samples) < max_samples:
            samples.append(
                {
                    "file": row_dict["source_file"],
                    "line": row_dict["source_line"],
                    "kind": event_type,
                    "status": row_dict.get("status", "unknown"),
                    "created_at": row_dict["timestamp"],
                    "project": project,
                }
            )

    subagents["lifecycle_delta_spawn_minus_close"] = subagents.get("spawn_agent", 0) - subagents.get("close_agent", 0)
    acceptance["structured_acceptance_missing_verification"] = int(
        structured_acceptance.get("structured_acceptance_missing_verification", 0)
    )
    acceptance["structured_acceptance_coverage"] = int(structured_acceptance.get("structured_acceptance_complete", 0))
    acceptance["footer_verification_fail"] = int(structured_acceptance.get("footer_verification_fail", 0))
    acceptance["footer_review_fail"] = int(structured_acceptance.get("footer_review_fail", 0))
    acceptance["footer_subagent_fail"] = int(structured_acceptance.get("footer_subagent_fail", 0))
    acceptance["footer_risk_fail"] = int(structured_acceptance.get("footer_risk_fail", 0))

    if not report_id:
        report_id = now.strftime("%Y%m%dT%H%M%SZ")

    reports_root = Path(reports_dir) if reports_dir else (codex_home / "orchestrator-observability" / "reports")
    reports_root.mkdir(parents=True, exist_ok=True)
    report_facts = facts[-MAX_EMBEDDED_FACTS:]
    facts_for_report = []
    for fact in report_facts:
        row = fact_to_dict(fact)
        row["cwd"] = _cwd_label(str(row.get("cwd") or "unknown"), include_paths)
        facts_for_report.append(row)

    token_usage_totals = dict(task_tree_summary.get("aggregate", {}).get("token_usage", {}))
    data = {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "report_name": f"orchestrator-observability-{report_id}.json",
        "codex_home": str(codex_home) if include_paths else "[redacted]",
        "since_days": since_days,
        "paths_included": include_paths,
        "fact_count": len(facts),
        "fact_output": {
            "limit": MAX_EMBEDDED_FACTS,
            "emitted": len(report_facts),
            "omitted": max(0, len(facts) - len(report_facts)),
            "truncated": len(facts) > len(report_facts),
        },
        "window": {
            "from": cutoff.isoformat().replace("+00:00", "Z"),
            "to": now.isoformat().replace("+00:00", "Z"),
        },
        "totals": dict(totals),
        "sessions_seen": len(sessions),
        "projects": dict(projects),
        "cwds": dict(cwd_activity),
        "daily_activity": dict(daily_activity),
        "orchestrator_event_breakdown": {k: v for k, v in event_breakdown.items()},
        "transcript_breakdown": dict(transcript_breakdown),
        "subagents": dict(subagents),
        "task_tree": task_tree_summary,
        "tool_calls": dict(tool_calls),
        "tool_surface": {
            "families": dict(tool_surface["families"]),
            "normalized_tool_names": dict(tool_surface["normalized_tool_names"]),
            "events_with_input_length": tool_surface["events_with_input_length"],
            "events_with_output_length": tool_surface["events_with_output_length"],
        },
        "acceptance": dict(acceptance),
        "structured_acceptance": dict(structured_acceptance),
        "noise": {
            **dict(noise),
            "large_output_by_project": dict(large_output_by_project),
            "large_output_by_tool": dict(large_output_by_tool),
        },
        "project_context": project_context,
        "project_context_available": any(
            isinstance(item, dict) and bool(item.get("available")) for item in project_context.values()
        ),
        "token_snapshots": token_snapshots,
        "token_usage_totals": token_usage_totals,
        "samples": samples,
        "facts": facts_for_report,
    }
    previous_report = _load_previous_report(reports_root, report_id)
    data["trend"] = _build_trend_summary(data, previous_report)
    data["footer_adoption"] = _build_footer_adoption_summary(data["acceptance"], data["trend"])
    data["acceptance_signal_adoption"] = _build_acceptance_signal_summary(data["acceptance"], data["trend"])
    data["visible_summary"] = _build_visible_summary(
        data["totals"],
        data["acceptance"],
        data["subagents"],
        data["trend"],
        data["acceptance_signal_adoption"],
    )
    data["suggestions"] = _build_suggestions(
        data["totals"],
        data["acceptance"],
        data["noise"],
        data["subagents"],
        dict(transcript_breakdown),
        data["sessions_seen"],
    )

    json_path = reports_root / f"orchestrator-observability-{report_id}.json"
    with json_path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)

    markdown_lines = [
        "# Orchestrator Observability Report",
        "",
        f"- Visible summary: {data['visible_summary']['first_line']}",
        f"- Why it matters: {data['visible_summary']['why_it_matters']}",
        f"- Next action: {data['visible_summary']['next_action']}",
        "",
        f"- Scope: {codex_home if include_paths else '[redacted]'}",
        f"- Window: last {since_days} day(s)",
        f"- Report time: {data['generated_at']}",
        "",
        f"- Total lines: {totals.get('total_lines', 0)}",
        f"- Filtered as older: {totals.get('filtered_older', 0)}",
        f"- Invalid lines: {totals.get('invalid_lines', 0)}",
        f"- Orchestrator events: {totals.get('orchestrator_events', 0)}",
        f"- Transcript events: {totals.get('transcript_events', 0)}",
        f"- Sessions seen: {len(sessions)}",
        f"- Projects seen: {len(projects)}",
        f"- Subagent spawn/wait/close: {subagents.get('spawn_agent', 0)}/{subagents.get('wait_agent', 0)}/{subagents.get('close_agent', 0)}",
        f"- Subagent lifecycle delta (legacy close_agent-derived): {subagents.get('lifecycle_delta_spawn_minus_close', 0)}",
        f"- Task-tree current open/completed children: {task_tree_summary['aggregate'].get('open_children', 0)}/{task_tree_summary['aggregate'].get('completed_children', 0)}",
        f"- Task-tree peak active children: {task_tree_summary['aggregate'].get('peak_active_children', 0)}",
        f"- Token usage total/cached/reasoning: {token_usage_totals.get('total_tokens', 0)}/{token_usage_totals.get('cached_input_tokens', 0)}/{token_usage_totals.get('reasoning_output_tokens', 0)}",
        f"- Large tool outputs: {noise.get('large_tool_outputs', 0)}",
        f"- Acceptance footer complete: {acceptance.get('acceptance_footer_complete', 0)}",
        f"- Acceptance footer missing on work-done replies: {acceptance.get('work_done_missing_acceptance_footer', 0)}",
        f"- Acceptance signal complete: {acceptance.get('work_done_acceptance_signal_complete', 0)}",
        f"- Acceptance signal missing on work-done replies: {acceptance.get('work_done_missing_acceptance_signal', 0)}",
        "",
        "## Footer adoption",
        f"- summary: {data['footer_adoption']['summary']}",
        f"- status: {data['footer_adoption']['status']}",
        f"- trend_status: {data['footer_adoption']['trend_status']}",
        "",
        "## Acceptance signal adoption",
        f"- summary: {data['acceptance_signal_adoption']['summary']}",
        f"- status: {data['acceptance_signal_adoption']['status']}",
        f"- trend_status: {data['acceptance_signal_adoption']['trend_status']}",
        "",
        "## Orchestrator events",
    ]

    for name, values in sorted(event_breakdown.items()):
        markdown_lines.append(
            f"- `{name}`: total={values.get('total', 0)}, blocked={values.get('blocked', 0)}, allowed={values.get('allowed', 0)}"
        )

    markdown_lines.append("")
    markdown_lines.append("## Transcript events")
    for name, value in sorted(transcript_breakdown.items()):
        markdown_lines.append(f"- {name}: {value}")

    markdown_lines.append("")
    markdown_lines.append("## Tool calls")
    for name, value in tool_calls.most_common():
        markdown_lines.append(f"- `{name}`: {value}")

    markdown_lines.append("")
    markdown_lines.append("## Tool surface")
    for name, value in tool_surface["families"].most_common():
        markdown_lines.append(f"- family `{name}`: {value}")

    markdown_lines.append("")
    markdown_lines.append("## Acceptance signals")
    for name, value in sorted(acceptance.items()):
        markdown_lines.append(f"- {name}: {value}")
    if structured_acceptance:
        markdown_lines.append("")
        markdown_lines.append("## Structured acceptance signals")
        for name, value in sorted(structured_acceptance.items()):
            markdown_lines.append(f"- {name}: {value}")

    markdown_lines.append("")
    markdown_lines.append("## Trend")
    markdown_lines.append(f"- overall: {data['trend'].get('overall', 'unknown')}")
    baseline_report = data["trend"].get("baseline_report") or ""
    markdown_lines.append(f"- baseline_report: {baseline_report or 'none'}")
    for item in data["trend"].get("signals", []):
        markdown_lines.append(
            f"- {item.get('metric')}: {item.get('status')} (current={item.get('current')}, previous={item.get('previous')}, delta={item.get('delta')})"
        )

    markdown_lines.append("")
    markdown_lines.append("## Project context")
    if project_context:
        for key in sorted(project_context):
            item = project_context[key]
            if not isinstance(item, dict):
                continue
            if item.get("available"):
                markdown_lines.append(f"- {key}: available ({item.get('source', '')})")
            else:
                markdown_lines.append(f"- {key}: unavailable")

    markdown_lines.append("")
    markdown_lines.append("## Project & CWD activity")
    for name, value in sorted(projects.items(), key=lambda item: item[1], reverse=True):
        markdown_lines.append(f"- {name}: {value}")

    if since_days <= 3:
        markdown_lines.append("")
        _append_daily_summary(markdown_lines, dict(daily_activity), since_days)

    markdown_lines.append("")
    markdown_lines.append("## Suggestions")
    for suggestion in data["suggestions"]:
        markdown_lines.append(f"- {suggestion}")

    if samples:
        markdown_lines.append("")
        markdown_lines.append("## Samples")
        for sample in samples:
            markdown_lines.append(f"- {sample['created_at']} ({sample['kind']}): {sample['status']} in {sample['project']}")

    md_path = reports_root / f"orchestrator-observability-{report_id}.md"
    md_path.write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")

    return {
        "json_report": str(json_path),
        "markdown_report": str(md_path),
        "fact_count": len(facts),
        "fact_output": data["fact_output"],
        "totals": dict(totals),
        "orchestrator_event_breakdown": {k: v for k, v in event_breakdown.items()},
        "subagents": dict(subagents),
        "acceptance": dict(acceptance),
        "noise": data["noise"],
        "trend": data["trend"],
        "footer_adoption": data["footer_adoption"],
        "acceptance_signal_adoption": data["acceptance_signal_adoption"],
        "project_context": data["project_context"],
        "project_context_available": data["project_context_available"],
        "visible_summary": data["visible_summary"],
    }


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
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
