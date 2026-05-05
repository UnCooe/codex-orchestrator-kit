#!/usr/bin/env python3
"""Generate Turnstate compliance reports from Codex session facts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional


SESSION_FACTS_DIR = Path(__file__).resolve().parents[2] / "orchestrator-observability" / "scripts"
if str(SESSION_FACTS_DIR) not in sys.path:
    sys.path.insert(0, str(SESSION_FACTS_DIR))

from session_facts import (  # noqa: E402
    SessionFact,
    extract_facts,
    fact_to_dict,
    scan_observability_event_files,
    scan_session_files,
)


LARGE_OUTPUT_THRESHOLD = 60000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Turnstate compliance reports from Codex sessions.")
    parser.add_argument("--codex-home", default=str(Path.home() / ".codex"), help="Path to CODEX_HOME")
    parser.add_argument("--since-days", type=int, default=3, help="Window in days for event filtering")
    parser.add_argument("--report-id", default=None, help="Optional report filename suffix")
    parser.add_argument(
        "--reports-dir",
        default=None,
        help="Optional custom reports directory (defaults under CODEX_HOME/turnstate-observability/reports)",
    )
    parser.add_argument(
        "--include-paths",
        action="store_true",
        help="Include full local paths in reports. Default redacts paths to project names.",
    )
    parser.add_argument("--max-samples", type=int, default=20, help="Maximum issue samples to include")
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
    return "unknown" if project == "unknown" else f"project:{project}"


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

    source = str(root / ".codex" / "project-context.md").replace("\\", "/")
    display_source = source if include_paths else "project/.codex/project-context.md"
    if not include_paths and root.name:
        display_source = f"project:{root.name}/.codex/project-context.md"

    if not (root / ".codex" / "project-context.md").exists():
        return {"available": False, "source": display_source, "summary": ""}

    try:
        text = (root / ".codex" / "project-context.md").read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return {"available": False, "source": display_source, "summary": ""}

    normalized = " ".join(_redact_context(text).replace("\r", "").replace("\n", " ").split())
    if len(normalized) > 240:
        normalized = normalized[:237] + "..."
    return {"available": True, "source": display_source, "summary": normalized}


def _sort_key(fact: SessionFact) -> tuple[str, str, int]:
    return (fact.timestamp or "", fact.source_file, fact.source_line)


def _session_key(fact: SessionFact) -> str:
    if fact.session_id:
        return fact.session_id
    return f"{fact.source_file}:{fact.source_line}"


def _has_tool_output(event_type: str) -> bool:
    return event_type in {"transcript_function_call_output", "transcript_custom_tool_output"}


def _issue(
    session_id: str,
    project: str,
    rule: str,
    severity: str,
    evidence: str,
    fact: Optional[SessionFact] = None,
) -> Dict[str, object]:
    item: Dict[str, object] = {
        "session_id": session_id,
        "project": project,
        "rule": rule,
        "severity": severity,
        "evidence": evidence,
    }
    if fact is not None:
        item["source_file"] = fact.source_file
        item["source_line"] = fact.source_line
        item["timestamp"] = fact.timestamp
    return item


def _build_suggestions(rule_failures: Counter, high_issues: int, medium_issues: int) -> List[str]:
    suggestions: List[str] = []
    if rule_failures.get("reasoning_trace_present", 0):
        suggestions.append("Confirm reasoning summaries are present for active sessions.")
    if rule_failures.get("tool_output_balanced", 0):
        suggestions.append("Balance tool calls and tool outputs; missing outputs are visibility gaps.")
    if rule_failures.get("token_count_present", 0):
        suggestions.append("Require token_count events for active sessions.")
    if rule_failures.get("final_acceptance_present", 0):
        suggestions.append("Require final acceptance note when work was done.")
    if rule_failures.get("large_output_avoided", 0):
        suggestions.append("Large outputs should be summarized and kept compact.")
    if high_issues:
        suggestions.append("Elevated risk: requires follow-up before proceeding.")
    elif medium_issues:
        suggestions.append("Some medium-risk issues were found; review before continuation.")
    if not suggestions:
        suggestions.append("No immediate Turnstate blockers from this window.")
    return suggestions


def _score(rule_totals: Counter, rule_failures: Counter) -> int:
    total = sum(rule_totals.values())
    failed = sum(rule_failures.values())
    if total <= 0:
        return 100
    score = round(100 * (1 - (failed / total)))
    return max(0, min(100, score))


def build_report(
    codex_home: Path,
    since_days: int,
    report_id: Optional[str],
    reports_dir: Optional[str],
    include_paths: bool,
    max_samples: int,
) -> Dict[str, object]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=since_days)
    event_files = list(scan_session_files(codex_home, cutoff=cutoff)) + list(scan_observability_event_files(codex_home))
    facts, fact_stats = extract_facts(event_files, codex_home, cutoff)

    sessions: Dict[str, List[SessionFact]] = defaultdict(list)
    for fact in facts:
        sessions[_session_key(fact)].append(fact)

    rule_totals: Counter = Counter()
    rule_failures: Counter = Counter()
    project_activity: Counter = Counter()
    event_breakdown: Counter = Counter()
    issue_samples: List[Dict[str, object]] = []
    severity_counts: Counter = Counter()
    session_summaries: List[Dict[str, object]] = []
    project_context: Dict[str, Dict[str, object]] = {}
    tool_surface_families: Counter = Counter()
    tool_surface_names: Counter = Counter()

    for session_id, rows in sessions.items():
        rows = sorted(rows, key=_sort_key)
        project = _project_name(rows[0].cwd if rows else "unknown")
        project_key = _cwd_label(rows[0].cwd if rows else "unknown", include_paths)
        if project_key not in project_context:
            project_context[project_key] = _project_context_summary(rows[0].cwd if rows else "", include_paths)

        project_activity[project] += len(rows)
        for row in rows:
            if row.tool_family:
                tool_surface_families[row.tool_family] += 1
            if row.normalized_tool_name:
                tool_surface_names[row.normalized_tool_name] += 1

        counts = Counter(row.event_type for row in rows)
        event_breakdown.update(counts)
        tool_calls = sum(counts[event] for event in ("transcript_function_call", "transcript_custom_tool_call"))
        tool_outputs = sum(
            counts[event] for event in ("transcript_function_call_output", "transcript_custom_tool_output")
        )
        reasoning_events = counts.get("transcript_reasoning", 0)
        token_events = counts.get("transcript_token_count", 0)
        assistant_work_done = any(row.assistant_work_done for row in rows)
        structured_verified = any(
            row.acceptance_source == "structured"
            and row.acceptance_has_work_done_signal
            and row.acceptance_has_verification_signal
            for row in rows
        )
        missing_verification = (
            any(row.assistant_work_done and not row.assistant_has_verification for row in rows)
            and not structured_verified
        )

        large_outputs = [
            row
            for row in rows
            if _has_tool_output(row.event_type) and int(row.output_length or 0) >= LARGE_OUTPUT_THRESHOLD
        ]
        active_session = bool(tool_calls or tool_outputs or assistant_work_done or reasoning_events)

        def add_issue(rule: str, severity: str, evidence: str, fact: Optional[SessionFact] = None) -> None:
            rule_failures[rule] += 1
            severity_counts[severity] += 1
            if len(issue_samples) < max_samples:
                issue_samples.append(_issue(session_id, project, rule, severity, evidence, fact or (rows[0] if rows else None)))

        if active_session:
            rule_totals["reasoning_trace_present"] += 1
            if reasoning_events == 0:
                add_issue(
                    "reasoning_trace_present",
                    "medium",
                    "No explicit reasoning traces found for active work. If execution-only, this may be acceptable.",
                )

            rule_totals["token_count_present"] += 1
            if token_events == 0:
                add_issue("token_count_present", "low", "No token_count event observed for this active session.")

        if tool_calls or tool_outputs:
            rule_totals["tool_output_balanced"] += 1
            if tool_outputs + 1 < tool_calls:
                add_issue(
                    "tool_output_balanced",
                    "high",
                    f"Tool calls and outputs are imbalanced (calls={tool_calls}, outputs={tool_outputs}).",
                )

        if assistant_work_done:
            rule_totals["final_acceptance_present"] += 1
            if missing_verification:
                add_issue(
                    "final_acceptance_present",
                    "medium",
                    "Work completion is claimed without an explicit verification signal.",
                )

        if large_outputs:
            rule_totals["large_output_avoided"] += 1
            add_issue(
                "large_output_avoided",
                "medium",
                f"Observed {len(large_outputs)} large tool output event(s); summarize compactly.",
                large_outputs[0],
            )

        session_summaries.append(
            {
                "session_id": session_id,
                "project": project if include_paths else _cwd_label(rows[0].cwd if rows else "unknown", include_paths),
                "events": len(rows),
                "tool_calls": tool_calls,
                "tool_outputs": tool_outputs,
                "reasoning_events": reasoning_events,
                "token_count_events": token_events,
                "assistant_work_done": assistant_work_done,
                "large_outputs": len(large_outputs),
            }
        )

    score = _score(rule_totals, rule_failures)
    high_issues = severity_counts.get("high", 0)
    medium_issues = severity_counts.get("medium", 0)
    if high_issues:
        status = "needs_review"
    elif medium_issues:
        status = "watch"
    else:
        status = "ok"

    if not report_id:
        report_id = now.strftime("%Y%m%dT%H%M%SZ")
    reports_root = Path(reports_dir) if reports_dir else codex_home / "turnstate-observability" / "reports"
    reports_root.mkdir(parents=True, exist_ok=True)

    facts_for_report = []
    for fact in facts:
        row = fact_to_dict(fact)
        row["cwd"] = _cwd_label(str(row.get("cwd") or "unknown",), include_paths)
        facts_for_report.append(row)

    data: Dict[str, object] = {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "codex_home": str(codex_home) if include_paths else "[redacted]",
        "since_days": since_days,
        "paths_included": include_paths,
        "rule_profile": "turnstate-default-v1",
        "window": {
            "from": cutoff.isoformat().replace("+00:00", "Z"),
            "to": now.isoformat().replace("+00:00", "Z"),
        },
        "fact_count": len(facts),
        "fact_stats": fact_stats,
        "sessions_seen": len(sessions),
        "score": score,
        "status": status,
        "severity_counts": dict(severity_counts),
        "rule_totals": dict(rule_totals),
        "rule_failures": dict(rule_failures),
        "projects": dict(project_activity),
        "event_breakdown": dict(event_breakdown),
        "tool_surface": {
            "families": dict(tool_surface_families),
            "normalized_tool_names": dict(tool_surface_names),
        },
        "project_context": project_context,
        "issue_samples": issue_samples,
        "session_summaries": session_summaries[:max_samples],
        "facts": facts_for_report,
    }
    data["suggestions"] = _build_suggestions(rule_failures, high_issues, medium_issues)

    json_path = reports_root / f"turnstate-compliance-{report_id}.json"
    with json_path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)

    markdown_lines = [
        "# Turnstate compliance report",
        "",
        f"- Scope: {codex_home if include_paths else '[redacted]'}",
        f"- Window: last {since_days} day(s)",
        f"- Report time: {data['generated_at']}",
        f"- Status: {status}",
        f"- Score: {score}",
        f"- Sessions seen: {len(sessions)}",
        f"- Facts seen: {len(facts)}",
        "",
        "## Rule totals",
    ]
    for rule, total in sorted(rule_totals.items()):
        markdown_lines.append(f"- `{rule}`: total={total}, failed={rule_failures.get(rule, 0)}")

    if tool_surface_families:
        markdown_lines.append("")
        markdown_lines.append("## Tool surface")
        for name, value in tool_surface_families.most_common():
            markdown_lines.append(f"- family `{name}`: {value}")

    if project_context:
        markdown_lines.append("")
        markdown_lines.append("## Project context")
        for key in sorted(project_context):
            item = project_context[key]
            if item.get("available"):
                markdown_lines.append(f"- {key}: available ({item.get('source', '')})")
            else:
                markdown_lines.append(f"- {key}: unavailable")

    markdown_lines.append("")
    markdown_lines.append("## Severity")
    if severity_counts:
        for severity, count in sorted(severity_counts.items()):
            markdown_lines.append(f"- {severity}: {count}")
    else:
        markdown_lines.append("- No severity tags recorded")

    markdown_lines.append("")
    markdown_lines.append("## Project activity")
    for project, count in project_activity.most_common():
        markdown_lines.append(f"- {project}: {count}")

    markdown_lines.append("")
    markdown_lines.append("## Suggestions")
    for suggestion in data["suggestions"]:
        markdown_lines.append(f"- {suggestion}")

    if issue_samples:
        markdown_lines.append("")
        markdown_lines.append("## Samples")
        for sample in issue_samples:
            markdown_lines.append(f"- {sample['severity']} `{sample['rule']}` in {sample['project']}: {sample['evidence']}")

    md_path = reports_root / f"turnstate-compliance-{report_id}.md"
    md_path.write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")

    return {
        "json_report": str(json_path),
        "markdown_report": str(md_path),
        "status": status,
        "score": score,
        "severity_counts": dict(severity_counts),
        "rule_failures": dict(rule_failures),
        "sessions_seen": len(sessions),
    }


if __name__ == "__main__":
    args = parse_args()
    output = build_report(
        Path(args.codex_home).expanduser(),
        args.since_days,
        args.report_id,
        args.reports_dir,
        args.include_paths,
        args.max_samples,
    )
    print(json.dumps(output, indent=2, ensure_ascii=False))
