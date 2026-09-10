from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator-observability" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import analyze  # noqa: E402
import subagent_workflow  # noqa: E402


class ObservabilityReportBoundsTests(unittest.TestCase):
    def test_reports_bound_embedded_facts_without_changing_full_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            session_file = codex_home / "sessions" / "2026" / "09" / "10" / "rollout-bounded.jsonl"
            session_file.parent.mkdir(parents=True)
            records = [
                {
                    "type": "session_meta",
                    "payload": {"id": "bounded-session", "cwd": "/repo/bounded"},
                    "timestamp": "2026-09-10T00:00:00Z",
                }
            ]
            records.extend(
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": "user", "content": f"event {index}"},
                    "timestamp": f"2026-09-10T00:{index // 60:02d}:{index % 60:02d}Z",
                }
                for index in range(2105)
            )
            session_file.write_text(
                "\n".join(json.dumps(record, sort_keys=True) for record in records),
                encoding="utf-8",
            )

            builders = (analyze.build_report, subagent_workflow.build_report)
            for index, builder in enumerate(builders):
                with self.subTest(builder=builder.__module__):
                    reports_dir = root / f"reports-{index}"
                    summary = builder(
                        codex_home=codex_home,
                        since_days=3650,
                        report_id="bounded",
                        max_samples=5,
                        reports_dir=str(reports_dir),
                    )
                    self.assertEqual(summary["fact_count"], 2106)
                    self.assertEqual(summary["fact_output"]["emitted"], 2000)
                    self.assertEqual(summary["fact_output"]["omitted"], 106)
                    self.assertTrue(summary["fact_output"]["truncated"])
                    json_path = Path(summary["json_report"])
                    data = json.loads(json_path.read_text(encoding="utf-8"))
                    self.assertEqual(len(data["facts"]), 2000)
                    self.assertLess(json_path.stat().st_size, 8_000_000)


if __name__ == "__main__":
    unittest.main()
