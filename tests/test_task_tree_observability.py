from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TASK_TREE_SCRIPT = ROOT / "skills" / "orchestrator-observability" / "scripts" / "task_tree.py"
ROOT_ID = "00000000-0000-4000-8000-000000000001"
CHILD_ID = "00000000-0000-4000-8000-000000000002"
GRANDCHILD_ID = "00000000-0000-4000-8000-000000000003"
OPEN_CHILD_ID = "00000000-0000-4000-8000-000000000004"
UNRELATED_ID = "00000000-0000-4000-8000-999999999999"
SECOND_CHILD_ID = "00000000-0000-4000-8000-000000000005"


def _load_task_tree():
    spec = importlib.util.spec_from_file_location("task_tree_contract", TASK_TREE_SCRIPT)
    if not spec or not spec.loader:
        raise AssertionError(f"cannot load {TASK_TREE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records), encoding="utf-8")


def _session_file(root: Path, session_id: str) -> Path:
    return root / f"rollout-{session_id}.jsonl"


def _meta(session_id: str, *, parent: str | None = None, depth: int = 0, role: str = "root", path: str = "/root") -> dict:
    source: dict = {}
    if parent is not None:
        source["subagent"] = {
            "thread_spawn": {
                "parent_thread_id": parent,
                "depth": depth,
                "agent_path": path,
                "agent_nickname": Path(path).name or role,
                "agent_role": role,
            }
        }
    return {
        "type": "session_meta",
        "payload": {
            "id": session_id,
            "parent_thread_id": parent,
            "source": source,
            "agent_path": path,
            "agent_role": role,
        },
        "timestamp": "2026-07-23T00:00:00Z",
    }


def _event(session_id: str, kind: str, timestamp: str, **extra: object) -> dict:
    payload = {"type": kind}
    payload.update(extra)
    return {
        "type": "event_msg",
        "payload": payload,
        "session_id": session_id,
        "timestamp": timestamp,
    }


def _call(session_id: str, name: str, call_id: str, timestamp: str, arguments: dict | None = None) -> dict:
    return {
        "payload": {
            "type": "function_call",
            "name": name,
            "call_id": call_id,
            "arguments": json.dumps(arguments or {}, sort_keys=True),
        },
        "session_id": session_id,
        "timestamp": timestamp,
    }


def _call_output(session_id: str, call_id: str, timestamp: str, output: str) -> dict:
    return {
        "payload": {"type": "function_call_output", "call_id": call_id, "output": output},
        "session_id": session_id,
        "timestamp": timestamp,
    }


def _sub_agent_activity(session_id: str, call_id: str, timestamp: str, **payload: object) -> dict:
    value = {"type": "sub_agent_activity", "kind": "interacted", "event_id": call_id}
    value.update(payload)
    return {
        "type": "event_msg",
        "payload": value,
        "session_id": session_id,
        "timestamp": timestamp,
    }


def _tokens(session_id: str, timestamp: str, *, input_tokens: int, cached: int, cache_write: int, output: int, reasoning: int) -> dict:
    total = input_tokens + output
    return {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached,
                    "cache_write_input_tokens": cache_write,
                    "output_tokens": output,
                    "reasoning_output_tokens": reasoning,
                    "total_tokens": total,
                }
            },
        },
        "session_id": session_id,
        "timestamp": timestamp,
        }


def _complete(session_id: str, started_at: str, completed_at: str, duration_ms: int, first_token_ms: int) -> dict:
    return _event(
        session_id,
        "task_complete",
        completed_at,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        time_to_first_token_ms=first_token_ms,
    )


def _turn_context(session_id: str, timestamp: str, *, model: str, effort: str) -> dict:
    return {
        "type": "turn_context",
        "payload": {
            "model": model,
            "effort": effort,
        },
        "session_id": session_id,
        "timestamp": timestamp,
    }


class TaskTreeObservabilityContractTests(unittest.TestCase):
    def test_peer_message_edges_record_direction_hash_bytes_and_delivery(self) -> None:
        task_tree = _load_task_tree()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            left_file = _session_file(root, CHILD_ID)
            right_file = _session_file(root, SECOND_CHILD_ID)
            encrypted_content = "codex-v0144.1:sealed:left-to-right:general:001"

            _write_jsonl(
                root_file,
                [
                    _meta(ROOT_ID, path="/root"),
                    _call(ROOT_ID, "spawn_agent", "spawn-left", "2026-07-23T00:00:01Z", {"task_name": "left"}),
                    _call_output(ROOT_ID, "spawn-left", "2026-07-23T00:00:02Z", '{"task_name":"/root/left"}'),
                    _call(ROOT_ID, "spawn_agent", "spawn-right", "2026-07-23T00:00:03Z", {"task_name": "right"}),
                    _call_output(ROOT_ID, "spawn-right", "2026-07-23T00:00:04Z", '{"task_name":"/root/right"}'),
                ],
            )
            _write_jsonl(
                left_file,
                [
                    _meta(CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/left"),
                    _call(
                        CHILD_ID,
                        "send_message",
                        "peer-send",
                        "2026-07-23T00:00:05Z",
                        {"target": "/root/right", "encrypted_content": encrypted_content},
                    ),
                    _call_output(CHILD_ID, "peer-send", "2026-07-23T00:00:06Z", ""),
                    _complete(
                        CHILD_ID,
                        "2026-07-23T00:00:01Z",
                        "2026-07-23T00:00:07Z",
                        duration_ms=6000,
                        first_token_ms=1000,
                    ),
                ],
            )
            _write_jsonl(
                right_file,
                [
                    _meta(SECOND_CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/right"),
                    {
                        "type": "response_item",
                        "session_id": SECOND_CHILD_ID,
                        "timestamp": "2026-07-23T00:00:06Z",
                        "payload": {
                            "type": "agent_message",
                            "from": "/root/left",
                            "encrypted_content": encrypted_content,
                        },
                    },
                    _complete(
                        SECOND_CHILD_ID,
                        "2026-07-23T00:00:02Z",
                        "2026-07-23T00:00:08Z",
                        duration_ms=6000,
                        first_token_ms=1000,
                    ),
                ],
            )

            report = task_tree.analyze_session_tree([root_file, left_file, right_file])

        edge = report["message_edges"][0]
        self.assertEqual(edge["source_session_id"], CHILD_ID)
        self.assertEqual(edge["target_session_id"], SECOND_CHILD_ID)
        self.assertEqual(edge["source_task_path"], "/root/left")
        self.assertEqual(edge["target_task_path"], "/root/right")
        self.assertEqual(edge["direction"], "child_to_child")
        self.assertEqual(
            set(report["aggregate"]["message_edges"]),
            {"child_to_child", "root_to_child", "child_to_root", "unknown"},
        )
        self.assertEqual(edge["tool_name"], "send_message")
        self.assertEqual(edge["delivery_status"], "delivered")
        self.assertEqual(edge["encrypted_content_bytes"], len(encrypted_content.encode("utf-8")))
        self.assertEqual(
            edge["encrypted_content_sha256"],
            "e7b6f2d6d68dda722de2d780afa3cff2c842e8847ad71134c4c6f36dae39d2a5",
        )
        self.assertNotIn("encrypted_content", edge)
        self.assertNotIn("message", edge)
        self.assertEqual(report["aggregate"]["message_edges"]["child_to_child"], 1)
        self.assertEqual(report["scope_violations"], [])

    def test_codex_0144_agent_message_delivery_matches_ciphertext_without_plaintext(self) -> None:
        task_tree = _load_task_tree()
        encrypted_content = "codex-v0144.1:sealed:source_lane-to-test_lane:PM01:001"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            source_file = _session_file(root, CHILD_ID)
            test_file = _session_file(root, SECOND_CHILD_ID)
            _write_jsonl(
                root_file,
                [
                    _meta(ROOT_ID, path="/root"),
                    _call(ROOT_ID, "spawn_agent", "spawn-source", "2026-07-23T00:00:01Z", {"task_name": "source_lane"}),
                    _call_output(ROOT_ID, "spawn-source", "2026-07-23T00:00:02Z", '{"task_name":"/root/source_lane"}'),
                    _call(ROOT_ID, "spawn_agent", "spawn-test", "2026-07-23T00:00:03Z", {"task_name": "test_lane"}),
                    _call_output(ROOT_ID, "spawn-test", "2026-07-23T00:00:04Z", '{"task_name":"/root/test_lane"}'),
                ],
            )
            _write_jsonl(
                source_file,
                [
                    _meta(CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/source_lane"),
                    _call(
                        CHILD_ID,
                        "send_message",
                        "send-encrypted",
                        "2026-07-23T00:00:05Z",
                        {"target": "/root/test_lane", "encrypted_content": encrypted_content},
                    ),
                    _call_output(CHILD_ID, "send-encrypted", "2026-07-23T00:00:06Z", ""),
                ],
            )
            _write_jsonl(
                test_file,
                [
                    _meta(SECOND_CHILD_ID, parent=ROOT_ID, depth=1, role="test_designer", path="/root/test_lane"),
                    {
                        "type": "response_item",
                        "session_id": SECOND_CHILD_ID,
                        "timestamp": "2026-07-23T00:00:07Z",
                        "payload": {
                            "type": "agent_message",
                            "from": "/root/source_lane",
                            "encrypted_content": encrypted_content,
                        },
                    },
                ],
            )

            report = task_tree.analyze_session_tree([root_file, source_file, test_file])

        edge = report["message_edges"][0]
        self.assertEqual(edge["source_task_path"], "/root/source_lane")
        self.assertEqual(edge["target_task_path"], "/root/test_lane")
        self.assertEqual(edge["direction"], "child_to_child")
        self.assertEqual(edge["delivery_status"], "delivered")
        self.assertEqual(edge["delivery_evidence"], "receiver_agent_message_encrypted_content")
        self.assertEqual(edge["encrypted_content_sha256"], "9b3e7ed1b5822c32bdeb037e94d3aebbbc4025f3e28552743b49812ec2bc03d1")
        self.assertEqual(edge["encrypted_content_bytes"], len(encrypted_content.encode("utf-8")))
        self.assertNotIn("encrypted_content", edge)
        self.assertNotIn("message", edge)
        self.assertNotIn("plaintext", edge)
        self.assertEqual(report["aggregate"]["message_edges"]["child_to_child"], 1)
        self.assertEqual(report["aggregate"]["root_relay_count"], 0)

    def test_sub_agent_activity_interacted_is_primary_delivery_evidence(self) -> None:
        task_tree = _load_task_tree()
        encrypted_content = "codex-v0144.1:sealed:activity-primary:001"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            source_file = _session_file(root, CHILD_ID)
            test_file = _session_file(root, SECOND_CHILD_ID)
            _write_jsonl(
                root_file,
                [
                    _meta(ROOT_ID, path="/root"),
                    _call(ROOT_ID, "spawn_agent", "spawn-source", "2026-07-23T00:00:01Z", {"task_name": "source_lane"}),
                    _call_output(ROOT_ID, "spawn-source", "2026-07-23T00:00:02Z", '{"task_name":"/root/source_lane"}'),
                    _call(ROOT_ID, "spawn_agent", "spawn-test", "2026-07-23T00:00:03Z", {"task_name": "test_lane"}),
                    _call_output(ROOT_ID, "spawn-test", "2026-07-23T00:00:04Z", '{"task_name":"/root/test_lane"}'),
                ],
            )
            _write_jsonl(
                source_file,
                [
                    _meta(CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/source_lane"),
                    _call(
                        CHILD_ID,
                        "send_message",
                        "activity-send",
                        "2026-07-23T00:00:05Z",
                        {"target": "/root/test_lane", "encrypted_content": encrypted_content},
                    ),
                    _sub_agent_activity(
                        CHILD_ID,
                        "activity-send",
                        "2026-07-23T00:00:06Z",
                        target="/root/test_lane",
                        status="delivered",
                    ),
                    _call_output(CHILD_ID, "activity-send", "2026-07-23T00:00:07Z", ""),
                ],
            )
            _write_jsonl(
                test_file,
                [_meta(SECOND_CHILD_ID, parent=ROOT_ID, depth=1, role="test_designer", path="/root/test_lane")],
            )

            report = task_tree.analyze_session_tree([root_file, source_file, test_file])

        edge = report["message_edges"][0]
        self.assertEqual(edge["delivery_status"], "delivered")
        self.assertEqual(edge["delivery_evidence"], "sub_agent_activity_interacted")
        self.assertEqual(edge["activity_target_task_path"], "/root/test_lane")
        self.assertEqual(edge["target_mismatch"], False)

    def test_sub_agent_activity_target_mismatch_is_visible_and_not_silently_delivered(self) -> None:
        task_tree = _load_task_tree()
        encrypted_content = "codex-v0144.1:sealed:activity-mismatch:001"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            source_file = _session_file(root, CHILD_ID)
            test_file = _session_file(root, SECOND_CHILD_ID)
            _write_jsonl(
                root_file,
                [
                    _meta(ROOT_ID, path="/root"),
                    _call(ROOT_ID, "spawn_agent", "spawn-source", "2026-07-23T00:00:01Z", {"task_name": "source_lane"}),
                    _call_output(ROOT_ID, "spawn-source", "2026-07-23T00:00:02Z", '{"task_name":"/root/source_lane"}'),
                    _call(ROOT_ID, "spawn_agent", "spawn-test", "2026-07-23T00:00:03Z", {"task_name": "test_lane"}),
                    _call_output(ROOT_ID, "spawn-test", "2026-07-23T00:00:04Z", '{"task_name":"/root/test_lane"}'),
                ],
            )
            _write_jsonl(
                source_file,
                [
                    _meta(CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/source_lane"),
                    _call(
                        CHILD_ID,
                        "send_message",
                        "activity-send",
                        "2026-07-23T00:00:05Z",
                        {"target": "/root/test_lane", "encrypted_content": encrypted_content},
                    ),
                    _sub_agent_activity(
                        CHILD_ID,
                        "activity-send",
                        "2026-07-23T00:00:06Z",
                        target="/root/other_lane",
                        status="delivered",
                    ),
                    _call_output(CHILD_ID, "activity-send", "2026-07-23T00:00:07Z", ""),
                ],
            )
            _write_jsonl(
                test_file,
                [_meta(SECOND_CHILD_ID, parent=ROOT_ID, depth=1, role="test_designer", path="/root/test_lane")],
            )

            report = task_tree.analyze_session_tree([root_file, source_file, test_file])

        edge = report["message_edges"][0]
        self.assertEqual(edge["delivery_status"], "target_mismatch")
        self.assertEqual(edge["delivery_evidence"], "sub_agent_activity_interacted")
        self.assertEqual(edge["activity_target_task_path"], "/root/other_lane")
        self.assertEqual(edge["target_mismatch"], True)

    def test_root_and_child_token_usage_are_reported_as_distinct_metric_families(self) -> None:
        task_tree = _load_task_tree()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            child_file = _session_file(root, CHILD_ID)
            _write_jsonl(
                root_file,
                [
                    _meta(ROOT_ID, path="/root"),
                    _call(ROOT_ID, "spawn_agent", "spawn-child", "2026-07-23T00:00:01Z", {"task_name": "child"}),
                    _call_output(ROOT_ID, "spawn-child", "2026-07-23T00:00:02Z", '{"task_name":"/root/child"}'),
                    _tokens(ROOT_ID, "2026-07-23T00:00:03Z", input_tokens=300, cached=30, cache_write=10, output=70, reasoning=20),
                ],
            )
            _write_jsonl(
                child_file,
                [
                    _meta(CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/child"),
                    _tokens(CHILD_ID, "2026-07-23T00:00:04Z", input_tokens=90, cached=9, cache_write=3, output=25, reasoning=8),
                ],
            )

            report = task_tree.analyze_session_tree([root_file, child_file])

        aggregate = report["aggregate"]
        self.assertEqual(aggregate["root_token_usage"]["total_tokens"], 370)
        self.assertEqual(aggregate["child_token_usage"]["total_tokens"], 115)
        self.assertEqual(aggregate["token_usage"]["total_tokens"], 485)
        self.assertEqual(report["roots"][0]["own_token_usage"]["total_tokens"], 370)
        self.assertEqual(report["roots"][0]["children"][0]["own_token_usage"]["total_tokens"], 115)

    def test_peer_messages_that_change_scope_are_explicit_scope_violations(self) -> None:
        task_tree = _load_task_tree()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            child_file = _session_file(root, CHILD_ID)
            second_file = _session_file(root, SECOND_CHILD_ID)
            _write_jsonl(
                root_file,
                [
                    _meta(ROOT_ID, path="/root"),
                    _call(ROOT_ID, "spawn_agent", "spawn-child", "2026-07-23T00:00:01Z", {"task_name": "child"}),
                    _call_output(ROOT_ID, "spawn-child", "2026-07-23T00:00:02Z", '{"task_name":"/root/child"}'),
                    _call(ROOT_ID, "spawn_agent", "spawn-second", "2026-07-23T00:00:03Z", {"task_name": "second"}),
                    _call_output(ROOT_ID, "spawn-second", "2026-07-23T00:00:04Z", '{"task_name":"/root/second"}'),
                ],
            )
            _write_jsonl(
                child_file,
                [
                    _meta(CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/child"),
                    _call(
                        CHILD_ID,
                        "send_message",
                        "scope-send",
                        "2026-07-23T00:00:05Z",
                        {
                            "target": "/root/second",
                            "message": "You may edit app/routes/tts/v6/session.py to resolve this.",
                        },
                    ),
                    _call_output(CHILD_ID, "scope-send", "2026-07-23T00:00:06Z", ""),
                ],
            )
            _write_jsonl(
                second_file,
                [_meta(SECOND_CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/second")],
            )

            report = task_tree.analyze_session_tree(
                [root_file, child_file, second_file],
                peer_message_policy={
                    "allowed_edges": [
                        {
                            "from_task": "/root/child",
                            "to_task": "/root/second",
                            "allowed_purposes": ["fact_check"],
                        }
                    ],
                    "write_boundary": "read_only",
                },
            )

        self.assertEqual(report["scope_violations"][0]["violation_id"], "peer_message_changed_write_boundary")
        self.assertEqual(report["scope_violations"][0]["source_task_path"], "/root/child")
        self.assertEqual(report["scope_violations"][0]["target_task_path"], "/root/second")
        self.assertEqual(report["aggregate"]["scope_violation_count"], 1)

    def test_role_contracts_are_loaded_from_the_executed_policy_agents_root(self) -> None:
        task_tree = _load_task_tree()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agents_root = root / "executed-policy" / "agents"
            agents_root.mkdir(parents=True)
            (agents_root / "researcher.toml").write_text(
                '\n'.join(
                    (
                        'name = "researcher"',
                        'model = "policy-specific-model"',
                        'model_reasoning_effort = "low"',
                        'developer_instructions = "Role contract ID: subagent-role-v9.9/policy_researcher."',
                    )
                ),
                encoding="utf-8",
            )
            root_file = _session_file(root, ROOT_ID)
            child_file = _session_file(root, CHILD_ID)
            _write_jsonl(
                root_file,
                [
                    _meta(ROOT_ID, path="/root"),
                    _call(
                        ROOT_ID,
                        "spawn_agent",
                        "spawn-child",
                        "2026-07-23T00:00:01Z",
                        {"task_name": "child", "agent_type": "researcher"},
                    ),
                    _call_output(ROOT_ID, "spawn-child", "2026-07-23T00:00:02Z", '{"task_name":"/root/child"}'),
                ],
            )
            _write_jsonl(
                child_file,
                [_meta(CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/child")],
            )

            report = task_tree.analyze_session_tree(
                [root_file, child_file],
                agents_root=agents_root,
            )

        child = report["roots"][0]["children"][0]
        self.assertEqual(child["role_contract_id"], "subagent-role-v9.9/policy_researcher")
        self.assertEqual(child["configured_model"], "unknown")
        self.assertEqual(child["configured_reasoning_effort"], "unknown")

    def test_analyzes_current_runtime_task_tree_without_close_agent(self) -> None:
        task_tree = _load_task_tree()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            child_file = _session_file(root, CHILD_ID)
            grandchild_file = _session_file(root, GRANDCHILD_ID)
            open_child_file = _session_file(root, OPEN_CHILD_ID)

            _write_jsonl(
                root_file,
                [
                    _meta(UNRELATED_ID, path="/unrelated"),
                    _meta(ROOT_ID, path="/root"),
                    _event(ROOT_ID, "task_started", "2026-07-23T00:00:00Z"),
                    _call(ROOT_ID, "spawn_agent", "spawn-child", "2026-07-23T00:00:01Z", {"task_name": "child"}),
                    _call_output(ROOT_ID, "spawn-child", "2026-07-23T00:00:02Z", '{"task_name":"/root/child"}'),
                    _call(ROOT_ID, "wait_agent", "wait-child", "2026-07-23T00:00:04Z", {"target": "/root/child"}),
                    _call(ROOT_ID, "followup_task", "follow-child", "2026-07-23T00:00:05Z", {"target": "/root/child"}),
                    _call(ROOT_ID, "spawn_agent", "spawn-open", "2026-07-23T00:00:08Z", {"task_name": "open"}),
                    _call_output(ROOT_ID, "spawn-open", "2026-07-23T00:00:09Z", '{"task_name":"/root/open"}'),
                    _tokens(ROOT_ID, "2026-07-23T00:00:10Z", input_tokens=100, cached=10, cache_write=5, output=20, reasoning=7),
                    _tokens(ROOT_ID, "2026-07-23T00:00:20Z", input_tokens=150, cached=25, cache_write=8, output=35, reasoning=11),
                ],
            )
            _write_jsonl(
                child_file,
                [
                    _meta(CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/child"),
                    _event(CHILD_ID, "task_started", "2026-07-23T00:00:03Z"),
                    _call(CHILD_ID, "spawn_agent", "spawn-grand", "2026-07-23T00:00:06Z", {"task_name": "grandchild"}),
                    _call_output(CHILD_ID, "spawn-grand", "2026-07-23T00:00:06Z", '{"task_name":"/root/child/grandchild"}'),
                    _complete(
                        CHILD_ID,
                        "2026-07-23T00:00:03Z",
                        "2026-07-23T00:00:12Z",
                        duration_ms=9000,
                        first_token_ms=1500,
                    ),
                    _tokens(CHILD_ID, "2026-07-23T00:00:13Z", input_tokens=40, cached=5, cache_write=2, output=8, reasoning=3),
                ],
            )
            _write_jsonl(
                grandchild_file,
                [
                    _meta(GRANDCHILD_ID, parent=CHILD_ID, depth=2, role="researcher", path="/root/child/grandchild"),
                    _event(GRANDCHILD_ID, "task_started", "2026-07-23T00:00:07Z"),
                    _complete(
                        GRANDCHILD_ID,
                        "2026-07-23T00:00:07Z",
                        "2026-07-23T00:00:11Z",
                        duration_ms=4000,
                        first_token_ms=1000,
                    ),
                ],
            )
            _write_jsonl(
                open_child_file,
                [
                    _meta(OPEN_CHILD_ID, parent=ROOT_ID, depth=1, role="reviewer", path="/root/open"),
                    _event(OPEN_CHILD_ID, "task_started", "2026-07-23T00:00:10Z"),
                ],
            )

            report = task_tree.analyze_session_tree([root_file, child_file, grandchild_file, open_child_file])

        self.assertEqual([item["session_id"] for item in report["roots"]], [ROOT_ID])
        root_node = report["roots"][0]
        self.assertEqual(root_node["max_depth"], 2)
        self.assertEqual(root_node["counts"]["spawn_agent"], 3)
        self.assertEqual(root_node["counts"]["followup_task"], 1)
        self.assertEqual(root_node["counts"]["wait_agent"], 1)
        self.assertEqual(root_node["peak_active_children"], 3)
        self.assertEqual(root_node["timing"]["wall_clock_seconds"], None)
        self.assertEqual(root_node["timing"]["source"], "unknown")

        children = {child["session_id"]: child for child in root_node["children"]}
        self.assertEqual(set(children), {CHILD_ID, OPEN_CHILD_ID})
        self.assertEqual(children[CHILD_ID]["agent_path"], "/root/child")
        self.assertEqual(children[CHILD_ID]["parent_thread_id"], ROOT_ID)
        self.assertEqual(children[CHILD_ID]["status"], "completed")
        self.assertEqual(children[CHILD_ID]["timing"]["duration_ms"], 9000)
        self.assertEqual(children[CHILD_ID]["timing"]["time_to_first_token_ms"], 1500)
        self.assertEqual(children[OPEN_CHILD_ID]["status"], "open")
        self.assertEqual(children[CHILD_ID]["children"][0]["session_id"], GRANDCHILD_ID)
        self.assertEqual(children[CHILD_ID]["children"][0]["agent_path"], "/root/child/grandchild")
        self.assertEqual(children[CHILD_ID]["children"][0]["status"], "completed")

        latest_root_usage = root_node["token_usage"]
        self.assertEqual(latest_root_usage["input_tokens"], 150)
        self.assertEqual(latest_root_usage["cached_input_tokens"], 25)
        self.assertEqual(latest_root_usage["cache_write_input_tokens"], 8)
        self.assertEqual(latest_root_usage["output_tokens"], 35)
        self.assertEqual(latest_root_usage["reasoning_output_tokens"], 11)
        self.assertEqual(latest_root_usage["total_tokens"], 185)

        aggregate = report["aggregate"]
        self.assertEqual(aggregate["roots"], 1)
        self.assertEqual(aggregate["sessions"], 4)
        self.assertEqual(aggregate["max_depth"], 2)
        self.assertEqual(aggregate["peak_active_children"], 3)
        self.assertEqual(aggregate["open_children"], 1)
        self.assertEqual(aggregate["completed_children"], 2)
        self.assertEqual(aggregate["token_usage"]["input_tokens"], 190)
        self.assertEqual(aggregate["token_usage"]["cached_input_tokens"], 30)
        self.assertEqual(aggregate["token_usage"]["cache_write_input_tokens"], 10)
        self.assertEqual(aggregate["token_usage"]["output_tokens"], 43)
        self.assertEqual(aggregate["token_usage"]["reasoning_output_tokens"], 14)
        self.assertEqual(aggregate["token_usage"]["total_tokens"], 233)

    def test_legacy_spawn_agent_id_output_is_kept_as_compatibility_only(self) -> None:
        task_tree = _load_task_tree()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            child_file = _session_file(root, CHILD_ID)
            _write_jsonl(
                root_file,
                [
                    _meta(ROOT_ID, path="/root"),
                    _event(ROOT_ID, "task_started", "2026-07-23T00:00:00Z"),
                    _call(ROOT_ID, "spawn_agent", "spawn-child", "2026-07-23T00:00:01Z", {"task_name": "child"}),
                    _call_output(ROOT_ID, "spawn-child", "2026-07-23T00:00:02Z", f'{{"agent_id":"{CHILD_ID}"}}'),
                ],
            )
            _write_jsonl(
                child_file,
                [
                    _meta(CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/child"),
                    _event(CHILD_ID, "task_started", "2026-07-23T00:00:03Z"),
                    _complete(
                        CHILD_ID,
                        "2026-07-23T00:00:03Z",
                        "2026-07-23T00:00:04Z",
                        duration_ms=1000,
                        first_token_ms=250,
                    ),
                ],
            )

            report = task_tree.analyze_session_tree([root_file, child_file])

        self.assertEqual(report["roots"][0]["children"][0]["session_id"], CHILD_ID)

    def test_child_without_terminal_event_remains_open_even_without_task_started(self) -> None:
        task_tree = _load_task_tree()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            child_file = _session_file(root, CHILD_ID)
            _write_jsonl(root_file, [_meta(ROOT_ID, path="/root")])
            _write_jsonl(
                child_file,
                [_meta(CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/child")],
            )

            report = task_tree.analyze_session_tree([root_file, child_file])

        self.assertEqual(report["roots"][0]["children"][0]["status"], "unknown")
        self.assertEqual(report["aggregate"]["open_children"], 1)
        self.assertEqual(report["aggregate"]["completed_children"], 0)

    def test_attributable_omitted_agent_type_normalizes_to_builtin_default_role(self) -> None:
        task_tree = _load_task_tree()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            child_file = _session_file(root, CHILD_ID)
            _write_jsonl(
                root_file,
                [
                    _meta(ROOT_ID, path="/root"),
                    _call(ROOT_ID, "spawn_agent", "spawn-child", "2026-07-23T00:00:01Z", {"task_name": "child"}),
                    _call_output(ROOT_ID, "spawn-child", "2026-07-23T00:00:02Z", '{"task_name":"/root/child"}'),
                ],
            )
            _write_jsonl(
                child_file,
                [_meta(CHILD_ID, parent=ROOT_ID, depth=1, role=None, path="/root/child")],
            )

            report = task_tree.analyze_session_tree([root_file, child_file])

        child = report["roots"][0]["children"][0]
        self.assertEqual(child["role"], "default")
        self.assertEqual(child["role_source"], "builtin")
        self.assertEqual(child["role_contract_id"], "subagent-role-v4.2/default")

    def test_null_role_without_attributable_spawn_remains_unknown(self) -> None:
        task_tree = _load_task_tree()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            child_file = _session_file(root, CHILD_ID)
            _write_jsonl(root_file, [_meta(ROOT_ID, path="/root")])
            _write_jsonl(
                child_file,
                [_meta(CHILD_ID, parent=ROOT_ID, depth=1, role=None, path="/root/child")],
            )

            report = task_tree.analyze_session_tree([root_file, child_file])

        child = report["roots"][0]["children"][0]
        self.assertEqual(child["role"], "unknown")
        self.assertEqual(child["role_source"], "unknown")

    def test_omitted_agent_type_requires_a_successful_spawn_target(self) -> None:
        task_tree = _load_task_tree()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            child_file = _session_file(root, CHILD_ID)
            _write_jsonl(
                root_file,
                [
                    _meta(ROOT_ID, path="/root"),
                    _call(ROOT_ID, "spawn_agent", "spawn-child", "2026-07-23T00:00:01Z", {"task_name": "child"}),
                    _call_output(ROOT_ID, "spawn-child", "2026-07-23T00:00:02Z", "{}"),
                ],
            )
            _write_jsonl(
                child_file,
                [_meta(CHILD_ID, parent=ROOT_ID, depth=1, role=None, path="/root/child")],
            )

            report = task_tree.analyze_session_tree([root_file, child_file])

        child = report["roots"][0]["children"][0]
        self.assertEqual(child["role"], "unknown")
        self.assertEqual(child["role_source"], "unknown")

    def test_omitted_agent_type_requires_the_declared_parent_to_match(self) -> None:
        task_tree = _load_task_tree()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            other_parent_file = _session_file(root, SECOND_CHILD_ID)
            child_file = _session_file(root, CHILD_ID)
            _write_jsonl(root_file, [_meta(ROOT_ID, path="/root")])
            _write_jsonl(
                other_parent_file,
                [
                    _meta(SECOND_CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/other"),
                    _call(SECOND_CHILD_ID, "spawn_agent", "spawn-child", "2026-07-23T00:00:01Z", {"task_name": "child"}),
                    _call_output(SECOND_CHILD_ID, "spawn-child", "2026-07-23T00:00:02Z", '{"task_name":"/root/child"}'),
                ],
            )
            _write_jsonl(
                child_file,
                [_meta(CHILD_ID, parent=ROOT_ID, depth=1, role=None, path="/root/child")],
            )

            report = task_tree.analyze_session_tree([root_file, other_parent_file, child_file])

        child = next(
            item for item in report["roots"][0]["children"] if item["session_id"] == CHILD_ID
        )
        self.assertEqual(child["role"], "unknown")
        self.assertEqual(child["role_source"], "unknown")

    def test_current_runtime_turn_context_and_unscoped_wait_result_consumption(self) -> None:
        task_tree = _load_task_tree()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            child_file = _session_file(root, CHILD_ID)
            _write_jsonl(
                root_file,
                [
                    _meta(ROOT_ID, path="/root"),
                    _call(
                        ROOT_ID,
                        "spawn_agent",
                        "spawn-child",
                        "2026-07-23T00:00:01Z",
                        {"task_name": "child", "agent_type": "researcher"},
                    ),
                    _call_output(ROOT_ID, "spawn-child", "2026-07-23T00:00:02Z", '{"task_name":"/root/child"}'),
                    _call(ROOT_ID, "wait_agent", "wait-sole", "2026-07-23T00:00:05Z", {"timeout_ms": 1000}),
                    _call_output(ROOT_ID, "wait-sole", "2026-07-23T00:00:06Z", '{"timed_out":false}'),
                ],
            )
            _write_jsonl(
                child_file,
                [
                    _meta(CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/child"),
                    _turn_context(CHILD_ID, "2026-07-23T00:00:03Z", model="gpt-5.5", effort="high"),
                    _complete(
                        CHILD_ID,
                        "2026-07-23T00:00:03Z",
                        "2026-07-23T00:00:04Z",
                        duration_ms=1000,
                        first_token_ms=250,
                    ),
                ],
            )

            report = task_tree.analyze_session_tree([root_file, child_file])

        child = report["roots"][0]["children"][0]
        self.assertEqual(child["role_source"], "requested_template")
        self.assertEqual(child["role_contract_id"], "subagent-role-v4.2/researcher")
        self.assertEqual(child["configured_model"], "unknown")
        self.assertEqual(child["configured_reasoning_effort"], "unknown")
        self.assertEqual(child["effective_model"], "gpt-5.5")
        self.assertEqual(child["effective_reasoning_effort"], "high")
        self.assertTrue(child["result_consumed"])

    def test_unscoped_successful_wait_is_not_attributed_when_ambiguous_or_timed_out(self) -> None:
        task_tree = _load_task_tree()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            child_file = _session_file(root, CHILD_ID)
            second_child_file = _session_file(root, SECOND_CHILD_ID)
            _write_jsonl(
                root_file,
                [
                    _meta(ROOT_ID, path="/root"),
                    _call(ROOT_ID, "spawn_agent", "spawn-child", "2026-07-23T00:00:01Z", {"task_name": "child"}),
                    _call_output(ROOT_ID, "spawn-child", "2026-07-23T00:00:02Z", '{"task_name":"/root/child"}'),
                    _call(ROOT_ID, "spawn_agent", "spawn-second", "2026-07-23T00:00:03Z", {"task_name": "second"}),
                    _call_output(ROOT_ID, "spawn-second", "2026-07-23T00:00:04Z", '{"task_name":"/root/second"}'),
                    _call(ROOT_ID, "wait_agent", "wait-ambiguous", "2026-07-23T00:00:05Z", {"timeout_ms": 1000}),
                    _call_output(ROOT_ID, "wait-ambiguous", "2026-07-23T00:00:06Z", '{"timed_out":false}'),
                ],
            )
            _write_jsonl(
                child_file,
                [
                    _meta(CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/child"),
                    _complete(
                        CHILD_ID,
                        "2026-07-23T00:00:03Z",
                        "2026-07-23T00:00:04Z",
                        duration_ms=1000,
                        first_token_ms=250,
                    ),
                ],
            )
            _write_jsonl(
                second_child_file,
                [
                    _meta(SECOND_CHILD_ID, parent=ROOT_ID, depth=1, role="reviewer", path="/root/second"),
                    _complete(
                        SECOND_CHILD_ID,
                        "2026-07-23T00:00:04Z",
                        "2026-07-23T00:00:05Z",
                        duration_ms=1000,
                        first_token_ms=250,
                    ),
                ],
            )

            ambiguous = task_tree.analyze_session_tree([root_file, child_file, second_child_file])

            timeout_root_file = root / "timeout-root.jsonl"
            timeout_child_file = root / "timeout-child.jsonl"
            _write_jsonl(
                timeout_root_file,
                [
                    _meta(ROOT_ID, path="/root"),
                    _call(ROOT_ID, "spawn_agent", "spawn-child", "2026-07-23T00:00:01Z", {"task_name": "child"}),
                    _call_output(ROOT_ID, "spawn-child", "2026-07-23T00:00:02Z", '{"task_name":"/root/child"}'),
                    _call(ROOT_ID, "wait_agent", "wait-timeout", "2026-07-23T00:00:05Z", {"timeout_ms": 1000}),
                    _call_output(ROOT_ID, "wait-timeout", "2026-07-23T00:00:06Z", '{"timed_out":true}'),
                ],
            )
            _write_jsonl(
                timeout_child_file,
                [
                    _meta(CHILD_ID, parent=ROOT_ID, depth=1, role="researcher", path="/root/child"),
                    _complete(
                        CHILD_ID,
                        "2026-07-23T00:00:03Z",
                        "2026-07-23T00:00:04Z",
                        duration_ms=1000,
                        first_token_ms=250,
                    ),
                ],
            )

            timed_out = task_tree.analyze_session_tree([timeout_root_file, timeout_child_file])

        ambiguous_children = ambiguous["roots"][0]["children"]
        self.assertEqual(len(ambiguous_children), 2)
        self.assertFalse(any(child["result_consumed"] for child in ambiguous_children))
        self.assertFalse(timed_out["roots"][0]["children"][0]["result_consumed"])

    def test_codex_0144_response_items_embedded_meta_and_missing_role(self) -> None:
        task_tree = _load_task_tree()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_file = _session_file(root, ROOT_ID)
            child_file = _session_file(root, CHILD_ID)
            _write_jsonl(
                root_file,
                [
                    _meta(ROOT_ID, path="/root"),
                    _call(ROOT_ID, "spawn_agent", "spawn-child", "2026-07-23T00:00:01Z", {"task_name": "researcher"}),
                    _call_output(ROOT_ID, "spawn-child", "2026-07-23T00:00:02Z", '{"task_name":"/root/researcher"}'),
                    {
                        "type": "response_item",
                        "timestamp": "2026-07-23T00:00:03Z",
                        "payload": {
                            "type": "message",
                            "id": "msg_020f8564003c38fb016a61f26e0aac8199970072352cd8d713",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "CANARY_ROOT_OK"}],
                        },
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-07-23T00:00:04Z",
                        "payload": {
                            "type": "function_call",
                            "id": "fc_020f8564003c38fb016a61f27b416c819b96f2d5213f060710",
                            "name": "wait_agent",
                            "call_id": "wait-child",
                            "arguments": "{}",
                        },
                    },
                ],
            )
            _write_jsonl(
                child_file,
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-07-23T00:00:01Z",
                        "payload": {
                            "id": CHILD_ID,
                            "parent_thread_id": ROOT_ID,
                            "source": {
                                "subagent": {
                                    "thread_spawn": {
                                        "parent_thread_id": ROOT_ID,
                                        "depth": 1,
                                        "agent_path": "/root/researcher",
                                        "agent_role": None,
                                    }
                                }
                            },
                            "agent_path": "/root/researcher",
                        },
                    },
                    _meta(ROOT_ID, path="/root"),
                    {
                        "type": "response_item",
                        "timestamp": "2026-07-23T00:00:05Z",
                        "payload": {
                            "type": "message",
                            "id": "msg_031527535989546c016a61f28c3734819b946cabcfd0dd6e8c",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "CANARY_CHILD_TERMINAL"}],
                        },
                    },
                ],
            )

            report = task_tree.analyze_session_tree([root_file, child_file])

        self.assertEqual(report["aggregate"]["roots"], 1)
        self.assertEqual(report["aggregate"]["sessions"], 2)
        child = report["roots"][0]["children"][0]
        self.assertEqual(child["agent_path"], "/root/researcher")
        self.assertEqual(child["role"], "researcher")

    def test_fork_history_and_exact_cross_file_duplicates_are_not_recounted(self) -> None:
        task_tree = _load_task_tree()
        fork_id = "00000000-0000-4000-8000-000000000099"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = root / "rollout-2026-07-23T000000-original.jsonl"
            fork = root / "rollout-2026-07-23T010000-original_fork.jsonl"
            shared = _call(ROOT_ID, "spawn_agent", "shared-call", "2026-07-23T01:00:01Z", {"task_name": "shared"})
            _write_jsonl(
                original,
                [
                    _meta(ROOT_ID, path="/root"),
                    shared,
                ],
            )
            _write_jsonl(
                fork,
                [
                    {
                        "type": "session_meta",
                        "payload": {"id": fork_id, "agent_path": "/root"},
                        "timestamp": "2026-07-23T01:00:00Z",
                    },
                    _call(fork_id, "spawn_agent", "copied-old", "2026-07-23T00:30:00Z", {"task_name": "old"}),
                    {**shared, "session_id": fork_id},
                    _call(fork_id, "wait_agent", "new-call", "2026-07-23T01:00:02Z"),
                ],
            )

            report = task_tree.analyze_session_tree([fork, original])

        self.assertEqual(report["record_normalization"]["fork_history_records_skipped"], 1)
        self.assertEqual(report["record_normalization"]["duplicate_records_skipped"], 1)
        self.assertEqual(report["aggregate"]["counts"]["spawn_agent"], 1)
        self.assertEqual(report["aggregate"]["counts"]["wait_agent"], 1)


if __name__ == "__main__":
    unittest.main()
