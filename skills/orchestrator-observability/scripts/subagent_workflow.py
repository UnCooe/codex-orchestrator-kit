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

MIN_SAMPLE_SESSIONS = 5
MIN_SAMPLE_WORK_DONE = 1
MIN_SAMPLE_SPAWNS = 1


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


def _action_policy(
    unclosed_delta: int,
    broad_scan_bypasses: int,
    work_done_missing_acceptance_footer: int,
    work_done_missing_subagent_status: int,
) -> str:
    if (
        unclosed_delta > 0
        or broad_scan_bypasses > 0
        or work_done_missing_acceptance_footer > 0
        or work_done_missing_subagent_status > 0
    ):
        return "triage"
    return "observe"


def _safe_get_int(source: Dict[str, object], key: str) -> int:
    try:
        return int(source.get(key, 0) or 0)
    except Exception:
        return 0


def _safe_get_float(source: Dict[str, object], key: str) -> float:
    try:
        return float(source.get(key, 0.0) or 0.0)
    except Exception:
        return 0.0


def _load_previous_report(reports_root: Path, report_id: Optional[str]) -> Optional[Dict[str, object]]:
    candidates = []
    for path in reports_root.glob("subagent-workflow-*.json"):
        if report_id and path.name == f"subagent-workflow-{report_id}.json":
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


def _is_compatible_baseline(previous_report: Dict[str, object], current_report: Dict[str, object]) -> bool:
    return (
        str(previous_report.get("report_type") or "") == str(current_report.get("report_type") or "")
        and int(previous_report.get("since_days", 0) or 0) == int(current_report.get("since_days", 0) or 0)
        and bool(previous_report.get("paths_included", False)) == bool(current_report.get("paths_included", False))
    )


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


def _classify_heavy_work_signal(signal_count: int, signal_sessions: int, delegated_sessions: int) -> str:
    if signal_count <= 0:
        return "no_signal"
    if delegated_sessions <= 0:
        return "misaligned"
    if delegated_sessions >= signal_sessions:
        return "aligned"
    return "partial"


def _heavy_work_interpretation(classification: str, signal_sessions: int, delegated_sessions: int) -> str:
    if classification == "no_signal":
        return "No heavy-work delegation prompts were observed in the selected window."
    if classification == "misaligned":
        return "Heavy-work prompts were observed, but none of those sessions spawned a subagent."
    if classification == "aligned":
        return "Every session with a heavy-work prompt also spawned a subagent."
    return (
        f"{delegated_sessions} of {signal_sessions} heavy-work signal session(s) spawned a subagent; "
        "delegation happened, but not consistently."
    )


def _heavy_work_signal_quality(classification: str, signal_sessions: int, coverage_rate: float) -> str:
    if classification == "no_signal":
        return "no_signal"
    if signal_sessions <= 1:
        return "thin_sample"
    if classification == "aligned" and coverage_rate >= 0.8:
        return "strong"
    if classification in {"aligned", "partial"} and coverage_rate >= 0.4:
        return "usable"
    return "weak"


def _build_trend_summary(current_report: Dict[str, object], previous_report: Optional[Dict[str, object]]) -> Dict[str, object]:
    if previous_report and not _is_compatible_baseline(previous_report, current_report):
        return {
            "baseline_report": str(previous_report.get("report_name") or previous_report.get("generated_at") or ""),
            "overall": "incomparable",
            "signals": [],
            "baseline_compatible": False,
        }

    current_subagents = current_report.get("subagents", {}) if isinstance(current_report.get("subagents"), dict) else {}
    current_workflow = current_report.get("workflow_signals", {}) if isinstance(current_report.get("workflow_signals"), dict) else {}
    current_heavy = current_workflow.get("heavy_work_delegation", {}) if isinstance(current_workflow.get("heavy_work_delegation"), dict) else {}

    previous_subagents = previous_report.get("subagents", {}) if previous_report and isinstance(previous_report.get("subagents"), dict) else {}
    previous_workflow = previous_report.get("workflow_signals", {}) if previous_report and isinstance(previous_report.get("workflow_signals"), dict) else {}
    previous_heavy = previous_workflow.get("heavy_work_delegation", {}) if isinstance(previous_workflow, dict) else {}

    current_coverage = round(_safe_get_float(current_heavy, "session_coverage_rate"), 4)
    previous_coverage = round(_safe_get_float(previous_heavy, "session_coverage_rate"), 4) if previous_report else None

    metrics = {
        "lifecycle_delta_spawn_minus_close": _compare_metric(
            _safe_get_int(current_subagents, "lifecycle_delta_spawn_minus_close"),
            _safe_get_int(previous_subagents, "lifecycle_delta_spawn_minus_close") if previous_report else None,
        ),
        "open_sessions": _compare_metric(
            _safe_get_int(current_subagents, "open_sessions"),
            _safe_get_int(previous_subagents, "open_sessions") if previous_report else None,
        ),
        "broad_scan_bypasses": _compare_metric(
            _safe_get_int(current_workflow, "broad_scan_bypasses"),
            _safe_get_int(previous_workflow, "broad_scan_bypasses") if previous_report else None,
        ),
        "heavy_work_signal_sessions_without_spawn": _compare_metric(
            _safe_get_int(current_heavy, "signal_sessions_without_spawn"),
            _safe_get_int(previous_heavy, "signal_sessions_without_spawn") if previous_report else None,
        ),
        "heavy_work_signal_session_coverage_x10000": _compare_metric(
            int(current_coverage * 10000),
            int(previous_coverage * 10000) if previous_coverage is not None else None,
            bigger_is_worse=False,
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
        if name == "heavy_work_signal_session_coverage_x10000":
            current_value = current_coverage
            previous_value = previous_coverage
            delta = None if previous_value is None else round(current_value - previous_value, 4)
        else:
            current_value = item["current"]
            previous_value = item["previous"]
            delta = item["delta"]
        signals.append(
            {
                "metric": name,
                "status": item["status"],
                "current": current_value,
                "previous": previous_value,
                "delta": delta,
            }
        )

    return {
        "baseline_report": str(previous_report.get("report_name") or previous_report.get("generated_at") or "") if previous_report else "",
        "overall": overall,
        "signals": signals,
        "baseline_compatible": previous_report is not None,
    }


def _sample_assessment(sessions_seen: int, work_done_messages: int, spawn_count: int) -> Dict[str, object]:
    reasons = []
    if sessions_seen < MIN_SAMPLE_SESSIONS:
        reasons.append("low_session_count")
    if work_done_messages < MIN_SAMPLE_WORK_DONE:
        reasons.append("low_work_done_coverage")
    if spawn_count < MIN_SAMPLE_SPAWNS:
        reasons.append("no_spawn_activity")

    sufficient = not (
        sessions_seen < MIN_SAMPLE_SESSIONS
        and work_done_messages < MIN_SAMPLE_WORK_DONE
        and spawn_count < MIN_SAMPLE_SPAWNS
    )
    return {
        "status": "sufficient" if sufficient else "insufficient",
        "reasons": reasons,
    }


def _policy_from_issue_count(issue_count: int) -> str:
    return "triage" if issue_count > 0 else "observe"


def _report_classification(
    action_policy: str,
    trend_overall: str,
    heavy_classification: str,
    sample_status: str,
) -> str:
    if sample_status == "insufficient":
        return "insufficient_sample"
    if trend_overall == "first_run":
        return "new"
    if trend_overall == "incomparable":
        return "repeated"
    has_open_issue = action_policy == "triage" or heavy_classification in {"misaligned", "partial"}
    if has_open_issue:
        if trend_overall in {"worsening", "mixed"}:
            return "worse"
        if trend_overall == "improving":
            return "improved"
        return "repeated"
    if trend_overall == "improving":
        return "improved"
    if trend_overall == "stable":
        return "stable"
    if trend_overall == "worsening":
        return "worse"
    return "observe"


def _build_visible_summary(
    action_policy: str,
    classification: str,
    workflow_policy: str,
    acceptance_policy: str,
    sample_assessment: Dict[str, object],
    closure_signals: Dict[str, object],
    heavy_work: Dict[str, object],
) -> Dict[str, str]:
    sample_status = str(sample_assessment.get("status") or "unknown")
    if sample_status == "insufficient":
        first_line = "Subagent 工作流复盘：insufficient-sample"
    else:
        first_line = "Subagent 工作流复盘：high" if action_policy == "triage" else "Subagent 工作流复盘：none"
    sample_reasons = ",".join(sample_assessment.get("reasons", [])) or "none"
    why_it_matters = (
        f"sample={sample_status}({sample_reasons}); "
        f"workflow={workflow_policy}; "
        f"acceptance={acceptance_policy}; "
        f"lifecycle_delta={closure_signals.get('lifecycle_delta', 0)}; "
        f"footer_missing={closure_signals.get('work_done_missing_acceptance_footer', 0)}; "
        f"heavy_quality={heavy_work.get('signal_quality', 'unknown')}; class={classification}"
    )
    if sample_status == "insufficient":
        next_action = "widen the window or wait for more completed turns before judging trend"
    elif action_policy == "triage":
        next_action = "fix workflow gaps first, then clean up footer adoption"
    else:
        next_action = "continue observe"
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
    per_session_heavy_signals = Counter()
    closure = Counter()
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

        if row_dict.get("assistant_work_done", False):
            closure["work_done_messages"] += 1
            if row_dict.get("assistant_has_acceptance_footer", False):
                closure["acceptance_footer_present"] += 1
            else:
                closure["work_done_missing_acceptance_footer"] += 1
            if row_dict.get("assistant_acceptance_footer_complete", False):
                closure["acceptance_footer_complete"] += 1
            elif row_dict.get("assistant_has_acceptance_footer", False):
                closure["acceptance_footer_partial"] += 1
            if (
                row_dict.get("acceptance_has_subagent_signal", False)
                and not row_dict.get("assistant_has_subagent_status", False)
            ):
                closure["work_done_missing_subagent_status"] += 1

        if event_type == "orchestrator_user_prompt" and str(row_dict.get("reason", "")) == "heavy_prompt_detected":
            heavy_work_delegation_signals += 1
            if session_id:
                per_session_heavy_signals[session_id] += 1

        if (
            event_type == "orchestrator_pre_tool_use"
            and status == "blocked"
            and str(row_dict.get("reason")) in {"broad_scan_detected", "broad_scan_without_research_capability"}
        ):
            broad_scan_blocks += 1
        if (
            event_type == "orchestrator_pre_tool_use"
            and status in {"allow", "warn"}
            and str(row_dict.get("reason")) in {
                "delegation_signal_detected",
                "workflow_signal_detected",
                "broad_scan_without_research_capability",
                "broad_scan_with_workflow_signal",
                "broad_scan_with_research_capability",
            }
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
    legacy_spawn_per_heavy_signal = _safe_divide(spawn_count, max(1, heavy_work_delegation_signals))

    open_session_count = 0
    for stats in per_session_subagents.values():
        if stats["spawn_agent"] > stats["close_agent"]:
            open_session_count += 1

    heavy_signal_sessions = 0
    heavy_signal_sessions_with_spawn = 0
    heavy_signal_session_spawns = 0
    for session_id, signal_count in per_session_heavy_signals.items():
        if signal_count <= 0:
            continue
        heavy_signal_sessions += 1
        session_spawn_count = per_session_subagents[session_id]["spawn_agent"]
        heavy_signal_session_spawns += session_spawn_count
        if session_spawn_count > 0:
            heavy_signal_sessions_with_spawn += 1

    heavy_signal_sessions_without_spawn = heavy_signal_sessions - heavy_signal_sessions_with_spawn
    heavy_signal_session_coverage = _safe_divide(heavy_signal_sessions_with_spawn, max(1, heavy_signal_sessions))
    avg_spawns_per_signal_session = _safe_divide(heavy_signal_session_spawns, max(1, heavy_signal_sessions))
    avg_spawns_per_delegated_signal_session = _safe_divide(
        heavy_signal_session_spawns, max(1, heavy_signal_sessions_with_spawn)
    )
    heavy_signal_classification = _classify_heavy_work_signal(
        heavy_work_delegation_signals, heavy_signal_sessions, heavy_signal_sessions_with_spawn
    )
    heavy_signal_quality = _heavy_work_signal_quality(
        heavy_signal_classification, heavy_signal_sessions, heavy_signal_session_coverage
    )
    heavy_signal_interpretation = _heavy_work_interpretation(
        heavy_signal_classification, heavy_signal_sessions, heavy_signal_sessions_with_spawn
    )
    work_done_messages = int(closure.get("work_done_messages", 0))
    acceptance_issue_count = int(closure.get("work_done_missing_acceptance_footer", 0)) + int(
        closure.get("work_done_missing_subagent_status", 0)
    )
    workflow_issue_count = (
        int(unclosed_delta > 0)
        + int(broad_scan_bypasses > 0)
        + int(heavy_signal_classification in {"misaligned", "partial"})
    )
    sample_assessment = _sample_assessment(len(sessions), work_done_messages, spawn_count)
    workflow_policy = _policy_from_issue_count(workflow_issue_count)
    acceptance_policy = _policy_from_issue_count(acceptance_issue_count)

    action_policy = "triage" if workflow_policy == "triage" or acceptance_policy == "triage" else "observe"
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
        "report_name": f"subagent-workflow-{report_id}.json",
        "codex_home": str(codex_home) if include_paths else "[redacted]",
        "since_days": since_days,
        "paths_included": include_paths,
        "report_type": "subagent_workflow",
        "reviewer_id": "subagent-workflow",
        "standard": "default-v1",
        "action_policy": action_policy,
        "workflow_policy": workflow_policy,
        "acceptance_policy": acceptance_policy,
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
            "spawn_per_heavy_signal": avg_spawns_per_signal_session,
            "spawn_per_heavy_signal_legacy": legacy_spawn_per_heavy_signal,
        },
        "workflow_signals": {
            "delegation_rate": delegation_rate,
            "heavy_work_delegation_rate": heavy_signal_session_coverage,
            "heavy_work_delegation_signals": heavy_work_delegation_signals,
            "broad_scan_blocks": broad_scan_blocks,
            "broad_scan_bypasses": broad_scan_bypasses,
            "heavy_work_delegation": {
                "classification": heavy_signal_classification,
                "signal_count": heavy_work_delegation_signals,
                "signal_sessions": heavy_signal_sessions,
                "signal_sessions_with_spawn": heavy_signal_sessions_with_spawn,
                "signal_sessions_without_spawn": heavy_signal_sessions_without_spawn,
                "session_coverage_rate": heavy_signal_session_coverage,
                "signal_quality": heavy_signal_quality,
                "avg_spawns_per_signal_session": avg_spawns_per_signal_session,
                "avg_spawns_per_delegated_signal_session": avg_spawns_per_delegated_signal_session,
                "spawn_count_in_signal_sessions": heavy_signal_session_spawns,
                "legacy_spawn_per_signal_rate": legacy_spawn_per_heavy_signal,
                "interpretation": heavy_signal_interpretation,
            },
        },
        "closure_signals": {
            "lifecycle_delta": unclosed_delta,
            "open_sessions": open_session_count,
            "broad_scan_bypasses": broad_scan_bypasses,
            "heavy_work_signal_quality": heavy_signal_quality,
            "work_done_messages": work_done_messages,
            "acceptance_footer_present": int(closure.get("acceptance_footer_present", 0)),
            "acceptance_footer_complete": int(closure.get("acceptance_footer_complete", 0)),
            "acceptance_footer_partial": int(closure.get("acceptance_footer_partial", 0)),
            "work_done_missing_acceptance_footer": int(closure.get("work_done_missing_acceptance_footer", 0)),
            "work_done_missing_subagent_status": int(closure.get("work_done_missing_subagent_status", 0)),
        },
        "sample_assessment": sample_assessment,
        "tool_calls": dict(tool_calls),
        "project_context": project_context,
        "samples": samples,
        "facts": facts_for_report,
    }
    previous_report = _load_previous_report(reports_root, report_id)
    data["trend"] = _build_trend_summary(data, previous_report)
    data["classification"] = _report_classification(
        action_policy,
        str(data["trend"].get("overall") or "unknown"),
        heavy_signal_classification,
        str(sample_assessment.get("status") or "unknown"),
    )
    data["visible_summary"] = _build_visible_summary(
        action_policy,
        str(data["classification"]),
        workflow_policy,
        acceptance_policy,
        sample_assessment,
        data["closure_signals"],
        data["workflow_signals"]["heavy_work_delegation"],
    )

    json_path = reports_root / f"subagent-workflow-{report_id}.json"
    with json_path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)

    markdown_lines = [
        "# Subagent Workflow Report",
        "",
        "- Reviewer: `subagent-workflow`",
        "- Standard: `default-v1`",
        f"- Action policy: `{action_policy}`",
        f"- Workflow policy: `{workflow_policy}`",
        f"- Acceptance policy: `{acceptance_policy}`",
        f"- Classification: `{data['classification']}`",
        f"- Visible summary: {data['visible_summary']['first_line']}",
        f"- Why it matters: {data['visible_summary']['why_it_matters']}",
        f"- Next action: {data['visible_summary']['next_action']}",
        f"- Project context available: {str(project_context_available).lower()}",
        f"- Scope: {codex_home if include_paths else '[redacted]'}",
        f"- Window: last {since_days} day(s)",
        f"- Report time: {data['generated_at']}",
        "",
        f"- Total lines: {totals.get('total_lines', 0)}",
        f"- Invalid lines: {totals.get('invalid_lines', 0)}",
        f"- Filtered as older: {totals.get('filtered_older', 0)}",
        f"- Sessions seen: {len(sessions)}",
        f"- Sample status: {sample_assessment['status']} ({', '.join(sample_assessment['reasons']) or 'none'})",
        "",
        "## Workflow health",
        f"- Spawn/Wait/Close: {spawn_count}/{wait_count}/{close_count}",
        f"- Unclosed delta: {unclosed_delta}",
        f"- Open sessions with unclosed subagents: {open_session_count}",
        "",
        "## Acceptance signals",
        f"- Work-done replies: {work_done_messages}",
        f"- Acceptance footer complete: {closure.get('acceptance_footer_complete', 0)}",
        f"- Acceptance footer partial: {closure.get('acceptance_footer_partial', 0)}",
        f"- Missing acceptance footer on work-done replies: {closure.get('work_done_missing_acceptance_footer', 0)}",
        f"- Missing subagent lifecycle status on work-done replies: {closure.get('work_done_missing_subagent_status', 0)}",
        "",
        "## Delegation signals",
        f"- Delegation rate (spawn / tool calls): {delegation_rate}",
        f"- Heavy-work delegation signals: {heavy_work_delegation_signals}",
        f"- Heavy-work signal session coverage: {heavy_signal_sessions_with_spawn}/{heavy_signal_sessions} ({heavy_signal_session_coverage})",
        f"- Heavy-work delegation classification: {heavy_signal_classification}",
        f"- Heavy-work interpretation: {heavy_signal_interpretation}",
        f"- Average spawns per heavy-work signal session: {avg_spawns_per_signal_session}",
        f"- Legacy spawn / heavy-work signal ratio: {legacy_spawn_per_heavy_signal}",
        f"- Broad-scan blocks: {broad_scan_blocks}",
        f"- Broad-scan bypass signals (observed): {broad_scan_bypasses}",
        "",
        "## Trend",
        f"- overall: {data['trend'].get('overall', 'unknown')}",
        f"- baseline_report: {data['trend'].get('baseline_report') or 'none'}",
        f"- baseline_compatible: {str(bool(data['trend'].get('baseline_compatible', False))).lower()}",
        "",
        "## Project context",
    ]

    for item in data["trend"].get("signals", []):
        markdown_lines.insert(
            len(markdown_lines) - 2,
            (
                f"- {item.get('metric')}: {item.get('status')} "
                f"(current={item.get('current')}, previous={item.get('previous')}, delta={item.get('delta')})"
            ),
        )

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
        "closure_signals": data["closure_signals"],
        "sample_assessment": data["sample_assessment"],
        "workflow_policy": data["workflow_policy"],
        "acceptance_policy": data["acceptance_policy"],
        "trend": data["trend"],
        "classification": data["classification"],
        "visible_summary": data["visible_summary"],
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
