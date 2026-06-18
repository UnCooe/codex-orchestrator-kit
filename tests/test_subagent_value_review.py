from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator-observability" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from subagent_value_review import build_report  # noqa: E402


SINCE_TS = datetime(2026, 5, 1, tzinfo=timezone.utc)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")


class SubagentValueReviewTests(unittest.TestCase):
    def test_scores_consumed_compact_closed_subagent_as_good_use(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "value.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"type": "session_meta", "payload": {"id": "sess-good", "cwd": "/repo/good"}, "timestamp": "2026-05-02T00:00:00Z"},
                    {"payload": {"type": "message", "role": "user", "content": "review the auth flow and cite files"}, "timestamp": "2026-05-02T00:00:01Z"},
                    {
                        "payload": {
                            "type": "function_call",
                            "name": "spawn_agent",
                            "call_id": "spawn-1",
                            "arguments": {
                                "agent_type": "reviewer",
                                "message": "Goal: review auth flow. Scope: read-only. Evidence: cite file lines. Output: concise conclusion, risks, missing facts. Done: return recommendation.",
                            },
                        },
                        "timestamp": "2026-05-02T00:00:10Z",
                    },
                    {"payload": {"type": "function_call_output", "call_id": "spawn-1", "output": "{\"agent_id\":\"agent-good\"}"}, "timestamp": "2026-05-02T00:00:11Z"},
                    {"payload": {"type": "function_call", "name": "wait_agent", "call_id": "wait-1", "arguments": {"targets": ["agent-good"]}}, "timestamp": "2026-05-02T00:01:00Z"},
                    {
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "wait-1",
                            "output": "结论: auth flow is safe. Evidence: app/auth.py:42 validates tokens. Risk: low. Missing: none.",
                        },
                        "timestamp": "2026-05-02T00:01:10Z",
                    },
                    {"payload": {"type": "message", "role": "assistant", "content": "The reviewer found app/auth.py:42 validates tokens, so I will keep the plan unchanged."}, "timestamp": "2026-05-02T00:01:20Z"},
                    {"payload": {"type": "function_call", "name": "close_agent", "call_id": "close-1", "arguments": {"target": "agent-good"}}, "timestamp": "2026-05-02T00:01:30Z"},
                ],
            )

            output = build_report(
                codex_home,
                since_days=30,
                report_id="value-good",
                reports_dir=str(reports_root),
                since_ts=SINCE_TS,
            )

            self.assertEqual(output["overall_classification"], "good_use")
            self.assertEqual(output["schema_version"], "subagent-value-review-v2")
            self.assertIn("report_summary", output)
            self.assertIn("issue_groups", output)
            self.assertIn("debug_artifact", output)
            self.assertEqual(output["totals"]["episodes"], 1)
            item = output["subagents"][0]
            self.assertEqual(item["classification"], "good_use")
            self.assertTrue(item["consumed"])
            self.assertGreaterEqual(item["handoff_quality"], 4)
            self.assertGreaterEqual(item["result_quality"], 4)
            self.assertGreaterEqual(item["net_utility"], 4)
            self.assertEqual(item["final_score"], item["net_utility"])
            self.assertIn("raw_scores", item)
            self.assertIn("classification_reason", item)
            markdown = Path(output["markdown_report"]).read_text(encoding="utf-8")
            self.assertIn("# Subagent 工作流复盘摘要", markdown)
            self.assertNotIn("## 3. 每个 Subagent 的价值判断", markdown)
            self.assertNotIn("```json", markdown)

    def test_single_broad_scan_is_low_confidence_missed_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "missed.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"type": "session_meta", "payload": {"id": "sess-missed", "cwd": "/repo/missed"}, "timestamp": "2026-05-02T00:00:00Z"},
                    {"payload": {"type": "message", "role": "user", "content": "map the whole repository architecture"}, "timestamp": "2026-05-02T00:00:01Z"},
                    {"payload": {"type": "function_call", "name": "exec_command", "call_id": "cmd-1", "arguments": {"cmd": "rg --files"}}, "timestamp": "2026-05-02T00:00:10Z"},
                ],
            )

            output = build_report(
                codex_home,
                since_days=30,
                report_id="value-missed",
                reports_dir=str(reports_root),
                since_ts=SINCE_TS,
            )

            self.assertEqual(output["totals"]["missed_use_candidates"], 1)
            candidate = output["missed_use_candidates"][0]
            self.assertEqual(candidate["reason"], "broad_scan_without_delegation")
            self.assertEqual(candidate["recommended_subagent_type"], "researcher")
            self.assertEqual(candidate["confidence"], "false_positive_candidate")
            self.assertTrue(any(item["type"] == "false_positive_missed_delegation" for item in output["issue_groups"]))

    def test_detects_high_confidence_missed_delegation_after_multi_step_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "missed-high.jsonl"
            records = [
                {"type": "session_meta", "payload": {"id": "sess-missed-high", "cwd": "/repo/missed"}, "timestamp": "2026-05-02T00:00:00Z"},
                {"payload": {"type": "message", "role": "user", "content": "map the whole repository architecture"}, "timestamp": "2026-05-02T00:00:01Z"},
            ]
            for idx, cmd in enumerate(["rg --files", "rg --files app", "rg \"class\" app", "rg \"def\" app", "rg \"route\" app"], start=1):
                records.append({"payload": {"type": "function_call", "name": "exec_command", "call_id": f"cmd-{idx}", "arguments": {"cmd": cmd}}, "timestamp": f"2026-05-02T00:00:{10 + idx:02d}Z"})
            _write_jsonl(transcript, records)

            output = build_report(
                codex_home,
                since_days=30,
                report_id="value-missed-high",
                reports_dir=str(reports_root),
                since_ts=SINCE_TS,
            )

            self.assertEqual(output["overall_classification"], "missed_use")
            candidate = output["missed_use_candidates"][0]
            self.assertEqual(candidate["confidence"], "high")
            issue = next(item for item in output["issue_groups"] if item["type"] == "missed_delegation")
            self.assertGreaterEqual(issue["trigger_count"], 1)
            markdown = Path(output["markdown_report"]).read_text(encoding="utf-8")
            self.assertNotIn("## 4. 漏用候选", markdown)

    def test_marks_unconsumed_verbose_unclosed_subagent_as_harmful(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "harmful.jsonl"
            verbose = "raw log line\n" * 2000
            _write_jsonl(
                transcript,
                [
                    {"type": "session_meta", "payload": {"id": "sess-harmful", "cwd": "/repo/harmful"}, "timestamp": "2026-05-02T00:00:00Z"},
                    {"payload": {"type": "message", "role": "user", "content": "debug this incident"}, "timestamp": "2026-05-02T00:00:01Z"},
                    {"payload": {"type": "function_call", "name": "spawn_agent", "call_id": "spawn-1", "arguments": {"message": "look around"}}, "timestamp": "2026-05-02T00:00:10Z"},
                    {"payload": {"type": "function_call_output", "call_id": "spawn-1", "output": "{\"agent_id\":\"agent-bad\"}"}, "timestamp": "2026-05-02T00:00:11Z"},
                    {"payload": {"type": "function_call", "name": "wait_agent", "call_id": "wait-1", "arguments": {"targets": ["agent-bad"]}}, "timestamp": "2026-05-02T00:01:00Z"},
                    {"payload": {"type": "function_call_output", "call_id": "wait-1", "output": verbose}, "timestamp": "2026-05-02T00:01:10Z"},
                    {"payload": {"type": "function_call", "name": "exec_command", "call_id": "cmd-1", "arguments": {"cmd": "rg --files"}}, "timestamp": "2026-05-02T00:01:15Z"},
                    {"payload": {"type": "message", "role": "assistant", "content": "I will inspect the issue directly instead."}, "timestamp": "2026-05-02T00:01:20Z"},
                ],
            )

            output = build_report(
                codex_home,
                since_days=30,
                report_id="value-harmful",
                reports_dir=str(reports_root),
                since_ts=SINCE_TS,
            )

            self.assertEqual(output["overall_classification"], "harmful_use")
            item = output["subagents"][0]
            self.assertEqual(item["classification"], "harmful_use")
            self.assertFalse(item["consumed"])
            self.assertEqual(item["close_reason"], "missing_close")
            self.assertLessEqual(item["net_utility"], 2)
            issue_types = {item["type"] for item in output["issue_groups"]}
            self.assertIn("overlong_result", issue_types)
            self.assertIn("low_compression_value", issue_types)
            self.assertIn("harmful_delegation", issue_types)
            overlong = next(item for item in output["issue_groups"] if item["type"] == "overlong_result")
            self.assertIn(overlong["priority"], {"P0", "P1"})

    def test_missing_result_and_close_are_observability_gap_not_harmful(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "unknown.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"type": "session_meta", "payload": {"id": "sess-unknown", "cwd": "/repo/unknown"}, "timestamp": "2026-05-02T00:00:00Z"},
                    {"payload": {"type": "message", "role": "user", "content": "review this"}, "timestamp": "2026-05-02T00:00:01Z"},
                    {"payload": {"type": "function_call", "name": "spawn_agent", "call_id": "spawn-1", "arguments": {"message": "Goal: review. Scope: read-only. Evidence: cite files. Output: concise. Done: return finding."}}, "timestamp": "2026-05-02T00:00:10Z"},
                    {"payload": {"type": "function_call_output", "call_id": "spawn-1", "output": "{\"agent_id\":\"agent-unknown\"}"}, "timestamp": "2026-05-02T00:00:11Z"},
                ],
            )

            output = build_report(
                codex_home,
                since_days=30,
                report_id="value-unknown",
                reports_dir=str(reports_root),
                since_ts=SINCE_TS,
            )

            item = output["subagents"][0]
            self.assertEqual(item["classification"], "insufficient_evidence")
            self.assertTrue(any(issue["type"] == "observability_gap" for issue in output["issue_groups"]))
            self.assertFalse(any(issue["type"] == "harmful_delegation" for issue in output["issue_groups"]))

    def test_classification_final_score_and_reason_are_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "consistent.jsonl"
            _write_jsonl(
                transcript,
                [
                    {"type": "session_meta", "payload": {"id": "sess-consistent", "cwd": "/repo/consistent"}, "timestamp": "2026-05-02T00:00:00Z"},
                    {"payload": {"type": "message", "role": "user", "content": "review auth"}, "timestamp": "2026-05-02T00:00:01Z"},
                    {"payload": {"type": "function_call", "name": "spawn_agent", "call_id": "spawn-1", "arguments": {"agent_type": "reviewer", "message": "Goal: review. Scope: read-only. Evidence: cite files. Output: concise. Done: return finding."}}, "timestamp": "2026-05-02T00:00:10Z"},
                    {"payload": {"type": "function_call_output", "call_id": "spawn-1", "output": "{\"agent_id\":\"agent-consistent\"}"}, "timestamp": "2026-05-02T00:00:11Z"},
                    {"payload": {"type": "function_call", "name": "wait_agent", "call_id": "wait-1", "arguments": {"targets": ["agent-consistent"]}}, "timestamp": "2026-05-02T00:01:00Z"},
                    {"payload": {"type": "function_call_output", "call_id": "wait-1", "output": "结论: ok. Evidence: app/auth.py:1. Risk: low."}, "timestamp": "2026-05-02T00:01:10Z"},
                ],
            )

            output = build_report(
                codex_home,
                since_days=30,
                report_id="value-consistent",
                reports_dir=str(reports_root),
                since_ts=SINCE_TS,
            )

            item = output["subagents"][0]
            self.assertEqual(item["final_score"], item["net_utility"])
            self.assertTrue(item["classification_reason"])
            if item["final_score"] >= 4 and item["classification"] == "ineffective_use":
                self.assertTrue(item["confidence_penalties"])

    def test_dedupes_same_tool_call_across_rollout_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            reports_root = codex_home / "orchestrator-observability" / "reports"
            records = [
                {"type": "session_meta", "payload": {"id": "sess-dup", "cwd": "/repo/dup"}, "timestamp": "2026-05-20T00:00:00Z"},
                {"payload": {"type": "message", "role": "user", "content": "review the repository"}, "timestamp": "2026-05-20T00:00:01Z"},
                {
                    "payload": {
                        "type": "function_call",
                        "name": "spawn_agent",
                        "call_id": "spawn-dup",
                        "arguments": {"agent_type": "reviewer", "message": "Goal: review. Scope: read-only. Evidence: cite files. Output: concise. Done: return finding."},
                    },
                    "timestamp": "2026-05-20T00:00:10Z",
                },
                {"payload": {"type": "function_call_output", "call_id": "spawn-dup", "output": "{\"agent_id\":\"agent-dup\"}"}, "timestamp": "2026-05-20T00:00:11Z"},
            ]
            _write_jsonl(codex_home / "sessions" / "2026" / "05" / "20" / "rollout-a.jsonl", records)
            shifted = [{**record, "timestamp": "2026-05-20T00:05:00Z"} if record.get("payload", {}).get("type") in {"function_call", "function_call_output"} else record for record in records]
            _write_jsonl(codex_home / "sessions" / "2026" / "05" / "20" / "rollout-b.jsonl", shifted)

            output = build_report(
                codex_home,
                since_days=7,
                report_id="value-dedupe",
                reports_dir=str(reports_root),
                since_ts=datetime(2026, 5, 20, tzinfo=timezone.utc),
            )

            self.assertEqual(output["totals"]["episodes"], 1)
            self.assertGreaterEqual(output["totals"].get("deduped_events", 0), 2)


if __name__ == "__main__":
    unittest.main()
