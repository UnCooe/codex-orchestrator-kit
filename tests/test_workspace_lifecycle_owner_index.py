from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from workspace_lifecycle_owner_index import index_sessions, unique_owner


def write_session(root: Path, session_id: str, events: list[dict]) -> Path:
    path = root / f"{session_id}.jsonl"
    records = [{
        "type": "session_meta",
        "timestamp": "2026-09-01T00:00:00Z",
        "payload": {"session_id": session_id, "id": session_id, "cwd": "/repo"},
    }]
    records.extend(events)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


class WorkspaceLifecycleOwnerIndexTests(unittest.TestCase):
    def test_indexes_actual_wrapped_worktree_add(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "child"
            write_session(root, "thread-1", [{
                "type": "response_item",
                "timestamp": "2026-09-01T00:01:00Z",
                "payload": {"type": "custom_tool_call", "name": "exec", "input": f'const r = await tools.exec_command({{"cmd":"git worktree add -b feature {target} HEAD"}});'},
            }])
            evidence = index_sessions(root, [target])[str(target.resolve())]
            owner = unique_owner(evidence)
            self.assertIsNotNone(owner)
            self.assertEqual(owner["thread_id"], "thread-1")

    def test_ignores_search_commands_and_compacted_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "child"
            write_session(root, "thread-search", [{
                "type": "response_item",
                "payload": {"type": "custom_tool_call", "name": "exec", "input": f'rg -F "git worktree add {target}" /sessions'},
            }, {
                "type": "compacted",
                "payload": {"replacement_history": [{"text": f"git worktree add {target}"}]},
            }])
            self.assertEqual(index_sessions(root, [target])[str(target.resolve())], [])

    def test_multiple_sessions_are_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "child"
            for session in ("thread-a", "thread-b"):
                write_session(root, session, [{
                    "type": "event_msg",
                    "payload": {"type": "item_completed", "thread_id": session, "item": {"type": "CommandExecution", "command": ["zsh", "-lc", f"git worktree add {target} HEAD"]}},
                }])
            evidence = index_sessions(root, [target])[str(target.resolve())]
            self.assertIsNone(unique_owner(evidence))


if __name__ == "__main__":
    unittest.main()
