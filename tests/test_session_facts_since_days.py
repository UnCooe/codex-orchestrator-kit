from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator-observability" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from session_facts import extract_facts, scan_session_files  # noqa: E402


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")


def _set_mtime(path: Path, timestamp: str) -> None:
    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    epoch = dt.timestamp()
    os.utime(path, (epoch, epoch))


class SessionFactSinceDaysTests(unittest.TestCase):
    def test_tolerates_multiline_json_and_non_dict_records(self) -> None:
        cutoff = datetime(2026, 5, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            transcript = codex_home / "sessions" / "2026" / "05" / "02" / "multiline.jsonl"
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {"id": "multi-session", "cwd": "/repo/multi"},
                                "timestamp": "2026-05-02T00:00:00Z",
                            }
                        ),
                        "{",
                        '  "payload": {',
                        '    "type": "message",',
                        '    "role": "assistant",',
                        '    "content": "implemented multiline parsing. Verification: pass."',
                        "  },",
                        '  "timestamp": "2026-05-02T00:01:00Z"',
                        "}",
                        "229274",
                    ]
                ),
                encoding="utf-8",
            )

            facts, stats = extract_facts([transcript], codex_home, cutoff)

            assistant_facts = [fact for fact in facts if fact.event_type == "transcript_message_assistant"]
            self.assertEqual(len(assistant_facts), 1)
            self.assertEqual(assistant_facts[0].session_id, "multi-session")
            self.assertEqual(stats.get("invalid_lines", 0), 0)
            self.assertEqual(stats.get("non_dict_records", 0), 1)

    def test_skips_stale_session_files_without_reading_them(self) -> None:
        cutoff = datetime(2026, 5, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            old_file = codex_home / "sessions" / "2026" / "04" / "01" / "rollout-old.jsonl"
            recent_file = codex_home / "sessions" / "2026" / "05" / "02" / "rollout-recent.jsonl"
            _write_jsonl(old_file, [
                {"payload": {"type": "message", "role": "assistant", "content": "old"}, "timestamp": "2026-04-01T00:00:00Z"}
            ])
            _write_jsonl(recent_file, [
                {"payload": {"type": "message", "role": "assistant", "content": "implemented recent change. Verification: pass."}, "timestamp": "2026-05-02T00:00:00Z"}
            ])
            _set_mtime(old_file, "2026-04-01T00:00:00Z")
            _set_mtime(recent_file, "2026-05-02T00:00:00Z")

            original_read_text = Path.read_text
            read_paths: list[Path] = []

            def spy_read_text(path: Path, *args, **kwargs):
                if path == old_file:
                    raise AssertionError("stale session file should not be parsed")
                read_paths.append(path)
                return original_read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", spy_read_text):
                facts, stats = extract_facts([old_file, recent_file], codex_home, cutoff)

            self.assertEqual(read_paths, [recent_file])
            self.assertEqual(stats["filtered_files"], 1)
            self.assertEqual({fact.session_id for fact in facts}, {"rollout-recent"})
            self.assertTrue(any(fact.assistant_work_done for fact in facts))

    def test_preserves_old_session_meta_context_for_recent_records(self) -> None:
        cutoff = datetime(2026, 5, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            mixed_file = codex_home / "sessions" / "2026" / "05" / "02" / "rollout-mixed.jsonl"
            _write_jsonl(mixed_file, [
                {
                    "type": "session_meta",
                    "payload": {"id": "mixed-session", "cwd": "C:\\repo\\mixed-project"},
                    "timestamp": "2026-04-01T00:00:00Z",
                },
                {
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": "implemented mixed-age context fix. Verification: pass.",
                    },
                    "timestamp": "2026-05-02T00:00:00Z",
                },
            ])
            _set_mtime(mixed_file, "2026-05-02T00:00:00Z")

            facts, stats = extract_facts([mixed_file], codex_home, cutoff)

            self.assertEqual(stats["filtered_older"], 1)
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0].session_id, "mixed-session")
            self.assertEqual(facts[0].cwd, "C:\\repo\\mixed-project")
            self.assertEqual(facts[0].event_type, "transcript_message_assistant")

    def test_cutoff_aware_discovery_prunes_dated_directories(self) -> None:
        cutoff = datetime(2026, 5, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            old_file = codex_home / "sessions" / "2026" / "04" / "01" / "old.jsonl"
            recent_file = codex_home / "sessions" / "2026" / "05" / "02" / "recent.jsonl"
            direct_file = codex_home / "sessions" / "direct.jsonl"
            _write_jsonl(old_file, [{"timestamp": "2026-04-01T00:00:00Z"}])
            _write_jsonl(recent_file, [{"timestamp": "2026-05-02T00:00:00Z"}])
            _write_jsonl(direct_file, [{"timestamp": "2026-04-01T00:00:00Z"}])

            discovered = set(scan_session_files(codex_home, cutoff=cutoff))

            self.assertNotIn(old_file, discovered)
            self.assertIn(recent_file, discovered)
            self.assertIn(direct_file, discovered)

    def test_path_and_mtime_hints_use_latest_observed_time(self) -> None:
        cutoff = datetime(2026, 5, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            old_path_fresh_mtime = codex_home / "sessions" / "2026" / "04" / "01" / "old-path-fresh-mtime.jsonl"
            fresh_path_old_mtime = codex_home / "sessions" / "2026" / "05" / "02" / "fresh-path-old-mtime.jsonl"
            _write_jsonl(old_path_fresh_mtime, [
                {"payload": {"type": "message", "role": "assistant", "content": "implemented fresh mtime."}, "timestamp": "2026-05-02T00:00:00Z"}
            ])
            _write_jsonl(fresh_path_old_mtime, [
                {"payload": {"type": "message", "role": "assistant", "content": "implemented fresh path."}, "timestamp": "2026-05-02T00:00:00Z"}
            ])
            _set_mtime(old_path_fresh_mtime, "2026-05-02T00:00:00Z")
            _set_mtime(fresh_path_old_mtime, "2026-04-01T00:00:00Z")

            facts, stats = extract_facts([old_path_fresh_mtime, fresh_path_old_mtime], codex_home, cutoff)

            self.assertEqual(stats["filtered_files"], 0)
            self.assertEqual({fact.session_id for fact in facts}, {"old-path-fresh-mtime", "fresh-path-old-mtime"})


if __name__ == "__main__":
    unittest.main()
