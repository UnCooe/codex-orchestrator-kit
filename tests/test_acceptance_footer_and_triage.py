from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator-observability" / "scripts"
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HOOKS))

from analyze import build_report  # noqa: E402
from orchestrator_observability import _detect_session_id  # noqa: E402
from workflow_profile import infer_capability  # noqa: E402
from session_facts import extract_facts  # noqa: E402
from subagent_workflow import build_report as build_subagent_workflow_report  # noqa: E402
from triage import classify  # noqa: E402


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")


def _run_stop_hook(payload: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(HOOKS / "orchestrator_stop.py")],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout)


def _run_pre_tool_hook(payload: dict) -> str:
    proc = subprocess.run(
        [sys.executable, str(HOOKS / "orchestrator_pre_tool_use.py")],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    return proc.stdout.strip()


def _run_user_prompt_hook(payload: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(HOOKS / "orchestrator_user_prompt.py")],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def _read_last_observability_event(codex_home: Path, event_name: str) -> dict:
    events_root = codex_home / "orchestrator-observability" / "events"
    candidates = sorted(events_root.glob("*.jsonl"))
    if not candidates:
        return {}
    last_match = {}
    for path in candidates:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                record = json.loads(line)
            except Exception:
                continue
            if record.get("event_name") == event_name:
                last_match = record
    return last_match


class AcceptanceFooterAndTriageTests(unittest.TestCase):
    def test_user_prompt_heavy_prompt_is_silent_by_default(self) -> None:
        output = _run_user_prompt_hook({"prompt": "please implement this feature and add tests"})
        self.assertEqual(output, {})

    def test_user_prompt_benign_prompt_is_silent_by_default(self) -> None:
        output = _run_user_prompt_hook({"prompt": "what is 2+2?"})
        self.assertEqual(output, {})

    def test_stop_allows_missing_visible_acceptance_footer_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "stop.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "make the change"}, "timestamp": "2026-05-02T00:00:00Z"},
                ],
            )
            payload = {
                "transcript_path": str(transcript),
                "last_assistant_message": "Implemented the requested workflow change. Verification: ran the focused unit test.",
            }

            output = _run_stop_hook(payload)

            self.assertEqual(output, {"continue": True})
            event = _read_last_observability_event(codex_home, "stop")
            self.assertEqual(event.get("reason"), "formatting_only_missing_acceptance")

    def test_stop_allows_retained_subagent_reason_when_spawn_outnumbers_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "retained.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "implement and test"}, "timestamp": "2026-05-02T00:00:00Z"},
                    {"payload": {"type": "function_call", "name": "spawn_agent", "arguments": {"task": "review"}}, "timestamp": "2026-05-02T00:01:00Z"},
                ],
            )
            payload = {
                "transcript_path": str(transcript),
                "last_assistant_message": (
                    "Implemented the change. Verification: ran focused tests.\n"
                    "verification_status=pass\n"
                    "review_status=pass\n"
                    "subagent_status=retained because the reviewer is continuing the next-turn audit\n"
                    "risk_status=low"
                ),
            }

            output = _run_stop_hook(payload)

            self.assertEqual(output, {"continue": True})
            event = _read_last_observability_event(codex_home, "stop")
            self.assertNotEqual(event.get("reason"), "subagent_lifecycle_incomplete")

    def test_detect_session_id_normalizes_rollout_filename(self) -> None:
        session_id = _detect_session_id(
            "/tmp/.codex/sessions/2026/05/15/rollout-2026-05-15T20-13-49-019e2668-493a-73c1-ba3c-391cbc8664bc.jsonl"
        )
        self.assertEqual(session_id, "019e2668-493a-73c1-ba3c-391cbc8664bc")

    def test_extract_facts_marks_partial_acceptance_footer(self) -> None:
        cutoff = datetime(2026, 5, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "footer.jsonl"
            _write_jsonl(
                transcript,
                [
                    {
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": (
                                "implemented workflow change.\n"
                                "verification_status=pass\n"
                                "review_status=pass"
                            ),
                        },
                        "timestamp": "2026-05-02T00:00:00Z",
                    }
                ],
            )

            facts, _stats = extract_facts([transcript], codex_home, cutoff)

            self.assertEqual(len(facts), 1)
            fact = facts[0]
            self.assertTrue(fact.assistant_has_acceptance_footer)
            self.assertFalse(fact.assistant_acceptance_footer_complete)
            self.assertEqual(fact.assistant_acceptance_footer_field_count, 2)

    def test_extract_facts_accepts_fullwidth_colon_footer(self) -> None:
        cutoff = datetime(2026, 5, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "footer.jsonl"
            _write_jsonl(
                transcript,
                [
                    {
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": (
                                "已完成检查。\n"
                                "verification_status：已完成只读核对\n"
                                "review_status：已检查\n"
                                "subagent_status：not_used\n"
                                "risk_status：低"
                            ),
                        },
                        "timestamp": "2026-05-02T00:00:00Z",
                    }
                ],
            )

            facts, _stats = extract_facts([transcript], codex_home, cutoff)

            self.assertEqual(len(facts), 1)
            fact = facts[0]
            self.assertTrue(fact.assistant_acceptance_footer_complete)
            self.assertFalse(fact.acceptance_footer_verification_fail)

    def test_extract_facts_allows_explained_partial_verification(self) -> None:
        cutoff = datetime(2026, 5, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "footer.jsonl"
            _write_jsonl(
                transcript,
                [
                    {
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": (
                                "implemented workflow check.\n"
                                "verification_status=partial；ran smoke checks but deferred live client validation.\n"
                                "review_status=pass\n"
                                "subagent_status=not_used\n"
                                "risk_status=medium；live client validation remains."
                            ),
                        },
                        "timestamp": "2026-05-02T00:00:00Z",
                    }
                ],
            )

            facts, _stats = extract_facts([transcript], codex_home, cutoff)

            self.assertEqual(len(facts), 1)
            fact = facts[0]
            self.assertTrue(fact.assistant_acceptance_footer_complete)
            self.assertFalse(fact.acceptance_footer_verification_fail)

    def test_build_report_compares_footer_trend_against_previous_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            reports_root.mkdir(parents=True, exist_ok=True)
            previous = {
                "generated_at": "2026-05-01T00:00:00Z",
                "report_name": "orchestrator-observability-prev.json",
                "totals": {"orchestrator_events": 5, "blocked_orchestrator_events": 0},
                "subagents": {"lifecycle_delta_spawn_minus_close": 0},
                "acceptance": {
                    "work_done_missing_verification": 0,
                    "work_done_missing_subagent_status": 0,
                    "work_done_missing_acceptance_footer": 0,
                    "acceptance_footer_partial": 0,
                },
            }
            (reports_root / "orchestrator-observability-prev.json").write_text(
                json.dumps(previous), encoding="utf-8"
            )

            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "current.jsonl"
            _write_jsonl(
                transcript,
                [
                    {
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "implemented change without footer. Verification: pass.",
                        },
                        "timestamp": "2026-05-02T00:00:00Z",
                    }
                ],
            )

            output = build_report(codex_home, since_days=30, report_id="current", max_samples=5, reports_dir=str(reports_root))

            self.assertEqual(output["trend"]["overall"], "worsening")
            footer_signal = next(
                item for item in output["trend"]["signals"] if item["metric"] == "work_done_missing_acceptance_footer"
            )
            self.assertEqual(footer_signal["status"], "worsened")

    def test_triage_uses_trend_to_downgrade_recovering_signal(self) -> None:
        report = {
            "totals": {"orchestrator_events": 10, "blocked_orchestrator_events": 4},
            "subagents": {"lifecycle_delta_spawn_minus_close": 0},
            "acceptance": {
                "work_done_missing_verification": 0,
                "work_done_missing_subagent_status": 0,
                "work_done_missing_acceptance_footer": 1,
                "acceptance_footer_partial": 0,
            },
            "trend": {
                "overall": "improving",
                "signals": [
                    {
                        "metric": "work_done_missing_acceptance_footer",
                        "status": "improved",
                        "current": 1,
                        "previous": 3,
                        "delta": -2,
                    }
                ],
            },
            "suggestions": ["Tighten footer reminders."],
        }

        severity, reasons, _flags = classify(report)

        self.assertEqual(severity, "candidate_low")
        self.assertEqual(reasons, ["recovering_but_open"])

    def test_subagent_workflow_report_exposes_minimal_closure_signals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            reports_root.mkdir(parents=True, exist_ok=True)
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "workflow.jsonl"
            _write_jsonl(
                transcript,
                [
                    {
                        "timestamp": "2026-05-02T00:00:00Z",
                        "session_id": "sess-1",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": (
                                "This is a substantial implementation. I will delegate.\n"
                                "verification_status=pass\n"
                                "review_status=pass"
                            ),
                        },
                    },
                    {
                        "timestamp": "2026-05-02T00:01:00Z",
                        "session_id": "sess-1",
                        "payload": {
                            "type": "function_call",
                            "name": "spawn_agent",
                            "arguments": {"task": "worker"},
                        },
                    },
                    {
                        "timestamp": "2026-05-02T00:02:00Z",
                        "session_id": "sess-1",
                        "event": "orchestrator_observability",
                        "event_name": "user_prompt",
                        "status": "warn",
                        "reason": "acceptance_context",
                    },
                ],
            )

            output = build_subagent_workflow_report(
                codex_home,
                since_days=30,
                report_id="workflow",
                reports_dir=str(reports_root),
                max_samples=5,
            )

            closure = output["closure_signals"]
            self.assertEqual(closure["lifecycle_delta"], 1)
            self.assertEqual(closure["open_sessions"], 1)
            self.assertEqual(closure["heavy_work_signal_quality"], "no_signal")
            self.assertEqual(output["visible_summary"]["first_line"], "Subagent 工作流复盘：high")
            self.assertEqual(output["workflow_policy"], "triage")
            self.assertEqual(output["acceptance_policy"], "observe")

    def test_subagent_workflow_counts_only_heavy_prompt_detected_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            reports_root.mkdir(parents=True, exist_ok=True)
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "workflow.jsonl"
            events = codex_home / "orchestrator-observability" / "events" / "2026-05-02.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"type": "session_meta", "payload": {"id": "sess-1", "cwd": "/repo/one"}, "timestamp": "2026-05-02T00:00:00Z"},
                    {"timestamp": "2026-05-02T00:01:00Z", "payload": {"type": "function_call", "name": "spawn_agent", "arguments": {"task": "worker"}}},
                ],
            )
            _write_jsonl(
                events,
                [
                    {
                        "event": "orchestrator_observability",
                        "event_name": "user_prompt",
                        "status": "allow",
                        "reason": "acceptance_context",
                        "session_id": "sess-1",
                        "created_at": "2026-05-02T00:00:30Z",
                        "cwd": "/repo/one",
                    },
                    {
                        "event": "orchestrator_observability",
                        "event_name": "user_prompt",
                        "status": "warn",
                        "reason": "heavy_prompt_detected",
                        "session_id": "sess-1",
                        "created_at": "2026-05-02T00:00:45Z",
                        "cwd": "/repo/one",
                    },
                ],
            )

            output = build_subagent_workflow_report(
                codex_home,
                since_days=30,
                report_id="workflow-heavy",
                reports_dir=str(reports_root),
                max_samples=5,
            )

            heavy = output["workflow_signals"]["heavy_work_delegation"]
            self.assertEqual(heavy["signal_count"], 1)
            self.assertEqual(heavy["signal_sessions"], 1)
            self.assertEqual(heavy["signal_sessions_with_spawn"], 1)

    def test_subagent_workflow_marks_insufficient_sample_when_no_work_done_or_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            reports_root.mkdir(parents=True, exist_ok=True)
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "workflow.jsonl"
            events = codex_home / "orchestrator-observability" / "events" / "2026-05-02.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"type": "session_meta", "payload": {"id": "sess-1", "cwd": "/repo/one"}, "timestamp": "2026-05-02T00:00:00Z"},
                    {"type": "session_meta", "payload": {"id": "sess-2", "cwd": "/repo/two"}, "timestamp": "2026-05-02T00:00:10Z"},
                ],
            )
            _write_jsonl(
                events,
                [
                    {
                        "event": "orchestrator_observability",
                        "event_name": "user_prompt",
                        "status": "warn",
                        "reason": "heavy_prompt_detected",
                        "session_id": "sess-1",
                        "created_at": "2026-05-02T00:00:45Z",
                        "cwd": "/repo/one",
                    }
                ],
            )

            output = build_subagent_workflow_report(
                codex_home,
                since_days=30,
                report_id="workflow-insufficient",
                reports_dir=str(reports_root),
                max_samples=5,
            )

            self.assertEqual(output["classification"], "insufficient_sample")
            self.assertEqual(output["sample_assessment"]["status"], "insufficient")
            self.assertEqual(output["visible_summary"]["first_line"], "Subagent 工作流复盘：insufficient-sample")

    def test_subagent_workflow_ignores_incompatible_baseline_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            reports_root.mkdir(parents=True, exist_ok=True)
            previous = {
                "generated_at": "2026-05-01T00:00:00Z",
                "report_name": "subagent-workflow-prev.json",
                "report_type": "subagent_workflow",
                "since_days": 7,
                "paths_included": False,
                "subagents": {"lifecycle_delta_spawn_minus_close": 10, "open_sessions": 2},
                "workflow_signals": {
                    "broad_scan_bypasses": 3,
                    "heavy_work_delegation": {"signal_sessions_without_spawn": 2, "session_coverage_rate": 0.2},
                },
            }
            (reports_root / "subagent-workflow-prev.json").write_text(json.dumps(previous), encoding="utf-8")

            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "workflow.jsonl"
            _write_jsonl(
                transcript,
                [
                    {
                        "timestamp": "2026-05-02T00:00:00Z",
                        "session_id": "sess-1",
                        "payload": {"type": "message", "role": "assistant", "content": "implemented a fix.\nverification_status=pass\nreview_status=pass\nsubagent_status=not_used\nrisk_status=low"},
                    }
                ],
            )

            output = build_subagent_workflow_report(
                codex_home,
                since_days=30,
                report_id="workflow-baseline",
                reports_dir=str(reports_root),
                max_samples=5,
            )

            self.assertEqual(output["trend"]["overall"], "incomparable")
            self.assertFalse(output["trend"]["baseline_compatible"])

    def test_subagent_workflow_counts_current_broad_scan_reason_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            reports_root.mkdir(parents=True, exist_ok=True)
            events = codex_home / "orchestrator-observability" / "events" / "2026-05-02.jsonl"
            _write_jsonl(
                events,
                [
                    {
                        "event": "orchestrator_observability",
                        "event_name": "pre_tool_use",
                        "status": "blocked",
                        "reason": "broad_scan_without_research_capability",
                        "tool_family": "shell_like",
                        "created_at": "2026-05-02T00:00:10Z",
                        "cwd": "/repo/one",
                    },
                    {
                        "event": "orchestrator_observability",
                        "event_name": "pre_tool_use",
                        "status": "allow",
                        "reason": "broad_scan_with_workflow_signal",
                        "tool_family": "shell_like",
                        "created_at": "2026-05-02T00:00:20Z",
                        "cwd": "/repo/one",
                    },
                ],
            )

            output = build_subagent_workflow_report(
                codex_home,
                since_days=30,
                report_id="workflow-broad-scan",
                reports_dir=str(reports_root),
                max_samples=5,
            )

            self.assertEqual(output["workflow_signals"]["broad_scan_blocks"], 1)
            self.assertEqual(output["workflow_signals"]["broad_scan_bypasses_observed"], 1)
            self.assertEqual(output["workflow_signals"]["broad_scan_bypasses"], 0)

    def test_subagent_workflow_reports_completion_integrity_signals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            reports_root.mkdir(parents=True, exist_ok=True)
            events = codex_home / "orchestrator-observability" / "events" / "2026-05-02.jsonl"
            _write_jsonl(
                events,
                [
                    {
                        "event": "orchestrator_observability",
                        "event_name": "stop",
                        "status": "warn",
                        "reason": "formatting_only_missing_acceptance",
                        "completion_state": {"subagent_lifecycle_state": "not_used"},
                        "created_at": "2026-05-02T00:00:10Z",
                        "cwd": "/repo/one",
                    },
                    {
                        "event": "orchestrator_observability",
                        "event_name": "stop",
                        "status": "allow",
                        "reason": "stop_gate_passed",
                        "completion_state": {"subagent_lifecycle_state": "closed"},
                        "created_at": "2026-05-02T00:00:20Z",
                        "cwd": "/repo/one",
                    },
                    {
                        "event": "orchestrator_observability",
                        "event_name": "stop",
                        "status": "blocked",
                        "reason": "verification_claim_conflict",
                        "completion_state": {"subagent_lifecycle_state": "closed"},
                        "created_at": "2026-05-02T00:00:30Z",
                        "cwd": "/repo/one",
                    },
                    {
                        "event": "orchestrator_observability",
                        "event_name": "stop",
                        "status": "blocked",
                        "reason": "subagent_lifecycle_incomplete",
                        "completion_state": {"subagent_lifecycle_state": "retained_or_incomplete"},
                        "created_at": "2026-05-02T00:00:40Z",
                        "cwd": "/repo/one",
                    },
                ],
            )

            output = build_subagent_workflow_report(
                codex_home,
                since_days=30,
                report_id="workflow-soft-acceptance",
                reports_dir=str(reports_root),
                max_samples=5,
            )

            self.assertEqual(output["closure_signals"]["formatting_only_warnings"], 1)
            self.assertEqual(output["closure_signals"]["hidden_lifecycle_successes"], 2)
            self.assertEqual(output["closure_signals"]["state_claim_conflicts"], 1)
            self.assertEqual(output["closure_signals"]["lifecycle_integrity_issues"], 1)

    def test_analyze_requires_subagent_status_only_when_signal_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            reports_root.mkdir(parents=True, exist_ok=True)
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "analyze.jsonl"
            _write_jsonl(
                transcript,
                [
                    {
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "implemented a narrow fix. Verification: pass.",
                        },
                        "timestamp": "2026-05-02T00:00:00Z",
                    }
                ],
            )

            output = build_report(codex_home, since_days=30, report_id="analyze-subagent", max_samples=5, reports_dir=str(reports_root))

            self.assertEqual(output["acceptance"].get("work_done_missing_subagent_status", 0), 0)

    def test_stop_blocks_unclosed_subagents_without_retained_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "stop.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "please implement this feature"}, "timestamp": "2026-05-02T00:00:00Z"},
                    {"payload": {"type": "function_call", "name": "spawn_agent", "arguments": {"task": "worker"}}, "timestamp": "2026-05-02T00:00:10Z"},
                ],
            )

            output = _run_stop_hook(
                {
                    "transcript_path": str(transcript),
                    "last_assistant_message": (
                        "implemented feature update.\n"
                        "verification_status=pass\n"
                        "review_status=pass\n"
                        "subagent_status=closed\n"
                        "risk_status=low"
                    ),
                }
            )

            self.assertEqual(output["decision"], "block")
            self.assertEqual(output["reason"], "Subagent lifecycle is incomplete.")

    def test_stop_allows_closed_subagents_without_visible_lifecycle_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "stop.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "please implement this feature"}, "timestamp": "2026-05-02T00:00:00Z"},
                    {"payload": {"type": "function_call", "name": "spawn_agent", "arguments": {"task": "worker"}}, "timestamp": "2026-05-02T00:00:10Z"},
                    {"payload": {"type": "function_call", "name": "close_agent", "arguments": {"target": "worker"}}, "timestamp": "2026-05-02T00:00:20Z"},
                ],
            )

            output = _run_stop_hook(
                {
                    "transcript_path": str(transcript),
                    "last_assistant_message": "implemented feature update. verified with targeted review and tests.",
                }
            )

            self.assertTrue(output.get("continue"))
            event = _read_last_observability_event(codex_home, "stop")
            self.assertEqual(event.get("completion_state", {}).get("subagent_lifecycle_state"), "closed")

    def test_stop_allows_heavy_turn_not_used_without_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "stop.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "please implement this feature and add tests"}, "timestamp": "2026-05-02T00:00:00Z"},
                ],
            )

            output = _run_stop_hook(
                {
                    "transcript_path": str(transcript),
                    "last_assistant_message": (
                        "implemented feature update.\n"
                        "verification_status=pass\n"
                        "review_status=pass\n"
                        "subagent_status=not_used\n"
                        "risk_status=low"
                    ),
                }
            )

            self.assertTrue(output.get("continue"))

    def test_stop_allows_heavy_turn_not_used_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "stop.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "please implement this feature and add tests"}, "timestamp": "2026-05-02T00:00:00Z"},
                ],
            )

            output = _run_stop_hook(
                {
                    "transcript_path": str(transcript),
                    "last_assistant_message": (
                        "implemented feature update.\n"
                        "verification_status=pass\n"
                        "review_status=pass\n"
                        "subagent_status=not_used: narrow single-file fix with deterministic validation, so main thread handled it directly.\n"
                        "risk_status=low"
                    ),
                }
            )

            self.assertTrue(output.get("continue"))

    def test_stop_ignores_subagent_marker_strings_inside_tool_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "stop.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "explain this hook behavior"}, "timestamp": "2026-05-02T00:00:00Z"},
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "name": "exec_command",
                            "output": 'source snippet: "<subagent_notification" and "\\"name\\": \\"spawn_agent\\""',
                        },
                        "timestamp": "2026-05-02T00:00:10Z",
                    },
                ],
            )

            output = _run_stop_hook(
                {
                    "transcript_path": str(transcript),
                    "last_assistant_message": (
                        "explained the hook behavior.\n"
                        "verification_status=pass\n"
                        "review_status=not_required\n"
                        "subagent_status=not_used\n"
                        "risk_status=low"
                    ),
                }
            )

            self.assertTrue(output.get("continue"))

    def test_stop_soft_warns_on_missing_footer_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "stop.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "implement the UI cleanup"}, "timestamp": "2026-05-02T00:00:00Z"},
                ],
            )

            output = _run_stop_hook(
                {
                    "transcript_path": str(transcript),
                    "last_assistant_message": "implemented the requested UI cleanup and verified it locally.",
                }
            )

            self.assertTrue(output.get("continue"))
            event = _read_last_observability_event(codex_home, "stop")
            self.assertEqual(event.get("status"), "warn")
            self.assertEqual(event.get("reason"), "formatting_only_missing_acceptance")
            self.assertEqual(event.get("completion_state", {}).get("subagent_lifecycle_state"), "not_used")

    def test_stop_accepts_natural_acceptance_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "stop.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "review the latency case"}, "timestamp": "2026-05-02T00:00:00Z"},
                ],
            )

            output = _run_stop_hook(
                {
                    "transcript_path": str(transcript),
                    "last_assistant_message": (
                        "implemented the report update.\n\n"
                        "ACCEPTANCE:\n"
                        "- Checked target: aiserver-test:/opt/project/midoo_pre\n"
                        "- Checked ES env: aiserver-pre / sg_pre\n"
                        "- Time window: 2026-05-19 14:25-14:37 +0800\n"
                        "- Verification result: expected, enable_word_timestamps=true\n"
                    ),
                }
            )

            self.assertTrue(output.get("continue"))
            event = _read_last_observability_event(codex_home, "stop")
            self.assertEqual(event.get("status"), "warn")
            self.assertEqual(event.get("reason"), "formatting_only_natural_acceptance")

    def test_stop_blocks_pass_footer_when_body_says_pytest_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "stop.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "implement the backend fix"}, "timestamp": "2026-05-02T00:00:00Z"},
                ],
            )

            output = _run_stop_hook(
                {
                    "transcript_path": str(transcript),
                    "last_assistant_message": (
                        "implemented the backend fix. pytest not run in this environment.\n"
                        "verification_status=pass\n"
                        "review_status=pass\n"
                        "subagent_status=not_used\n"
                        "risk_status=low"
                    ),
                }
            )

            self.assertEqual(output["decision"], "block")
            self.assertEqual(output["reason"], "Verification claim conflicts with the summary.")

    def test_stop_blocks_low_risk_footer_when_body_has_remaining_risk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "stop.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "implement the debug panel"}, "timestamp": "2026-05-02T00:00:00Z"},
                ],
            )

            output = _run_stop_hook(
                {
                    "transcript_path": str(transcript),
                    "last_assistant_message": (
                        "implemented the panel. remaining risk: still needs production tunnel validation.\n"
                        "verification_status=pass\n"
                        "review_status=pass\n"
                        "subagent_status=not_used\n"
                        "risk_status=low"
                    ),
                }
            )

            self.assertEqual(output["decision"], "block")
            self.assertEqual(output["reason"], "Risk claim conflicts with the summary.")

    def test_pre_tool_soft_policy_warns_for_broad_scan_without_research_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            (repo / ".codex").mkdir(parents=True, exist_ok=True)
            (repo / ".codex" / "review-profile.toml").write_text(
                'project_id = "demo"\n[workflow_profile]\npretool_policy = "soft"\n',
                encoding="utf-8",
            )
            transcript = repo / ".codex" / "sessions" / "2026" / "05" / "02" / "pre.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "please inspect the repo"}, "timestamp": "2026-05-02T00:00:00Z"},
                ],
            )

            output = _run_pre_tool_hook(
                {
                    "cwd": str(repo),
                    "transcript_path": str(transcript),
                    "tool_name": "bash",
                    "tool_input": {"cmd": "rg --files ."},
                }
            )

            self.assertEqual(output, "")

    def test_pre_tool_hard_policy_blocks_for_broad_scan_without_research_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            (repo / ".codex").mkdir(parents=True, exist_ok=True)
            (repo / ".codex" / "review-profile.toml").write_text(
                'project_id = "demo"\n[workflow_profile]\npretool_policy = "hard"\n',
                encoding="utf-8",
            )
            transcript = repo / ".codex" / "sessions" / "2026" / "05" / "02" / "pre.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "please inspect the repo"}, "timestamp": "2026-05-02T00:00:00Z"},
                ],
            )

            output = json.loads(
                _run_pre_tool_hook(
                    {
                        "cwd": str(repo),
                        "transcript_path": str(transcript),
                        "tool_name": "bash",
                        "tool_input": {"cmd": "rg --files ."},
                    }
                )
            )

            self.assertEqual(output["decision"], "block")

    def test_workflow_profile_maps_project_specific_roles_to_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            (repo / ".codex").mkdir(parents=True, exist_ok=True)
            (repo / ".codex" / "review-profile.toml").write_text(
                (
                    'project_id = "demo"\n'
                    "[role_capabilities]\n"
                    '"aiserver-implementer" = "implementation"\n'
                    '"aiserver-reviewer" = "review"\n'
                    '"aiserver-test-writer" = "test_design"\n'
                ),
                encoding="utf-8",
            )

            self.assertEqual(infer_capability("aiserver-implementer", {"cwd": str(repo)}), "implementation")
            self.assertEqual(infer_capability("aiserver-reviewer", {"cwd": str(repo)}), "review")
            self.assertEqual(infer_capability("aiserver-test-writer", {"cwd": str(repo)}), "test_design")

    def test_analyze_uses_agents_md_as_project_context_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            project = Path(temp_dir) / "repo"
            project.mkdir()
            (project / "AGENTS.md").write_text("Project context from AGENTS.md", encoding="utf-8")
            reports_root = codex_home / "orchestrator-observability" / "reports"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "context.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"type": "session_meta", "payload": {"id": "ctx", "cwd": str(project)}, "timestamp": "2026-05-02T00:00:00Z"},
                    {
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "Implemented context fallback. Verification: unit test.",
                        },
                        "timestamp": "2026-05-02T00:01:00Z",
                    },
                ],
            )

            output = build_report(codex_home, since_days=30, report_id="context", max_samples=5, reports_dir=str(reports_root))

            context = output["project_context"]["project:repo"]
            self.assertTrue(context["available"])
            self.assertEqual(context["source"], "project:repo/AGENTS.md")
            self.assertTrue(output["project_context_available"])

    def test_triage_reads_project_context_from_analyzer_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            project = Path(temp_dir) / "repo"
            project.mkdir()
            (project / "AGENTS.md").write_text("Project context from AGENTS.md", encoding="utf-8")
            reports_root = codex_home / "orchestrator-observability" / "reports"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "context.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"type": "session_meta", "payload": {"id": "ctx", "cwd": str(project)}, "timestamp": "2026-05-02T00:00:00Z"},
                    {"payload": {"type": "message", "role": "assistant", "content": "Nothing to change here."}, "timestamp": "2026-05-02T00:01:00Z"},
                ],
            )
            output = build_report(codex_home, since_days=30, report_id="context-triage", max_samples=5, reports_dir=str(reports_root))
            report = json.loads(Path(output["json_report"]).read_text(encoding="utf-8"))

            severity, reasons, flags = classify(report)

            self.assertTrue(flags["project_context_available"])
            self.assertEqual(severity, "none")
            self.assertNotEqual(reasons, ["setup_debt"])

    def test_analyze_prefers_visible_acceptance_signal_over_footer_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "signal.jsonl"
            _write_jsonl(
                transcript,
                [
                    {
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "Implemented reporting change. Verification: ran unit tests.",
                        },
                        "timestamp": "2026-05-02T00:00:00Z",
                    }
                ],
            )

            output = build_report(codex_home, since_days=30, report_id="signal", max_samples=5, reports_dir=str(reports_root))

            self.assertEqual(output["acceptance"]["work_done_missing_acceptance_footer"], 1)
            self.assertEqual(output["acceptance"]["work_done_acceptance_signal_complete"], 1)
            self.assertEqual(output["acceptance_signal_adoption"]["status"], "healthy")

    def test_subagent_workflow_treats_intentional_broad_scan_bypass_as_observed_not_triage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            events = codex_home / "orchestrator-observability" / "events" / "2026-05-02.jsonl"
            _write_jsonl(
                events,
                [
                    {
                        "event": "orchestrator_observability",
                        "event_name": "pre_tool_use",
                        "status": "allow",
                        "reason": "broad_scan_with_research_capability",
                        "tool_name": "shell_command",
                        "timestamp": "2026-05-02T00:00:00Z",
                    }
                ],
            )

            output = build_subagent_workflow_report(codex_home, since_days=30, report_id="broad", max_samples=5, reports_dir=str(reports_root))

            self.assertEqual(output["workflow_signals"]["broad_scan_bypasses_observed"], 1)
            self.assertEqual(output["workflow_signals"]["broad_scan_bypasses"], 0)
            self.assertEqual(output["workflow_policy"], "observe")

    def test_subagent_workflow_does_not_count_plain_workflow_signal_as_broad_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            events = codex_home / "orchestrator-observability" / "events" / "2026-05-02.jsonl"
            _write_jsonl(
                events,
                [
                    {
                        "event": "orchestrator_observability",
                        "event_name": "pre_tool_use",
                        "status": "allow",
                        "reason": "workflow_signal_detected",
                        "tool_name": "shell_command",
                        "timestamp": "2026-05-02T00:00:00Z",
                    }
                ],
            )

            output = build_subagent_workflow_report(codex_home, since_days=30, report_id="plain-workflow", max_samples=5, reports_dir=str(reports_root))

            self.assertEqual(output["workflow_signals"]["broad_scan_bypasses_observed"], 0)
            self.assertEqual(output["workflow_signals"]["broad_scan_bypasses"], 0)

    def test_subagent_workflow_explained_retained_session_does_not_classify_worse(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            reports_root.mkdir(parents=True, exist_ok=True)
            previous = {
                "generated_at": "2026-05-01T00:00:00Z",
                "report_name": "subagent-workflow-prev.json",
                "report_type": "subagent_workflow",
                "since_days": 30,
                "paths_included": False,
                "subagents": {"unresolved_lifecycle_delta": 0, "unexplained_open_sessions": 0},
                "workflow_signals": {
                    "broad_scan_bypasses": 0,
                    "heavy_work_delegation": {
                        "signal_sessions_without_spawn": 0,
                        "session_coverage_rate": 0.0,
                    },
                },
            }
            (reports_root / "subagent-workflow-prev.json").write_text(json.dumps(previous), encoding="utf-8")
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "retained.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "message", "role": "user", "content": "review this"}, "timestamp": "2026-05-02T00:00:00Z"},
                    {"payload": {"type": "function_call", "name": "spawn_agent", "arguments": {"task": "review"}}, "timestamp": "2026-05-02T00:01:00Z"},
                    {
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "Review started.\nsubagent_status=retained because it continues next turn.",
                        },
                        "timestamp": "2026-05-02T00:02:00Z",
                    },
                ],
            )

            output = build_subagent_workflow_report(codex_home, since_days=30, report_id="retained", max_samples=5, reports_dir=str(reports_root))

            self.assertEqual(output["subagents"]["unresolved_lifecycle_delta"], 0)
            self.assertEqual(output["workflow_policy"], "observe")
            self.assertNotEqual(output["classification"], "worse")

    def test_pre_tool_hard_policy_message_mentions_researcher_subagent_and_exception_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "repo"
            profile = project / ".codex" / "review-profile.toml"
            profile.parent.mkdir(parents=True)
            profile.write_text("[workflow_profile]\npretool_policy = \"hard\"\n", encoding="utf-8")

            output = _run_pre_tool_hook(
                {
                    "cwd": str(project),
                    "tool_name": "shell_command",
                    "tool_input": {"command": "rg --files"},
                }
            )

            self.assertIn("researcher subagent", output)
            self.assertIn("state the exception", output)

    def test_analyze_large_outputs_are_counted_without_raw_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "large.jsonl"
            raw_blob = "X" * 60050
            _write_jsonl(
                transcript,
                [
                    {"payload": {"type": "function_call_output", "output": raw_blob}, "timestamp": "2026-05-02T00:00:00Z"},
                    {"payload": {"type": "custom_tool_call_output", "output": raw_blob}, "timestamp": "2026-05-02T00:01:00Z"},
                ],
            )

            output = build_report(codex_home, since_days=30, report_id="large", max_samples=5, reports_dir=str(reports_root))
            report_text = (reports_root / "orchestrator-observability-large.json").read_text(encoding="utf-8")

            self.assertEqual(output["noise"]["large_tool_outputs"], 2)
            self.assertEqual(output["noise"]["large_function_outputs"], 1)
            self.assertEqual(output["noise"]["large_custom_tool_outputs"], 1)
            self.assertEqual(output["noise"]["large_output_by_project"]["unknown"], 2)
            self.assertNotIn(raw_blob, report_text)


if __name__ == "__main__":
    unittest.main()
