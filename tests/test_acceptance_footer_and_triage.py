from __future__ import annotations

import json
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
from session_facts import extract_facts  # noqa: E402
from triage import classify  # noqa: E402


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")


class AcceptanceFooterAndTriageTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
