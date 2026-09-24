from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "hooks" / "critical_action_gate.py"
POLICY = ROOT / "tests" / "fixtures" / "critical_gate" / "generic_server_policy.json"


def _payload(command: str, *, cwd: Path, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "cwd": str(cwd),
        "tool_name": "Bash",
        "tool_input": {"cmd": command},
        "policy_path": str(POLICY),
    }
    payload.update(extra)
    return payload


def _run_gate_raw(payload: dict[str, object], *, env: dict[str, str] | None = None) -> dict[str, object] | None:
    if not GATE.is_file():
        raise AssertionError(f"missing critical action gate: {GATE}")
    proc = subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
    )
    output = proc.stdout.strip()
    if not output:
        if proc.returncode != 0:
            raise AssertionError(f"critical action gate exited non-zero without JSON: {proc.returncode}; stderr={proc.stderr}")
        return None
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"critical action gate emitted invalid JSON: {output!r}") from exc
    if proc.returncode != 0:
        raise AssertionError(f"critical action gate exited non-zero: {proc.returncode}; payload={parsed}; stderr={proc.stderr}")
    return parsed


def _run_gate(payload: dict[str, object], *, env: dict[str, str] | None = None) -> dict[str, object]:
    parsed = _run_gate_raw(payload, env=env)
    if parsed is None:
        return {"decision": "allow", "silent": True}
    hook_output = parsed.get("hookSpecificOutput")
    if isinstance(hook_output, dict) and hook_output.get("permissionDecision") == "deny":
        reason = str(hook_output.get("permissionDecisionReason", ""))
        if not reason:
            raise AssertionError(f"deny output missing permissionDecisionReason: {parsed}")
        return {"decision": "block", "reason": reason, "raw": parsed}
    if parsed.get("decision") == "block":
        raise AssertionError(f"legacy block output ignored by codex-cli 0.144.1: {parsed}")
    return parsed


def _write_transcript(root: Path, user_text: str, assistant_text: str | None = None) -> Path:
    records: list[dict[str, object]] = [{"payload": {"type": "message", "role": "user", "content": user_text}}]
    if assistant_text:
        records.append({"payload": {"type": "message", "role": "assistant", "content": assistant_text}})
    return _write_transcript_records(root, records)


def _write_transcript_records(root: Path, records: list[dict[str, object]]) -> Path:
    path = root / ".codex" / "sessions" / "current.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    return path


def _message(role: str, content: str) -> dict[str, object]:
    return {"payload": {"type": "message", "role": role, "content": content}}


def _tool_call(name: str, arguments: object) -> dict[str, object]:
    return {"payload": {"type": "function_call", "name": name, "arguments": arguments}}


def _write_aiserver_policy(root: Path) -> Path:
    path = root / "aiserver-policy.json"
    path.write_text(
        json.dumps(
            {
                "version": "critical-gate-aiserver-test-v1",
                "protected_refs": ["pre", "test", "master", "main"],
                "server_policy": {
                    "environment_aliases": {
                        "prod": ["prod", "production", "正式", "生产", "线上"],
                        "pre": ["pre", "staging", "预发", "预发布"],
                        "test": ["test", "testing", "测试"],
                    },
                    "action_aliases": {
                        "deploy": ["deploy", "部署", "发布", "上线"],
                        "restart": ["restart", "重启", "重启服务"],
                        "git": ["git", "更新代码", "拉取代码", "切换分支"],
                        "emergency_bypass": ["emergency bypass", "bypass prodctl", "紧急绕过", "应急直连"],
                    },
                    "hosts": {
                        "app-pre-1": {
                            "env": "pre",
                            "aliases": ["pre app host", "预发应用机器"],
                            "allowed_app_dirs": ["/opt/project/aiserver"],
                            "services": ["aiserver"],
                        },
                        "app-test-1": {
                            "env": "test",
                            "aliases": ["test app host", "测试应用机器"],
                            "allowed_app_dirs": ["/opt/project/aiserver"],
                            "services": ["aiserver"],
                        },
                    },
                    "prodctl": {
                        "required_for_envs": ["prod"],
                        "preferred_for_envs": ["pre", "test"],
                        "read_actions": ["status", "version", "plan-deploy", "plan-config"],
                        "write_actions": ["deploy", "restart", "restart-service", "apply-config"],
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_aiserver_pre_policy(root: Path) -> Path:
    path = root / "aiserver-pre-policy.json"
    path.write_text(
        json.dumps(
            {
                "version": "aiserver-pre-routine-test-v1",
                "protected_refs": ["pre", "test", "master", "main"],
                "server_policy": {
                    "environment_aliases": {
                        "pre": ["pre", "staging", "预发", "预发布"],
                        "test": ["test", "testing", "测试"],
                        "prod": ["prod", "production", "正式", "生产", "线上"],
                    },
                    "action_aliases": {
                        "deploy": ["deploy", "部署", "发布", "上线"],
                        "restart": ["restart", "重启", "重启服务"],
                        "git": ["git", "更新代码", "拉取代码", "切换分支"],
                    },
                    "hosts": {
                        "pre": {
                            "env": "pre",
                            "aliases": ["aiserver-pre"],
                            "allowed_app_dirs": ["/opt/project/aiserver"],
                            "services": ["aiserver"],
                        }
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _run(cmd: list[str], cwd: Path) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise AssertionError(f"command failed: {cmd}; stdout={proc.stdout}; stderr={proc.stderr}")
    return proc.stdout.strip()


def _init_git_repo(root: Path, branch: str = "feature/demo") -> None:
    root.mkdir(parents=True, exist_ok=True)
    _run(["git", "init"], root)
    _run(["git", "config", "user.email", "tests@example.invalid"], root)
    _run(["git", "config", "user.name", "Tests"], root)
    (root / "README.md").write_text("initial\n", encoding="utf-8")
    _run(["git", "add", "README.md"], root)
    _run(["git", "commit", "-m", "initial"], root)
    _run(["git", "checkout", "-b", branch], root)


def _write_merge_safety_state(
    repo: Path,
    *,
    target_ref: str = "pre",
    source_ref: str = "feature/demo",
    operation: str = "merge",
    precheck_ok: bool = False,
    validation_ok: bool = False,
    review_ok: bool = False,
    lineage_ok: bool = False,
    postcheck_ok: bool = False,
) -> None:
    path = repo / ".codex-local" / "merge-safety" / "current.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "state_version": 1,
                "kind": "merge_safety_intent",
                "operation": operation,
                "target_ref": target_ref,
                "source_ref": source_ref,
                "precheck": {"ok": precheck_ok},
                "evidence": {
                    "validation_ok": validation_ok,
                    "review_ok": review_ok,
                    "lineage_ok": lineage_ok,
                    "postcheck_ok": postcheck_ok,
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _write_merge_safety_state_raw(repo: Path, state: dict[str, object]) -> None:
    path = repo / ".codex-local" / "merge-safety" / "current.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _assert_matching_intent_reason(testcase: unittest.TestCase, result: dict[str, object]) -> None:
    reason = str(result.get("reason", "")).lower()
    testcase.assertIn("matching", reason)
    testcase.assertIn("intent", reason)


class CriticalActionGateContractTests(unittest.TestCase):
    def test_restart_dry_run_is_not_a_production_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for action in ('restart', 'restart-service'):
                result = _run_gate(_payload(
                    f'git status --short --branch; ./prodctl {action} --host app-prod-1 --service example-app --dry-run',
                    cwd=root))
                self.assertEqual(result['decision'], 'allow', result)

    def test_dry_run_does_not_authorize_neighboring_real_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for command in (
                './prodctl restart --host app-prod-1 --service example-app --dry-run; ./prodctl restart --host app-prod-1 --service example-app',
                './prodctl restart --host app-prod-1 --service example-app; echo --dry-run',
                './prodctl restart --host app-prod-1 --service example-app --approval-token "--dry-run"',
            ):
                result = _run_gate(_payload(command, cwd=root))
                self.assertEqual(result['decision'], 'block', result)

    def test_readonly_ssh_standard_descriptor_duplication_is_not_file_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for redirection in ("2>&1", "1>&2"):
                result = _run_gate(_payload("ssh app-pre-1 'systemctl status example-app " + redirection + "'", cwd=root))
                self.assertEqual(result.get("decision"), "allow", result)
            result = _run_gate(_payload("ssh app-pre-1 'systemctl status example-app > /etc/unauthorized'", cwd=root))
            self.assertEqual(result.get("decision"), "block", result)

    def test_raw_block_output_uses_pretool_permission_deny_protocol_without_legacy_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="pre")

            output = _run_gate_raw(_payload("git merge feature/demo", cwd=root))

            self.assertIsInstance(output, dict)
            assert output is not None
            self.assertNotIn("decision", output)
            hook_output = output.get("hookSpecificOutput")
            self.assertIsInstance(hook_output, dict, output)
            assert isinstance(hook_output, dict)
            self.assertEqual(hook_output.get("hookEventName"), "PreToolUse")
            self.assertEqual(hook_output.get("permissionDecision"), "deny")
            reason = str(hook_output.get("permissionDecisionReason", "")).lower()
            self.assertIn("protected", reason)
            self.assertIn("pre-check", reason)

    def test_ordinary_bash_fast_path_does_not_touch_git_transcript_or_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_git = fake_bin / "git"
            fake_git.write_text("#!/usr/bin/env sh\necho git-must-not-run >&2\nexit 99\n", encoding="utf-8")
            fake_git.chmod(fake_git.stat().st_mode | stat.S_IXUSR)
            transcript = root / "transcript.jsonl"
            transcript.write_text("must not be read\n", encoding="utf-8")
            telemetry = root / "telemetry" / "events.jsonl"
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

            result = _run_gate(
                _payload(
                    "python -V",
                    cwd=root,
                    transcript_path=str(transcript),
                    telemetry_path=str(telemetry),
                ),
                env=env,
            )

            self.assertEqual(result.get("decision"), "allow", result)
            self.assertTrue(result.get("silent"), "ordinary fast path must exit 0 without stdout")
            self.assertFalse(telemetry.exists(), "ordinary fast path must not write telemetry")

    def test_non_bash_tool_is_out_of_scope_and_allowed_without_deep_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = _payload("git push origin feature/demo", cwd=Path(temp_dir))
            payload["tool_name"] = "Read"

            result = _run_gate(payload)

            self.assertEqual(result.get("decision"), "allow")
            self.assertTrue(result.get("silent"), result)

    def test_exec_command_uses_same_protected_action_checks_as_bash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="pre")
            payload = _payload("git merge feature/demo", cwd=root)
            payload["tool_name"] = "exec_command"

            blocked = _run_gate(payload)

            self.assertEqual(blocked.get("decision"), "block")
            self.assertIn("protected", str(blocked.get("reason", "")).lower())

    def test_protected_merge_requires_matching_intent_and_successful_precheck(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="pre")

            blocked = _run_gate(_payload("git merge feature/demo", cwd=root))

            self.assertEqual(blocked.get("decision"), "block")
            self.assertIn("protected", str(blocked.get("reason", "")).lower())
            self.assertIn("pre-check", str(blocked.get("reason", "")).lower())

            _write_merge_safety_state(root, target_ref="pre", source_ref="feature/demo", precheck_ok=True)
            allowed = _run_gate(
                _payload(
                    "git merge feature/demo",
                    cwd=root,
                )
            )

            self.assertEqual(allowed.get("decision"), "allow")
            self.assertTrue(allowed.get("silent"), allowed)

    def test_protected_merge_rebase_and_cherry_pick_reject_missing_or_mismatched_intent_even_with_precheck(self) -> None:
        cases = (
            ("git merge feature/demo", "merge", "pre", "feature/demo"),
            ("git rebase feature/demo", "rebase", "pre", "feature/demo"),
            ("git cherry-pick feature/demo", "cherry-pick", "pre", "feature/demo"),
        )

        for command, operation, target_ref, source_ref in cases:
            with self.subTest(command=command, state="missing_intent_fields"):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "repo"
                    _init_git_repo(root, branch=target_ref)
                    _write_merge_safety_state_raw(
                        root,
                        {
                            "state_version": 1,
                            "kind": "merge_safety_intent",
                            "precheck": {"ok": True},
                            "evidence": {},
                        },
                    )

                    result = _run_gate(_payload(command, cwd=root))

                    self.assertEqual(result.get("decision"), "block")
                    _assert_matching_intent_reason(self, result)

            mismatches = (
                ("operation", {"operation": "merge" if operation != "merge" else "rebase"}),
                ("target_ref", {"target_ref": "main" if target_ref != "main" else "pre"}),
                ("source_ref", {"source_ref": "feature/other"}),
            )
            for mismatch_name, override in mismatches:
                with self.subTest(command=command, state=mismatch_name):
                    with tempfile.TemporaryDirectory() as temp_dir:
                        root = Path(temp_dir) / "repo"
                        _init_git_repo(root, branch=target_ref)
                        state = {
                            "state_version": 1,
                            "kind": "merge_safety_intent",
                            "operation": operation,
                            "target_ref": target_ref,
                            "source_ref": source_ref,
                            "precheck": {"ok": True},
                            "evidence": {},
                        }
                        state.update(override)
                        _write_merge_safety_state_raw(root, state)

                        result = _run_gate(_payload(command, cwd=root))

                        self.assertEqual(result.get("decision"), "block")
                        _assert_matching_intent_reason(self, result)

    def test_abort_for_merge_rebase_or_cherry_pick_is_always_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for command in ("git merge --abort", "git rebase --abort", "git cherry-pick --abort"):
                with self.subTest(command=command):
                    result = _run_gate(_payload(command, cwd=Path(temp_dir)))
                    self.assertEqual(result.get("decision"), "allow", result)
                    self.assertTrue(result.get("silent"), result)

    def test_protected_pull_is_refused_even_with_generic_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript(root, "sync pre branch")
            result = _run_gate(
                _payload(
                    "git pull origin pre",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "block")
            self.assertIn("fetch", str(result.get("reason", "")).lower())

    def test_ordinary_shell_push_is_silent_without_hook_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            direct = _run_gate(_payload("git push origin feature/demo", cwd=root))

            self.assertEqual(direct.get("decision"), "allow")
            self.assertTrue(direct.get("silent"), direct)

            transcript = _write_transcript(root, "push feature/demo to origin")
            allowed = _run_gate(
                _payload(
                    "git push origin feature/demo",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(allowed.get("decision"), "allow")
            self.assertTrue(allowed.get("silent"), allowed)

    def test_protected_push_requires_validation_review_lineage_and_postcheck(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="feature/demo")
            transcript = _write_transcript(root, "push HEAD to pre")
            blocked = _run_gate(
                _payload(
                    "git push origin HEAD:pre",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(blocked.get("decision"), "block")
            reason = str(blocked.get("reason", "")).lower()
            for required in ("validation", "review", "lineage", "post-check"):
                self.assertIn(required, reason)

            _write_merge_safety_state(
                root,
                operation="push",
                target_ref="pre",
                source_ref="HEAD",
                precheck_ok=True,
                validation_ok=True,
                review_ok=True,
                lineage_ok=True,
                postcheck_ok=True,
            )
            allowed = _run_gate(
                _payload(
                    "git push origin HEAD:pre",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(allowed.get("decision"), "allow")
            self.assertTrue(allowed.get("silent"), allowed)

    def test_pre_and_test_protected_push_can_rely_on_complete_merge_safety_evidence_without_push_words(self) -> None:
        cases = (
            ("pre", "上预发验证"),
            ("test", "上测试验证"),
        )

        for target_ref, user_text in cases:
            with self.subTest(target=target_ref):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "repo"
                    _init_git_repo(root, branch="feature/demo")
                    transcript = _write_transcript(root, user_text)
                    _write_merge_safety_state(
                        root,
                        operation="push",
                        target_ref=target_ref,
                        source_ref="HEAD",
                        precheck_ok=True,
                        validation_ok=True,
                        review_ok=True,
                        lineage_ok=True,
                        postcheck_ok=True,
                    )

                    allowed = _run_gate(
                        _payload(
                            f"git push origin HEAD:{target_ref}",
                            cwd=root,
                            transcript_path=str(transcript),
                        )
                    )

                    self.assertEqual(allowed.get("decision"), "allow", allowed)
                    self.assertTrue(allowed.get("silent"), allowed)

    def test_master_protected_push_still_requires_explicit_push_or_merge_authorization_even_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="feature/demo")
            transcript = _write_transcript(root, "同步完成，继续验证")
            _write_merge_safety_state(
                root,
                operation="push",
                target_ref="master",
                source_ref="HEAD",
                precheck_ok=True,
                validation_ok=True,
                review_ok=True,
                lineage_ok=True,
                postcheck_ok=True,
            )

            blocked = _run_gate(
                _payload(
                    "git push origin HEAD:master",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(blocked.get("decision"), "block", blocked)
            self.assertIn("authorization", str(blocked.get("reason", "")).lower())

    def test_protected_push_accepts_plan_approval_with_common_typo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="sync-work")
            transcript = _write_transcript(root, "可以，推倒远端 master")
            _write_merge_safety_state(
                root,
                operation="push",
                target_ref="master",
                source_ref="HEAD",
                precheck_ok=True,
                validation_ok=True,
                review_ok=True,
                lineage_ok=True,
                postcheck_ok=True,
            )

            allowed = _run_gate(
                _payload(
                    "git push origin HEAD:master",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(allowed.get("decision"), "allow")
            self.assertTrue(allowed.get("silent"), allowed)

    def test_protected_push_accepts_explicit_merge_to_master_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="sync-work")
            transcript = _write_transcript(root, "合并到 master")
            _write_merge_safety_state(
                root,
                operation="push",
                target_ref="master",
                source_ref="HEAD",
                precheck_ok=True,
                validation_ok=True,
                review_ok=True,
                lineage_ok=True,
                postcheck_ok=True,
            )

            allowed = _run_gate(
                _payload(
                    "git push origin HEAD:master",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(allowed.get("decision"), "allow", allowed)
            self.assertTrue(allowed.get("silent"), allowed)

    def test_protected_push_reads_session_context_when_transcript_path_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="sync-work")
            context_dir = root / "session-context"
            context_dir.mkdir()
            session_id = "session-test-1"
            (context_dir / f"{session_id}.json").write_text(
                json.dumps(
                    {
                        "session_id": session_id,
                        "user_text": "授权，继续推进。",
                        "assistant_text": (
                            "请将当前分支快进推送到 master。已完成 validation review "
                            "lineage post-check。"
                        ),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            _write_merge_safety_state(
                root,
                operation="push",
                target_ref="master",
                source_ref="HEAD",
                precheck_ok=True,
                validation_ok=True,
                review_ok=True,
                lineage_ok=True,
                postcheck_ok=True,
            )

            allowed = _run_gate(
                _payload(
                    "git push origin HEAD:master",
                    cwd=root,
                    session_id=session_id,
                ),
                env={**os.environ, "CODEX_SESSION_CONTEXT_DIR": str(context_dir)},
            )

            self.assertEqual(allowed.get("decision"), "allow", allowed)
            self.assertTrue(allowed.get("silent"), allowed)

    def test_missing_merge_evidence_still_blocks_session_context_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="sync-work")
            context_dir = root / "session-context"
            context_dir.mkdir()
            session_id = "session-test-2"
            (context_dir / f"{session_id}.json").write_text(
                json.dumps(
                    {
                        "session_id": session_id,
                        "user_text": "允许执行 git push origin HEAD:master，将 d507fdf 推送到 master。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            blocked = _run_gate(
                _payload(
                    "git push origin HEAD:master",
                    cwd=root,
                    session_id=session_id,
                ),
                env={**os.environ, "CODEX_SESSION_CONTEXT_DIR": str(context_dir)},
            )

            self.assertEqual(blocked.get("decision"), "block", blocked)
            for required in ("validation", "review", "lineage", "post-check"):
                self.assertIn(required, str(blocked.get("reason", "")).lower())

    def test_protected_push_rejects_missing_or_mismatched_intent_even_with_complete_evidence(self) -> None:
        complete_evidence = {
            "validation_ok": True,
            "review_ok": True,
            "lineage_ok": True,
            "postcheck_ok": True,
        }
        cases = (
            ("missing_intent_fields", {}),
            ("wrong_operation", {"operation": "merge"}),
            ("wrong_target_ref", {"operation": "push", "target_ref": "main", "source_ref": "HEAD"}),
            ("wrong_source_ref", {"operation": "push", "target_ref": "pre", "source_ref": "feature/other"}),
        )

        for case_name, intent_fields in cases:
            with self.subTest(case=case_name):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "repo"
                    _init_git_repo(root, branch="feature/demo")
                    transcript = _write_transcript(root, "push HEAD to pre")
                    _write_merge_safety_state_raw(
                        root,
                        {
                            "state_version": 1,
                            "kind": "merge_safety_intent",
                            **intent_fields,
                            "precheck": {"ok": True},
                            "evidence": complete_evidence,
                        },
                    )

                    result = _run_gate(
                        _payload(
                            "git push origin HEAD:pre",
                            cwd=root,
                            transcript_path=str(transcript),
                        )
                    )

                    self.assertEqual(result.get("decision"), "block")
                    _assert_matching_intent_reason(self, result)

    def test_force_push_is_blocked_except_exact_force_with_lease_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generic_transcript = _write_transcript(root, "push feature/demo")
            blocked_force = _run_gate(
                _payload(
                    "git push --force origin feature/demo",
                    cwd=root,
                    transcript_path=str(generic_transcript),
                )
            )
            self.assertEqual(blocked_force.get("decision"), "block")
            self.assertIn("--force-with-lease", str(blocked_force.get("reason", "")).lower())

            blocked_lease = _run_gate(
                _payload(
                    "git push --force-with-lease origin feature/demo",
                    cwd=root,
                    transcript_path=str(generic_transcript),
                )
            )
            self.assertEqual(blocked_lease.get("decision"), "block")
            self.assertIn("force-with-lease", str(blocked_lease.get("reason", "")).lower())

            exact_transcript = _write_transcript(root, "push feature/demo with --force-with-lease to origin")
            allowed_lease = _run_gate(
                _payload(
                    "git push --force-with-lease origin feature/demo",
                    cwd=root,
                    transcript_path=str(exact_transcript),
                )
            )
            self.assertEqual(allowed_lease.get("decision"), "allow")
            self.assertTrue(allowed_lease.get("silent"), allowed_lease)

    def test_prod_raw_ssh_write_is_blocked_unless_current_turn_emergency_bypass_matches_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            blocked = _run_gate(_payload("ssh app-prod-1 'cd /srv/example-app && git pull && systemctl restart example-app'", cwd=root))

            self.assertEqual(blocked.get("decision"), "block")
            self.assertIn("prodctl", str(blocked.get("reason", "")).lower())

            transcript = _write_transcript(root, "emergency bypass prodctl for prod app-prod-1 git pull and restart example-app")
            allowed = _run_gate(
                _payload(
                    "ssh app-prod-1 'cd /srv/example-app && git pull && systemctl restart example-app'",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(allowed.get("decision"), "allow")
            self.assertTrue(allowed.get("silent"), allowed)

    def test_prod_emergency_direct_ssh_requires_semantic_bypass_target_action_and_configured_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript(root, "紧急绕过 prodctl 直连正式应用机器，更新代码并重启 example-app")
            bounded = _run_gate(
                _payload(
                    "ssh app-prod-1 'cd /srv/example-app && git pull && systemctl restart example-app'",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )
            self.assertEqual(bounded.get("decision"), "allow")
            self.assertTrue(bounded.get("silent"), bounded)

            wrong_dir = _run_gate(
                _payload(
                    "ssh app-prod-1 'cd /tmp/example-app && git pull && systemctl restart example-app'",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )
            self.assertEqual(wrong_dir.get("decision"), "block")
            self.assertIn("bounds", str(wrong_dir.get("reason", "")).lower())

            wrong_service = _run_gate(
                _payload(
                    "ssh app-prod-1 'cd /srv/example-app && git pull && systemctl restart other-app'",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )
            self.assertEqual(wrong_service.get("decision"), "block")
            self.assertIn("bounds", str(wrong_service.get("reason", "")).lower())

    def test_configured_prod_raw_file_writes_require_current_turn_emergency_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            commands = (
                "ssh app-prod-1 'cd /srv/example-app && touch runtime.flag'",
                "ssh app-prod-1 'cd /srv/example-app && rm -f runtime.flag'",
                "ssh app-prod-1 'cd /srv/example-app && sed -i.bak s/old/new/ runtime.conf'",
                "ssh app-prod-1 'cd /srv/example-app && printf enabled | tee runtime.conf >/dev/null'",
                "ssh app-prod-1 'cd /srv/example-app && printf enabled > runtime.conf'",
            )

            for command in commands:
                with self.subTest(command=command):
                    result = _run_gate(_payload(command, cwd=root))

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn("prodctl", str(result.get("reason", "")).lower())

    def test_configured_prod_filesystem_mutations_require_current_turn_emergency_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            commands = (
                "ssh app-prod-1 'cd /srv/example-app && mkdir -p releases'",
                "ssh app-prod-1 'cd /srv/example-app && cp runtime.conf runtime.conf.bak'",
                "ssh app-prod-1 'cd /srv/example-app && mv runtime.conf.tmp runtime.conf'",
                "ssh app-prod-1 'cd /srv/example-app && chmod 0644 runtime.conf'",
                "ssh app-prod-1 'cd /srv/example-app && chown app:app runtime.conf'",
                "ssh app-prod-1 'cd /srv/example-app && ln -s releases/current current'",
                "ssh app-prod-1 'cd /srv/example-app && install -m 0644 source.conf runtime.conf'",
            )

            for command in commands:
                with self.subTest(command=command):
                    result = _run_gate(_payload(command, cwd=root))

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn("prodctl", str(result.get("reason", "")).lower())

    def test_configured_prod_filesystem_mutation_binds_emergency_authorization_to_exact_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            command = "ssh app-prod-1 'cd /srv/example-app && mkdir -p releases'"

            exact = _write_transcript(
                root,
                "emergency bypass prodctl: direct SSH to prod host app-prod-1 and mkdir -p /srv/example-app/releases",
            )
            allowed = _run_gate(_payload(command, cwd=root, transcript_path=str(exact)))
            self.assertEqual(allowed.get("decision"), "allow")
            self.assertTrue(allowed.get("silent"), allowed)

            wrong_action = _write_transcript(
                root,
                "emergency bypass prodctl: direct SSH to prod host app-prod-1 and copy /srv/example-app/releases",
            )
            blocked = _run_gate(_payload(command, cwd=root, transcript_path=str(wrong_action)))
            self.assertEqual(blocked.get("decision"), "block")
            self.assertIn("prodctl", str(blocked.get("reason", "")).lower())

    def test_pre_and_test_filesystem_mutations_require_matching_current_turn_semantics(self) -> None:
        cases = (
            (
                "pre",
                "ssh app-pre-1 'cd /srv/example-app && cp runtime.conf runtime.conf.bak'",
                "direct SSH to pre host app-pre-1 and copy /srv/example-app/runtime.conf to runtime.conf.bak",
            ),
            (
                "test",
                "ssh app-test-1 'cd /srv/example-app && chmod 0644 runtime.conf'",
                "direct SSH to test host app-test-1 and chmod /srv/example-app/runtime.conf to 0644",
            ),
        )

        for env_name, command, user_text in cases:
            with self.subTest(env=env_name, state="missing_authorization"):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    blocked = _run_gate(_payload(command, cwd=root))

                    self.assertEqual(blocked.get("decision"), "block")
                    self.assertIn("current-turn", str(blocked.get("reason", "")).lower())

            with self.subTest(env=env_name, state="matching_authorization"):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    transcript = _write_transcript(root, user_text)
                    allowed = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(allowed.get("decision"), "allow")
                    self.assertTrue(allowed.get("silent"), allowed)

    def test_configured_read_only_ssh_diagnostics_may_redirect_output_only_to_dev_null(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            commands = (
                "ssh app-prod-1 'journalctl -n 20 2>/dev/null'",
                "ssh app-pre-1 'cat /srv/example-app/runtime.conf >/dev/null'",
            )

            for command in commands:
                with self.subTest(command=command):
                    result = _run_gate(_payload(command, cwd=root))

                    self.assertEqual(result.get("decision"), "allow")
                    self.assertTrue(result.get("silent"), result)

    def test_configured_prod_file_write_binds_emergency_authorization_to_host_action_and_app_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            command = "ssh app-prod-1 'cd /srv/example-app && touch runtime.flag'"

            exact = _write_transcript(
                root,
                "emergency bypass prodctl: direct SSH to prod host app-prod-1 and touch /srv/example-app/runtime.flag",
            )
            allowed = _run_gate(_payload(command, cwd=root, transcript_path=str(exact)))
            self.assertEqual(allowed.get("decision"), "allow")
            self.assertTrue(allowed.get("silent"), allowed)

            mismatches = (
                "emergency bypass prodctl: direct SSH to prod host app-prod-2 and touch /srv/example-app/runtime.flag",
                "emergency bypass prodctl: direct SSH to prod host app-prod-1 and remove /srv/example-app/runtime.flag",
            )
            for user_text in mismatches:
                with self.subTest(user_text=user_text):
                    transcript = _write_transcript(root, user_text)
                    result = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn("prodctl", str(result.get("reason", "")).lower())

            outside = _run_gate(
                _payload(
                    "ssh app-prod-1 'cd /tmp && touch runtime.flag'",
                    cwd=root,
                    transcript_path=str(exact),
                )
            )
            self.assertEqual(outside.get("decision"), "block")
            self.assertIn("bounds", str(outside.get("reason", "")).lower())

    def test_prod_emergency_absolute_writes_outside_app_dir_remain_blocked_after_allowed_cd(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            allowed_transcript = _write_transcript(
                root,
                "emergency bypass prodctl: direct SSH to prod host app-prod-1 and touch /srv/example-app/runtime.flag",
            )
            allowed = _run_gate(
                _payload(
                    "ssh app-prod-1 'cd /srv/example-app && touch /srv/example-app/runtime.flag'",
                    cwd=root,
                    transcript_path=str(allowed_transcript),
                )
            )
            self.assertEqual(allowed.get("decision"), "allow")
            self.assertTrue(allowed.get("silent"), allowed)

            cases = (
                (
                    "tmp",
                    "ssh app-prod-1 'cd /srv/example-app && touch /tmp/runtime.flag'",
                    "emergency bypass prodctl: direct SSH to prod host app-prod-1 and touch /tmp/runtime.flag",
                ),
                (
                    "etc",
                    "ssh app-prod-1 'cd /srv/example-app && touch /etc/runtime.flag'",
                    "emergency bypass prodctl: direct SSH to prod host app-prod-1 and touch /etc/runtime.flag",
                ),
            )
            for destination, command, user_text in cases:
                with self.subTest(destination=destination):
                    transcript = _write_transcript(root, user_text)
                    blocked = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(blocked.get("decision"), "block")
                    self.assertIn("bounds", str(blocked.get("reason", "")).lower())

            dev_null = _run_gate(_payload("ssh app-prod-1 'cat /srv/example-app/runtime.conf >/dev/null'", cwd=root))
            self.assertEqual(dev_null.get("decision"), "allow")
            self.assertTrue(dev_null.get("silent"), dev_null)

    def test_pre_and_test_bounded_file_writes_require_matching_current_turn_direct_ssh_semantics(self) -> None:
        cases = (
            (
                "pre",
                "ssh app-pre-1 'cd /srv/example-app && touch runtime.flag'",
                "direct SSH to pre host app-pre-1 and touch /srv/example-app/runtime.flag",
                "direct SSH to test host app-pre-1 and touch /srv/example-app/runtime.flag",
            ),
            (
                "test",
                "ssh app-test-1 'cd /srv/example-app && sed -i.bak s/old/new/ runtime.conf'",
                "direct SSH to test host app-test-1 and edit /srv/example-app/runtime.conf with sed -i",
                "direct SSH to test host app-test-1 and touch /srv/example-app/runtime.conf",
            ),
        )

        for env_name, command, exact_text, mismatch_text in cases:
            with self.subTest(env=env_name, state="missing_authorization"):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    blocked = _run_gate(_payload(command, cwd=root))

                    self.assertEqual(blocked.get("decision"), "block")
                    self.assertIn("current-turn", str(blocked.get("reason", "")).lower())

            with self.subTest(env=env_name, state="matching_authorization"):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    transcript = _write_transcript(root, exact_text)
                    allowed = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(allowed.get("decision"), "allow")
                    self.assertTrue(allowed.get("silent"), allowed)

            with self.subTest(env=env_name, state="mismatched_authorization"):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    transcript = _write_transcript(root, mismatch_text)
                    blocked = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(blocked.get("decision"), "block")
                    self.assertIn("current-turn", str(blocked.get("reason", "")).lower())

    def test_pre_and_test_absolute_writes_outside_app_dir_remain_blocked_after_allowed_cd(self) -> None:
        cases = (
            (
                "pre",
                "tmp",
                "ssh app-pre-1 'cd /srv/example-app && touch /tmp/runtime.flag'",
                "direct SSH to pre host app-pre-1 and touch /tmp/runtime.flag",
            ),
            (
                "test",
                "etc",
                "ssh app-test-1 'cd /srv/example-app && touch /etc/runtime.flag'",
                "direct SSH to test host app-test-1 and touch /etc/runtime.flag",
            ),
        )

        for env_name, destination, command, user_text in cases:
            with self.subTest(env=env_name, destination=destination, state="matching_authorization_outside_bounds"):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    transcript = _write_transcript(root, user_text)
                    blocked = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(blocked.get("decision"), "block")
                    self.assertIn("bounds", str(blocked.get("reason", "")).lower())

            with self.subTest(env=env_name, state="matching_authorization_inside_bounds"):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    transcript = _write_transcript(
                        root,
                        f"direct SSH to {env_name} host app-{env_name}-1 and touch /srv/example-app/runtime.flag",
                    )
                    allowed = _run_gate(
                        _payload(
                            f"ssh app-{env_name}-1 'cd /srv/example-app && touch /srv/example-app/runtime.flag'",
                            cwd=root,
                            transcript_path=str(transcript),
                        )
                    )

                    self.assertEqual(allowed.get("decision"), "allow")
                    self.assertTrue(allowed.get("silent"), allowed)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dev_null = _run_gate(_payload("ssh app-pre-1 'cat /srv/example-app/runtime.conf >/dev/null'", cwd=root))

            self.assertEqual(dev_null.get("decision"), "allow")
            self.assertTrue(dev_null.get("silent"), dev_null)

    def test_bounded_direct_ssh_rejects_traversal_later_cd_outside_moves_installs_redirects_and_dynamic_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            allowed_copy_transcript = _write_transcript(
                root,
                "emergency bypass prodctl: direct SSH to prod host app-prod-1 and copy /tmp/runtime.conf to /srv/example-app/runtime.conf",
            )
            allowed_copy = _run_gate(
                _payload(
                    "ssh app-prod-1 'cd /srv/example-app && cp /tmp/runtime.conf runtime.conf'",
                    cwd=root,
                    transcript_path=str(allowed_copy_transcript),
                )
            )
            self.assertEqual(allowed_copy.get("decision"), "allow")
            self.assertTrue(allowed_copy.get("silent"), allowed_copy)

            prod_cases = (
                (
                    "parent_relative_cp_destination",
                    "ssh app-prod-1 'cd /srv/example-app && cp runtime.conf ../runtime.conf'",
                    "emergency bypass prodctl: direct SSH to prod host app-prod-1 and copy /srv/example-app/runtime.conf to /srv/runtime.conf",
                ),
                (
                    "later_parent_cd_touch",
                    "ssh app-prod-1 'cd /srv/example-app && cd .. && touch runtime.flag'",
                    "emergency bypass prodctl: direct SSH to prod host app-prod-1 and touch /srv/runtime.flag",
                ),
                (
                    "outside_mv_source",
                    "ssh app-prod-1 'cd /srv/example-app && mv /tmp/runtime.conf runtime.conf'",
                    "emergency bypass prodctl: direct SSH to prod host app-prod-1 and move /tmp/runtime.conf to /srv/example-app/runtime.conf",
                ),
                (
                    "outside_install_destination",
                    "ssh app-prod-1 'cd /srv/example-app && install -m 0644 runtime.conf /tmp/runtime.conf'",
                    "emergency bypass prodctl: direct SSH to prod host app-prod-1 and install /srv/example-app/runtime.conf to /tmp/runtime.conf",
                ),
                (
                    "outside_redirect_destination",
                    "ssh app-prod-1 'cd /srv/example-app && printf enabled >/tmp/runtime.conf'",
                    "emergency bypass prodctl: direct SSH to prod host app-prod-1 and write /tmp/runtime.conf",
                ),
                (
                    "dynamic_target",
                    "ssh app-prod-1 'cd /srv/example-app && touch $TARGET'",
                    "emergency bypass prodctl: direct SSH to prod host app-prod-1 and touch $TARGET",
                ),
            )

            for case_name, command, user_text in prod_cases:
                with self.subTest(env="prod", case=case_name):
                    transcript = _write_transcript(root, user_text)
                    blocked = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(blocked.get("decision"), "block")
                    self.assertIn("bounds", str(blocked.get("reason", "")).lower())

            dev_null = _run_gate(_payload("ssh app-prod-1 'cat /srv/example-app/runtime.conf >/dev/null'", cwd=root))
            self.assertEqual(dev_null.get("decision"), "allow")
            self.assertTrue(dev_null.get("silent"), dev_null)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript(
                root,
                "direct SSH to pre host app-pre-1 and touch /srv/runtime.flag",
            )
            pre_later_cd = _run_gate(
                _payload(
                    "ssh app-pre-1 'cd /srv/example-app && cd .. && touch runtime.flag'",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(pre_later_cd.get("decision"), "block")
            self.assertIn("bounds", str(pre_later_cd.get("reason", "")).lower())

    def test_bounded_direct_ssh_symlink_sources_must_stay_inside_app_dir(self) -> None:
        cases = (
            (
                "prod",
                "app-prod-1",
                "emergency bypass prodctl: direct SSH to prod host app-prod-1",
            ),
            (
                "pre",
                "app-pre-1",
                "direct SSH to pre host app-pre-1",
            ),
        )

        for env_name, host, auth_prefix in cases:
            with self.subTest(env=env_name, state="allowed_in_app_symlink"):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    transcript = _write_transcript(
                        root,
                        f"{auth_prefix} and ln -s /srv/example-app/releases/current /srv/example-app/current",
                    )
                    allowed = _run_gate(
                        _payload(
                            f"ssh {host} 'cd /srv/example-app && ln -s releases/current current'",
                            cwd=root,
                            transcript_path=str(transcript),
                        )
                    )

                    self.assertEqual(allowed.get("decision"), "allow")
                    self.assertTrue(allowed.get("silent"), allowed)

            outside_sources = (
                (
                    "absolute_etc_source",
                    f"ssh {host} 'cd /srv/example-app && ln -s /etc/passwd current'",
                    f"{auth_prefix} and ln -s /etc/passwd /srv/example-app/current",
                ),
                (
                    "relative_traversal_source",
                    f"ssh {host} 'cd /srv/example-app && ln -s ../../etc/passwd current'",
                    f"{auth_prefix} and ln -s /etc/passwd /srv/example-app/current",
                ),
            )
            for case_name, command, user_text in outside_sources:
                with self.subTest(env=env_name, case=case_name):
                    with tempfile.TemporaryDirectory() as temp_dir:
                        root = Path(temp_dir)
                        transcript = _write_transcript(root, user_text)
                        blocked = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                        self.assertEqual(blocked.get("decision"), "block")
                        self.assertIn("bounds", str(blocked.get("reason", "")).lower())

    def test_pre_and_test_limited_ssh_can_use_semantic_authorization_without_user_supplied_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript(root, "deploy current branch to pre, restart example-app, and verify")
            allowed = _run_gate(
                _payload(
                    "ssh app-pre-1 'cd /srv/example-app && git fetch origin && git checkout feature/demo && systemctl restart example-app'",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(allowed.get("decision"), "allow")
            self.assertTrue(allowed.get("silent"), allowed)
            self.assertNotIn("hash", json.dumps(allowed).lower())

    def test_pre_and_test_limited_ssh_accept_policy_semantic_aliases_without_hash_or_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cases = (
                (
                    "请把当前分支部署到预发应用机器，更新代码后重启 example-app",
                    "ssh app-pre-1 'cd /srv/example-app && git fetch origin && git checkout feature/demo && systemctl restart example-app'",
                ),
                (
                    "请把当前分支部署到测试应用机器，更新代码后重启 example-app",
                    "ssh app-test-1 'cd /srv/example-app && git fetch origin && git checkout feature/demo && systemctl restart example-app'",
                ),
            )

            for user_text, command in cases:
                with self.subTest(user_text=user_text):
                    transcript = _write_transcript(root, user_text)
                    result = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(result.get("decision"), "allow")
                    self.assertTrue(result.get("silent"), result)
                    rendered = json.dumps(result, ensure_ascii=False).lower()
                    self.assertNotIn("hash", rendered)
                    self.assertNotIn("token", rendered)

    def test_configured_pre_and_test_routine_ssh_deploy_build_restart_do_not_require_current_turn_auth(self) -> None:
        cases = (
            "ssh app-pre-1 'cd /srv/example-app && git fetch origin && git checkout feature/demo && pnpm build && systemctl restart example-app'",
            "ssh app-test-1 'cd /srv/example-app && git fetch origin && git checkout feature/demo && npm run build && supervisorctl restart example-app'",
        )

        for command in cases:
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)

                    result = _run_gate(_payload(command, cwd=root))

                    self.assertEqual(result.get("decision"), "allow", result)
                    self.assertTrue(result.get("silent"), result)

    def test_configured_pre_and_test_routine_ssh_respects_current_user_denial(self) -> None:
        cases = (
            (
                "denied_restart",
                "ssh app-pre-1 'cd /srv/example-app && git fetch origin && git checkout feature/demo && systemctl restart example-app'",
                "不要重启预发服务",
            ),
            (
                "denied_ssh_transport",
                "ssh app-test-1 'cd /srv/example-app && git fetch origin && git checkout feature/demo && supervisorctl restart example-app'",
                "do not use ssh for test",
            ),
        )

        for case_name, command, user_text in cases:
            with self.subTest(case=case_name):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    transcript = _write_transcript(root, user_text)

                    result = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(result.get("decision"), "block", result)
                    self.assertIn("current-turn", str(result.get("reason", "")).lower())

    def test_configured_pre_and_test_nonroutine_git_reset_does_not_use_routine_ssh_shortcut(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            result = _run_gate(
                _payload(
                    "ssh app-pre-1 'cd /srv/example-app && git reset --hard origin/master && systemctl restart example-app'",
                    cwd=root,
                )
            )

            self.assertEqual(result.get("decision"), "block", result)
            self.assertIn("may discard work", str(result.get("reason", "")).lower())

    def test_real_pre_ssh_branch_sync_reset_restart_is_rejected_as_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = _write_aiserver_pre_policy(root)
            command = """ssh aiserver-pre 'set -euo pipefail
cd /opt/project/aiserver
echo "pre_start_branch=$(git branch --show-current)"
echo "pre_start_head=$(git rev-parse HEAD)"
test -z "$(git status --porcelain=v1 --untracked-files=no)"
git fetch origin
git switch pre
git reset --hard origin/pre
expected_head=$(git rev-parse origin/pre)
test "$(git branch --show-current)" = pre
test "$(git rev-parse HEAD)" = "$expected_head"
supervisorctl restart aiserver
supervisorctl status aiserver
test "$(git branch --show-current)" = pre
test "$(git rev-parse HEAD)" = "$expected_head"
curl -fsS --max-time 10 http://127.0.0.1:8001/healthz'"""

            result = _run_gate(_payload(command, cwd=root, policy_path=str(policy_path)))

            self.assertEqual(result.get("decision"), "block", result)
            reason = str(result.get("reason", "")).lower()
            self.assertIn("may discard work", reason)
            self.assertNotIn("current-turn", reason)

    def test_pre_ssh_embedded_hard_reset_is_technical_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = _write_aiserver_pre_policy(root)
            for prefix, git_prefix in (("", "git"), ("git fetch origin; ", "git"), ("", "git -C .")):
                command = (
                    "ssh aiserver-pre 'cd /opt/project/aiserver; " + prefix
                    + "if " + git_prefix + " reset --hard origin/pre; then supervisorctl restart aiserver; fi'"
                )
                result = _run_gate(_payload(command, cwd=root, policy_path=str(policy_path)))
                self.assertEqual(result.get("decision"), "block", result)
                self.assertIn("may discard work", str(result.get("reason", "")))

    def test_pre_sync_symbolic_ref_read_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = _write_aiserver_pre_policy(root)
            for probe, decision in (
                ("symbolic-ref --short HEAD", "allow"),
                ("symbolic-ref -q HEAD", "allow"),
                ("symbolic-ref HEAD refs/heads/master", "block"),
                ("symbolic-ref --delete HEAD", "block"),
                ("symbolic-ref -m reason HEAD refs/heads/master", "block"),
                ("ls-remote origin refs/heads/pre", "allow"),
                ("ls-remote --upload-pack=evil origin", "block"),
                ("ls-remote ext::evil", "block"),
                ("-c protocol.ext.allow=always ls-remote ext::evil", "block"),
                ("--exec-path=/tmp/evil ls-remote origin", "block"),
                ("-c core.sshCommand=evil ls-remote origin", "block"),
            ):
                with self.subTest(probe=probe):
                    command = "ssh aiserver-pre 'cd /opt/project/aiserver; git fetch origin pre; git merge --ff-only origin/pre; git " + probe + "; supervisorctl restart aiserver'"
                    result = _run_gate(_payload(command, cwd=root, policy_path=str(policy_path)))
                    self.assertEqual(result.get("decision"), decision, result)

    def test_shared_pre_sync_on_test_host_allows_readonly_ancestry_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = _write_aiserver_pre_policy(root)
            policy = json.loads(policy_path.read_text())
            policy["server_policy"]["hosts"]["pre"]["env"] = "test"
            policy_path.write_text(json.dumps(policy))
            command = "ssh aiserver-pre 'cd /opt/project/aiserver; git fetch origin pre; git merge-base --is-ancestor pre origin/pre; git switch pre; git merge --ff-only origin/pre; supervisorctl restart aiserver'"
            result = _run_gate(_payload(command, cwd=root, policy_path=str(policy_path)))
            self.assertEqual(result.get("decision"), "allow", result)
            result = _run_gate(_payload(command.replace("merge --ff-only origin/pre", "merge --ff-only origin/master"), cwd=root, policy_path=str(policy_path)))
            self.assertEqual(result.get("decision"), "block", result)

    def test_pre_ssh_fast_forward_merge_restart_is_routine_when_policy_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = _write_aiserver_pre_policy(root)
            command = """ssh aiserver-pre 'set -euo pipefail
cd /opt/project/aiserver
git fetch origin
git switch pre
git merge --ff-only origin/pre
supervisorctl restart aiserver
supervisorctl status aiserver'"""

            result = _run_gate(_payload(command, cwd=root, policy_path=str(policy_path)))

            self.assertEqual(result.get("decision"), "allow", result)
            self.assertTrue(result.get("silent"), result)

    def test_pre_ssh_fast_forward_merge_allows_standard_readonly_git_probes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = _write_aiserver_pre_policy(root)
            command = """ssh aiserver-pre 'set -euo pipefail
cd /opt/project/aiserver
git status --short
git rev-parse --verify HEAD
git fetch origin
git switch pre
git merge --ff-only origin/pre
supervisorctl restart aiserver'"""

            result = _run_gate(_payload(command, cwd=root, policy_path=str(policy_path)))

            self.assertEqual(result.get("decision"), "allow", result)
            self.assertTrue(result.get("silent"), result)

    def test_pre_ssh_reset_hard_wrong_ref_is_not_routine(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = _write_aiserver_pre_policy(root)
            command = """ssh aiserver-pre 'set -euo pipefail
cd /opt/project/aiserver
git fetch origin
git switch pre
git reset --hard origin/master
supervisorctl restart aiserver'"""

            result = _run_gate(_payload(command, cwd=root, policy_path=str(policy_path)))

            self.assertEqual(result.get("decision"), "block", result)
            self.assertIn("may discard work", str(result.get("reason", "")).lower())

    def test_pre_ssh_branch_delete_is_not_treated_as_readonly_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = _write_aiserver_pre_policy(root)
            command = """ssh aiserver-pre 'set -euo pipefail
cd /opt/project/aiserver
git fetch origin
git switch pre
git branch -D stale-branch
git merge --ff-only origin/pre
supervisorctl restart aiserver'"""

            result = _run_gate(_payload(command, cwd=root, policy_path=str(policy_path)))

            self.assertEqual(result.get("decision"), "block", result)
            self.assertIn("routine", str(result.get("reason", "")).lower())

    def test_configured_pre_and_test_routine_ssh_still_requires_app_and_service_bounds(self) -> None:
        cases = (
            (
                "wrong_dir",
                "ssh app-pre-1 'cd /tmp/example-app && git pull --ff-only && systemctl restart example-app'",
            ),
            (
                "wrong_service",
                "ssh app-test-1 'cd /srv/example-app && git pull --ff-only && systemctl restart other-app'",
            ),
        )

        for case_name, command in cases:
            with self.subTest(case=case_name):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)

                    result = _run_gate(_payload(command, cwd=root))

                    self.assertEqual(result.get("decision"), "block", result)
                    self.assertIn("bounds", str(result.get("reason", "")).lower())

    def test_configured_pre_routine_ssh_accepts_exact_supervisor_group_service_with_colon(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = json.loads(POLICY.read_text(encoding="utf-8"))
            policy["server_policy"]["hosts"]["app-pre-1"]["allowed_app_dirs"] = ["/opt/project/midoo_pre"]
            policy["server_policy"]["hosts"]["app-pre-1"]["services"] = ["midoo_pre:midoo_pre_api_app"]
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")

            allowed = _run_gate(
                _payload(
                    "ssh app-pre-1 'cd /opt/project/midoo_pre && supervisorctl restart midoo_pre:midoo_pre_api_app'",
                    cwd=root,
                    policy_path=str(policy_path),
                )
            )
            self.assertEqual(allowed.get("decision"), "allow", allowed)
            self.assertTrue(allowed.get("silent"), allowed)

            wrong_service = _run_gate(
                _payload(
                    "ssh app-pre-1 'cd /opt/project/midoo_pre && supervisorctl restart midoo_pre:other_app'",
                    cwd=root,
                    policy_path=str(policy_path),
                )
            )
            self.assertEqual(wrong_service.get("decision"), "block", wrong_service)
            self.assertIn("bounds", str(wrong_service.get("reason", "")).lower())

    def test_prod_raw_ssh_routine_deploy_build_restart_still_requires_prod_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            result = _run_gate(
                _payload(
                    "ssh app-prod-1 'cd /srv/example-app && git pull --ff-only && pnpm build && systemctl restart example-app'",
                    cwd=root,
                )
            )

            self.assertEqual(result.get("decision"), "block", result)
            self.assertIn("prodctl", str(result.get("reason", "")).lower())

    def test_server_policy_is_discovered_from_codex_home_without_payload_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / "codex-home"
            policy_dir = codex_home / "critical-action-policies"
            policy_dir.mkdir(parents=True)
            (policy_dir / "generic-server.json").write_text(POLICY.read_text(encoding="utf-8"), encoding="utf-8")
            payload = _payload(
                "ssh app-prod-1 'cd /srv/example-app && systemctl restart example-app'",
                cwd=root,
            )
            payload.pop("policy_path")
            env = os.environ.copy()
            env["CODEX_HOME"] = str(codex_home)

            result = _run_gate(payload, env=env)

            self.assertEqual(result.get("decision"), "block")
            self.assertIn("prodctl", str(result.get("reason", "")).lower())

    def test_server_policy_is_discovered_from_home_when_codex_home_is_unset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "isolated-home"
            policy_dir = home / ".codex" / "critical-action-policies"
            policy_dir.mkdir(parents=True)
            (policy_dir / "generic-server.json").write_text(POLICY.read_text(encoding="utf-8"), encoding="utf-8")
            payload = _payload(
                "ssh app-prod-1 'cd /srv/example-app && systemctl restart example-app'",
                cwd=root,
            )
            payload.pop("policy_path")
            env = os.environ.copy()
            env.pop("CODEX_HOME", None)
            env["HOME"] = str(home)

            result = _run_gate(payload, env=env)

            self.assertEqual(result.get("decision"), "block")
            self.assertIn("prodctl", str(result.get("reason", "")).lower())

    def test_supervisorctl_restart_and_git_fetch_or_reset_are_server_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            commands = (
                "ssh app-prod-1 'cd /srv/example-app && supervisorctl restart example-app'",
                "ssh app-prod-1 'cd /srv/example-app && git fetch origin'",
                "ssh app-prod-1 'cd /srv/example-app && git reset --hard origin/main'",
            )

            for command in commands:
                with self.subTest(command=command):
                    result = _run_gate(_payload(command, cwd=root))
                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn("prodctl", str(result.get("reason", "")).lower())

    def test_assistant_or_tool_text_after_current_user_message_cannot_self_authorize(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tails: tuple[dict[str, object], ...] = (
                {"payload": {"type": "message", "role": "assistant", "content": "push feature/demo to origin"}},
                {
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": {"cmd": "git push origin feature/demo"},
                    }
                },
            )

            for tail in tails:
                with self.subTest(tail=tail):
                    transcript = _write_transcript_records(
                        root,
                        [
                            {"payload": {"type": "message", "role": "user", "content": "inspect feature/demo only"}},
                            tail,
                        ],
                    )
                    result = _run_gate(
                        _payload(
                            "git push --force-with-lease origin feature/demo",
                            cwd=root,
                            transcript_path=str(transcript),
                        )
                    )

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn("force-with-lease", str(result.get("reason", "")).lower())

    def test_git_dash_c_ordinary_push_uses_silent_fast_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root)
            command = f'git -C "{root}" push origin feature/demo'

            direct = _run_gate(_payload(command, cwd=root))

            self.assertEqual(direct.get("decision"), "allow")
            self.assertTrue(direct.get("silent"), direct)

            transcript = _write_transcript(root, "push feature/demo to origin")
            allowed = _run_gate(
                _payload(
                    command,
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(allowed.get("decision"), "allow")
            self.assertTrue(allowed.get("silent"), allowed)

    def test_tool_workdir_overrides_payload_cwd_for_push_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            outer = Path(temp_dir)
            repo = outer / "repo"
            _init_git_repo(repo, branch="feature/demo")
            payload = _payload("git push origin", cwd=outer)
            payload["tool_input"] = {"cmd": "git push origin", "workdir": str(repo)}

            result = _run_gate(payload)

            self.assertEqual(result.get("decision"), "allow")
            self.assertTrue(result.get("silent"), result)

    def test_runtime_bash_payload_without_workdir_requires_explicit_git_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            outer = Path(temp_dir)
            session_repo = outer / "session-repo"
            checked_repo = outer / "checked repo"
            _init_git_repo(session_repo)
            _init_git_repo(checked_repo)
            (checked_repo / "README.md").write_text("checked revision\n", encoding="utf-8")
            _run(["git", "add", "README.md"], checked_repo)
            _run(["git", "commit", "-m", "checked revision"], checked_repo)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checked_repo, text=True).strip()
            _write_merge_safety_state(checked_repo, target_ref="test", source_ref="HEAD", operation="push",
                                     validation_ok=True, review_ok=True, lineage_ok=True, postcheck_ok=True)
            transcript = _write_transcript_records(outer, [
                _message("assistant", f"将已验证提交 {head} 推送到 origin/test，仅推送，不部署。"),
                _message("user", "授权，继续。"),
            ])
            payload = _payload("", cwd=session_repo, transcript_path=str(transcript))
            # Actual runtime bridge emits Bash/command and drops exec_command.workdir.
            payload["tool_input"] = {"command": "git push origin HEAD:test"}
            self.assertEqual(_run_gate(payload).get("decision"), "block")
            payload["tool_input"] = {"command": f'git -C "{checked_repo}" push origin HEAD:test'}
            self.assertEqual(_run_gate(payload).get("decision"), "allow")
            payload["tool_input"] = {"command": f'git -C "{checked_repo}" push --force origin HEAD:test'}
            self.assertEqual(_run_gate(payload).get("decision"), "block")

    def test_implicit_push_with_config_derived_scope_requires_explicit_refspec(self) -> None:
        cases = (
            (("push.default", "matching"),),
            (("push.default", "upstream"),),
            (("remote.origin.push", "HEAD:refs/heads/pre"),),
            (("remote.origin.push", ":refs/heads/pre"),),
            (
                ("remote.origin.push", "HEAD:refs/heads/feature/demo"),
                ("remote.origin.push", "HEAD:refs/heads/feature/other"),
            ),
        )
        for config_entries in cases:
            with self.subTest(config_entries=config_entries):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "repo"
                    _init_git_repo(root, branch="feature/demo")
                    for key, value in config_entries:
                        _run(["git", "config", "--add", key, value], root)

                    result = _run_gate(_payload("git push origin", cwd=root))

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn("explicit", str(result.get("reason", "")).lower())

    def test_head_push_resolves_current_protected_branch_before_fast_path(self) -> None:
        for source in ("HEAD", "@"):
            with self.subTest(source=source):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "repo"
                    _init_git_repo(root, branch="feature/demo")
                    _run(["git", "branch", "-M", "pre"], root)

                    result = _run_gate(_payload(f"git push origin {source}", cwd=root))

                    self.assertEqual(result.get("decision"), "block")

    def test_repo_push_configuration_cannot_expand_explicit_branch_fast_path(self) -> None:
        for key, value in (("push.followTags", "true"), ("remote.origin.mirror", "true")):
            with self.subTest(key=key):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "repo"
                    _init_git_repo(root, branch="feature/demo")
                    _run(["git", "config", key, value], root)

                    result = _run_gate(_payload("git push origin feature/demo", cwd=root))

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn("explicit", str(result.get("reason", "")).lower())

    def test_gh_pr_merge_is_high_risk_git_candidate_and_requires_merge_safety_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript(root, "merge PR 123")

            result = _run_gate(
                _payload(
                    "gh pr merge 123 --merge --delete-branch",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "block")
            reason = str(result.get("reason", "")).lower()
            self.assertIn("merge-safety", reason)
            self.assertIn("current-turn", reason)

    def test_gh_pr_merge_requires_matching_intent_precheck_and_current_turn_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript(root, "merge PR 123 into pre")
            _write_merge_safety_state_raw(
                root,
                {
                    "state_version": 1,
                    "kind": "merge_safety_intent",
                    "operation": "merge",
                    "target_ref": "main",
                    "source_ref": "123",
                    "precheck": {"ok": True},
                    "evidence": {},
                },
            )

            wrong_target = _run_gate(
                _payload(
                    "gh pr merge 123 --merge --delete-branch",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(wrong_target.get("decision"), "block")
            _assert_matching_intent_reason(self, wrong_target)

            _write_merge_safety_state_raw(
                root,
                {
                    "state_version": 1,
                    "kind": "merge_safety_intent",
                    "operation": "merge",
                    "target_ref": "pre",
                    "source_ref": "123",
                    "precheck": {"ok": False},
                    "evidence": {},
                },
            )
            failed_precheck = _run_gate(
                _payload(
                    "gh pr merge 123 --merge --delete-branch",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(failed_precheck.get("decision"), "block")
            self.assertIn("pre-check", str(failed_precheck.get("reason", "")).lower())

            _write_merge_safety_state_raw(
                root,
                {
                    "state_version": 1,
                    "kind": "merge_safety_intent",
                    "operation": "merge",
                    "target_ref": "pre",
                    "source_ref": "123",
                    "precheck": {"ok": True},
                    "evidence": {},
                },
            )
            no_current_turn_auth = _run_gate(
                _payload(
                    "gh pr merge 123 --merge --delete-branch",
                    cwd=root,
                )
            )

            self.assertEqual(no_current_turn_auth.get("decision"), "block")
            self.assertIn("current-turn", str(no_current_turn_auth.get("reason", "")).lower())

            allowed = _run_gate(
                _payload(
                    "gh pr merge 123 --merge --delete-branch",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(allowed.get("decision"), "allow")
            self.assertTrue(allowed.get("silent"), allowed)

    def test_remote_tracking_protected_refs_are_normalized_for_any_remote_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="feature/demo")
            transcript = _write_transcript(root, "push HEAD to upstream")

            protected = _run_gate(
                _payload(
                    "git push origin HEAD:refs/remotes/upstream/main",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(protected.get("decision"), "block")
            reason = str(protected.get("reason", "")).lower()
            for required in ("validation", "review", "lineage", "post-check"):
                self.assertIn(required, reason)

            unprotected = _run_gate(
                _payload(
                    "git push origin HEAD:refs/remotes/upstream/feature/demo",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(unprotected.get("decision"), "allow")
            self.assertTrue(unprotected.get("silent"), unprotected)

    def test_ordinary_single_ref_feature_pushes_are_silent_fast_path_without_transcript_authorization(self) -> None:
        cases = (
            ("implicit_current_branch", lambda root, head: "git push origin"),
            ("explicit_current_branch", lambda root, head: "git push origin feature/demo"),
            ("commit_sha_to_feature_ref", lambda root, head: f"git push origin {head}:refs/heads/feature/demo"),
        )

        for name, command_factory in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "repo"
                    _init_git_repo(root, branch="feature/demo")
                    head = _run(["git", "rev-parse", "HEAD"], root)

                    result = _run_gate(_payload(command_factory(root, head), cwd=root))

                    self.assertEqual(result.get("decision"), "allow")
                    self.assertTrue(result.get("silent"), result)

    def test_compound_push_does_not_treat_later_command_as_refspec(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="feature/demo")
            for separator in (";", "&&", "\n"):
                command = f'git -C {root} push origin HEAD:refs/heads/feature/demo {separator} git status --short'
                result = _run_gate(_payload(command, cwd=root))
                self.assertEqual(result.get("decision"), "allow", result)
            blocked = _run_gate(_payload(
                'git push origin HEAD:feature/demo; git push origin HEAD:master', cwd=root,
            ))
            self.assertEqual(blocked.get("decision"), "block", blocked)

    def test_approved_predeploy_journey_authorizes_protected_push_without_repeating_sha(self) -> None:
        assistant_plan = (
            "我的预发计划是：\n"
            "1. 从最新 origin/test 创建临时集成分支。\n"
            "2. 运行聚焦测试和 app 编译。\n"
            "3. 确认远端 test 没有再次前进，然后推送到 origin/test。\n"
            "4. 部署 aiserver-test:/opt/project/midoo_pre，只重新构建并重启 "
            "midoo_pre:midoo_pre_api_app。\n"
            "5. 验证服务器 HEAD、进程状态、启动日志和接口可访问。\n"
            "不修改数据库，不执行 migration，不重启 ai/user/rpc/job。"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="integration/prebuilt")
            head = _run(["git", "rev-parse", "HEAD"], root)
            _write_merge_safety_state(
                root,
                target_ref="test",
                source_ref=head,
                operation="push",
                precheck_ok=True,
                validation_ok=True,
                review_ok=True,
                lineage_ok=True,
                postcheck_ok=True,
            )
            transcript = _write_transcript_records(
                root,
                [
                    _message("user", "先把上预发的计划给我看看"),
                    _message("assistant", assistant_plan),
                    _message("user", "ok，按照规划推进吧"),
                ],
            )

            result = _run_gate(
                _payload(
                    f"git push origin {head}:refs/heads/test",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "allow", result)
            self.assertTrue(result.get("silent"), result)

    def test_short_push_approval_binds_the_exact_adjacent_protected_push_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="integration/prebuilt")
            head = _run(["git", "rev-parse", "HEAD"], root)
            _write_merge_safety_state(
                root,
                target_ref="test",
                source_ref=head,
                operation="push",
                precheck_ok=True,
                validation_ok=True,
                review_ok=True,
                lineage_ok=True,
                postcheck_ok=True,
            )
            transcript = _write_transcript_records(
                root,
                [
                    _message("assistant", f"将已验证提交推送到 origin/test；目标提交为 {head}。"),
                    _message("user", "推吧"),
                ],
            )

            result = _run_gate(
                _payload(
                    f"git push origin {head}:refs/heads/test",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "allow")
            self.assertTrue(result.get("silent"), result)

    def test_adjacent_push_plan_cannot_authorize_a_different_protected_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="integration/prebuilt")
            head = _run(["git", "rev-parse", "HEAD"], root)
            _write_merge_safety_state(
                root,
                target_ref="master",
                source_ref=head,
                operation="push",
                precheck_ok=True,
                validation_ok=True,
                review_ok=True,
                lineage_ok=True,
                postcheck_ok=True,
            )
            transcript = _write_transcript_records(
                root,
                [
                    _message("assistant", "将已验证提交推送到 origin/test。"),
                    _message("user", "可以，按照规划推进吧"),
                ],
            )

            result = _run_gate(
                _payload(
                    f"git push origin {head}:refs/heads/master",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "block")

    def test_pretest_push_uses_matching_merge_safety_evidence_even_if_adjacent_plan_names_other_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="integration/prebuilt")
            head = _run(["git", "rev-parse", "HEAD"], root)
            other = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
            _write_merge_safety_state(
                root,
                target_ref="test",
                source_ref=head,
                operation="push",
                precheck_ok=True,
                validation_ok=True,
                review_ok=True,
                lineage_ok=True,
                postcheck_ok=True,
            )
            transcript = _write_transcript_records(
                root,
                [
                    _message("assistant", f"将已验证提交 {other} 推送到 origin/test。"),
                    _message("user", "可以，按照规划推进吧"),
                ],
            )

            result = _run_gate(
                _payload(
                    f"git push origin {head}:refs/heads/test",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "allow", result)
            self.assertTrue(result.get("silent"), result)

    def test_pretest_push_uses_matching_merge_safety_evidence_even_if_adjacent_plan_names_other_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="feature/other")
            _write_merge_safety_state(
                root,
                target_ref="test",
                source_ref="feature/other",
                operation="push",
                precheck_ok=True,
                validation_ok=True,
                review_ok=True,
                lineage_ok=True,
                postcheck_ok=True,
            )
            transcript = _write_transcript_records(
                root,
                [
                    _message("assistant", "Push branch feature/demo to origin/test."),
                    _message("user", "ok, proceed with that plan"),
                ],
            )

            result = _run_gate(
                _payload(
                    "git push origin feature/other:refs/heads/test",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "allow", result)
            self.assertTrue(result.get("silent"), result)

    def test_adjacent_push_plan_with_named_source_authorizes_that_exact_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="feature/demo")
            _write_merge_safety_state(
                root,
                target_ref="test",
                source_ref="feature/demo",
                operation="push",
                precheck_ok=True,
                validation_ok=True,
                review_ok=True,
                lineage_ok=True,
                postcheck_ok=True,
            )
            transcript = _write_transcript_records(
                root,
                [
                    _message("assistant", "Push branch feature/demo to origin/test."),
                    _message("user", "ok, proceed with that plan"),
                ],
            )

            result = _run_gate(
                _payload(
                    "git push origin feature/demo:refs/heads/test",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "allow", result)

    def test_protected_single_ref_pushes_stay_blocked_without_transcript_authorization(self) -> None:
        for protected_ref in ("master", "main", "pre", "test"):
            with self.subTest(protected_ref=protected_ref):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "repo"
                    _init_git_repo(root, branch="feature/demo")

                    result = _run_gate(_payload(f"git push origin HEAD:refs/heads/{protected_ref}", cwd=root))

                    self.assertEqual(result.get("decision"), "block")

    def test_dangerous_push_shapes_stay_blocked_without_transcript_authorization(self) -> None:
        cases = (
            ("git push origin feature/demo --force", "--force"),
            ("git push origin feature/demo --force-with-lease", "force-with-lease"),
            ("git push origin :feature/demo", ""),
            ("git push origin --delete feature/demo", ""),
            ("git push origin --all", "--all"),
            ("git push origin --mirror", "--mirror"),
            ("git push origin feature/demo --tags", "--tags"),
            ("git push origin feature/demo --follow-tags", "--follow-tags"),
            ("git push origin feature/demo --prune", "--prune"),
            ("git push origin feature/demo feature/other", "multiple"),
        )

        for command, reason_marker in cases:
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "repo"
                    _init_git_repo(root, branch="feature/demo")

                    result = _run_gate(_payload(command, cwd=root))

                    self.assertEqual(result.get("decision"), "block")
                    if reason_marker:
                        self.assertIn(reason_marker, str(result.get("reason", "")).lower())

    def test_git_push_current_feature_branch_can_use_semantic_current_branch_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="feature/demo")
            transcript = _write_transcript(root, "推送当前分支到 origin")

            result = _run_gate(
                _payload(
                    "git push origin feature/demo",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "allow")
            self.assertTrue(result.get("silent"), result)

    def test_implicit_feature_push_uses_current_branch_for_silent_fast_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="feature/demo")

            direct = _run_gate(_payload("git push origin", cwd=root))
            self.assertEqual(direct.get("decision"), "allow")
            self.assertTrue(direct.get("silent"), direct)

            for user_text in ("push 当前分支到 origin", "push current branch to origin"):
                with self.subTest(user_text=user_text):
                    transcript = _write_transcript(root, user_text)
                    allowed = _run_gate(_payload("git push origin", cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(allowed.get("decision"), "allow")
                    self.assertTrue(allowed.get("silent"), allowed)

    def test_implicit_push_from_protected_current_branch_requires_evidence_and_matching_intent(self) -> None:
        for branch in ("pre", "main"):
            with self.subTest(branch=branch):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "repo"
                    _init_git_repo(root, branch="feature/setup")
                    _run(["git", "branch", "-M", branch], root)
                    transcript = _write_transcript(root, "push 当前分支到 origin")

                    result = _run_gate(_payload("git push origin", cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(result.get("decision"), "block")
                    reason = str(result.get("reason", "")).lower()
                    for required in ("validation", "review", "lineage", "post-check", "matching intent"):
                        self.assertIn(required, reason)

    def test_protected_push_still_requires_merge_safety_evidence_after_explicit_target_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="feature/demo")
            transcript = _write_transcript(root, "推送当前分支到 origin 的 pre")

            result = _run_gate(
                _payload(
                    "git push origin HEAD:pre",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "block")
            reason = str(result.get("reason", "")).lower()
            for required in ("validation", "review", "lineage", "post-check"):
                self.assertIn(required, reason)

    def test_protected_push_rejects_delete_and_multi_refspec_shapes_even_with_matching_single_ref_evidence(self) -> None:
        cases = (
            ("git push origin :pre", "delete"),
            ("git push origin --delete pre", "delete"),
            ("git push origin HEAD:pre HEAD:main", "multiple"),
            ("git push origin --all", "--all"),
            ("git push origin --mirror", "--mirror"),
        )

        for command, expected_reason in cases:
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "repo"
                    _init_git_repo(root, branch="feature/demo")
                    transcript = _write_transcript(root, "push HEAD to pre and main, delete pre, push all refs, mirror refs")
                    _write_merge_safety_state(
                        root,
                        operation="push",
                        target_ref="pre",
                        source_ref="HEAD",
                        precheck_ok=True,
                        validation_ok=True,
                        review_ok=True,
                        lineage_ok=True,
                        postcheck_ok=True,
                    )

                    result = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn(expected_reason, str(result.get("reason", "")).lower())

    def test_adjacent_chinese_restart_approval_binds_exact_pre_aiserver_plan_for_prodctl_and_ssh(self) -> None:
        assistant_plan = (
            "Plan: restart aiserver on pre host app-pre-1 only, then verify. "
            "Commands: ./prodctl restart-service --host app-pre-1 --service aiserver; "
            "ssh app-pre-1 'cd /opt/project/aiserver && supervisorctl restart aiserver'."
        )
        commands = (
            "./prodctl restart-service --host app-pre-1 --service aiserver",
            "ssh app-pre-1 'cd /opt/project/aiserver && supervisorctl restart aiserver'",
        )

        for command in commands:
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    policy_path = _write_aiserver_policy(root)
                    transcript = _write_transcript_records(
                        root,
                        [
                            _message("user", "先给我一个预发 aiserver 重启计划"),
                            _message("assistant", assistant_plan),
                            _message("user", "允许，重启后验证吧"),
                        ],
                    )

                    result = _run_gate(_payload(command, cwd=root, policy_path=str(policy_path), transcript_path=str(transcript)))

                    self.assertEqual(result.get("decision"), "allow")
                    self.assertTrue(result.get("silent"), result)

    def test_generic_adjacent_approval_binds_one_exact_assistant_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = _write_aiserver_policy(root)
            transcript = _write_transcript_records(
                root,
                [
                    _message("user", "先列计划"),
                    _message("assistant", "Plan: restart aiserver on pre host app-pre-1 using prodctl only."),
                    _message("user", "可以，按上述计划执行"),
                ],
            )

            result = _run_gate(
                _payload(
                    "./prodctl restart-service --host app-pre-1 --service aiserver",
                    cwd=root,
                    policy_path=str(policy_path),
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "allow")
            self.assertTrue(result.get("silent"), result)

    def test_one_approved_predeploy_plan_covers_deploy_and_restart_despite_unrelated_exclusions(self) -> None:
        assistant_plan = (
            "计划：通过 prodctl 部署到预发 app-pre-1 的 example-app，构建完成后重启 example-app，最后验证。"
            "不修改数据库，不执行 migration，不重启 worker。"
        )
        commands = (
            "./prodctl deploy --host app-pre-1 --service example-app --ref abc1234",
            "./prodctl restart-service --host app-pre-1 --service example-app",
        )

        for command in commands:
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    transcript = _write_transcript_records(
                        root,
                        [
                            _message("assistant", assistant_plan),
                            _message("user", "ok，按照规划推进吧"),
                        ],
                    )

                    result = _run_gate(
                        _payload(command, cwd=root, transcript_path=str(transcript))
                    )

                    self.assertEqual(result.get("decision"), "allow", result)
                    self.assertTrue(result.get("silent"), result)

    def test_real_predeploy_authorization_word_covers_deploy_and_restart_service(self) -> None:
        assistant_plan = (
            "预发集成已成功。请授权在 pre 环境使用受控 prodctl 将 aiserver "
            "从 51b3a24cd1fbe0d1f4be4614160d5fc592a8b72c 切换到 "
            "657189510e6f62dcad8976422b6bc6ebde85acc3，并重启一次服务进行验证。"
        )
        commands = (
            "./prodctl deploy --host pre --ref 657189510e6f62dcad8976422b6bc6ebde85acc3",
            "./prodctl restart-service --host pre --service aiserver",
        )

        for command in commands:
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    policy_path = _write_aiserver_policy(root)
                    policy = json.loads(policy_path.read_text(encoding="utf-8"))
                    policy["server_policy"]["hosts"]["pre"] = dict(
                        policy["server_policy"]["hosts"]["app-pre-1"]
                    )
                    policy_path.write_text(json.dumps(policy), encoding="utf-8")
                    transcript = _write_transcript_records(
                        root,
                        [
                            _message("assistant", assistant_plan),
                            _message("user", "授权\n然后测试验证"),
                        ],
                    )

                    result = _run_gate(
                        _payload(
                            command,
                            cwd=root,
                            policy_path=str(policy_path),
                            transcript_path=str(transcript),
                        )
                    )

                    self.assertEqual(result.get("decision"), "allow", result)
                    self.assertTrue(result.get("silent"), result)

    def test_natural_predeploy_intent_covers_deploy_and_restart_without_command_copy(self) -> None:
        assistant_plan = (
            "预发集成已成功。接下来把 aiserver 上到 pre，重启一次并做健康检查。"
            "命令：./prodctl deploy --host pre --service aiserver --ref 6571895；"
            "./prodctl restart-service --host pre --service aiserver。"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = _write_aiserver_policy(root)
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["server_policy"]["hosts"]["pre"] = dict(
                policy["server_policy"]["hosts"]["app-pre-1"]
            )
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            transcript = _write_transcript_records(
                root,
                [_message("assistant", assistant_plan), _message("user", "上预发验证")],
            )
            for command in (
                "./prodctl deploy --host pre --service aiserver --ref 6571895",
                "./prodctl restart-service --host pre --service aiserver",
            ):
                with self.subTest(command=command):
                    result = _run_gate(
                        _payload(
                            command,
                            cwd=root,
                            policy_path=str(policy_path),
                            transcript_path=str(transcript),
                        )
                    )
                    self.assertEqual(result.get("decision"), "allow", result)
                    self.assertTrue(result.get("silent"), result)

    def test_natural_test_intent_covers_deploy_and_restart_without_command_copy(self) -> None:
        assistant_plan = (
            "测试环境验证：把 aiserver 部署到 test，重启后跑健康检查。"
            "命令：./prodctl deploy --host test --service aiserver --ref abc1234；"
            "./prodctl restart-service --host test --service aiserver。"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = _write_aiserver_policy(root)
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["server_policy"]["hosts"]["test"] = dict(
                policy["server_policy"]["hosts"]["app-test-1"]
            )
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            transcript = _write_transcript_records(
                root,
                [_message("assistant", assistant_plan), _message("user", "上 test 验证")],
            )
            for command in (
                "./prodctl deploy --host test --service aiserver --ref abc1234",
                "./prodctl restart-service --host test --service aiserver",
            ):
                with self.subTest(command=command):
                    result = _run_gate(
                        _payload(
                            command,
                            cwd=root,
                            policy_path=str(policy_path),
                            transcript_path=str(transcript),
                        )
                    )
                    self.assertEqual(result.get("decision"), "allow", result)
                    self.assertTrue(result.get("silent"), result)

    def test_one_plan_can_cover_test_and_pre_with_short_followup(self) -> None:
        assistant_plan = (
            "计划：先把当前版本部署到 test 并重启验证，再把同一版本部署到 pre 并重启验证。"
            "使用受控 prodctl，命令为 ./prodctl deploy --host test --service aiserver --ref abc1234；"
            "./prodctl restart-service --host test --service aiserver；"
            "./prodctl deploy --host pre --service aiserver --ref abc1234；"
            "./prodctl restart-service --host pre --service aiserver。完成后做健康检查。"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = _write_aiserver_policy(root)
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["server_policy"]["hosts"]["test"] = dict(
                policy["server_policy"]["hosts"]["app-test-1"]
            )
            policy["server_policy"]["hosts"]["pre"] = dict(
                policy["server_policy"]["hosts"]["app-pre-1"]
            )
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            transcript = _write_transcript_records(
                root,
                [_message("assistant", assistant_plan), _message("user", "按照规划推进吧")],
            )
            commands = (
                "./prodctl deploy --host test --service aiserver --ref abc1234",
                "./prodctl restart-service --host test --service aiserver",
                "./prodctl deploy --host pre --service aiserver --ref abc1234",
                "./prodctl restart-service --host pre --service aiserver",
            )
            for command in commands:
                with self.subTest(command=command):
                    result = _run_gate(
                        _payload(
                            command,
                            cwd=root,
                            policy_path=str(policy_path),
                            transcript_path=str(transcript),
                        )
                    )
                    self.assertEqual(result.get("decision"), "allow", result)
                    self.assertTrue(result.get("silent"), result)

    def test_predeploy_plan_survives_interleaved_runtime_messages(self) -> None:
        assistant_plan = (
            "预发集成已成功。请授权在 pre 环境使用受控 prodctl 将 aiserver "
            "切换到 6571895，并重启一次服务进行验证。"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = _write_aiserver_policy(root)
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["server_policy"]["hosts"]["pre"] = dict(
                policy["server_policy"]["hosts"]["app-pre-1"]
            )
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            transcript = _write_transcript_records(
                root,
                [
                    _message("assistant", assistant_plan),
                    {"payload": {"type": "message", "role": "developer", "content": "runtime note"}},
                    {"payload": {"type": "item_completed", "item": {"type": "tool_call"}}},
                    _message("user", "授权，继续推进"),
                ],
            )

            for command in (
                "./prodctl deploy --host pre --ref 6571895",
                "./prodctl restart-service --host pre --service aiserver",
            ):
                with self.subTest(command=command):
                    result = _run_gate(
                        _payload(
                            command,
                            cwd=root,
                            policy_path=str(policy_path),
                            transcript_path=str(transcript),
                        )
                    )
                    self.assertEqual(result.get("decision"), "allow", result)
                    self.assertTrue(result.get("silent"), result)

    def test_approved_deploy_plan_that_excludes_restart_cannot_authorize_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript_records(
                root,
                [
                    _message(
                        "assistant",
                        "计划：通过 prodctl 部署到预发 app-pre-1 的 example-app，但不要重启 example-app。",
                    ),
                    _message("user", "可以，按照规划推进吧"),
                ],
            )

            result = _run_gate(
                _payload(
                    "./prodctl restart-service --host app-pre-1 --service example-app",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "block")

    def test_adjacent_deploy_plan_with_explicit_ref_cannot_authorize_a_different_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript_records(
                root,
                [
                    _message(
                        "assistant",
                        "计划：通过 prodctl 将 abc1234 部署到预发 app-pre-1 的 example-app。",
                    ),
                    _message("user", "可以，按照规划推进吧"),
                ],
            )

            result = _run_gate(
                _payload(
                    "./prodctl deploy --host app-pre-1 --service example-app --ref deadbeef",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "block")

    def test_adjacent_deploy_plan_with_named_ref_cannot_authorize_a_different_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript_records(
                root,
                [
                    _message(
                        "assistant",
                        "Deploy example-app from branch feature/demo to pre host app-pre-1 using prodctl.",
                    ),
                    _message("user", "ok, proceed with that plan"),
                ],
            )

            result = _run_gate(
                _payload(
                    "./prodctl deploy --host app-pre-1 --service example-app --ref feature/other",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "block", result)

    def test_adjacent_deploy_plan_with_named_ref_authorizes_that_exact_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript_records(
                root,
                [
                    _message(
                        "assistant",
                        "Deploy example-app from branch feature/demo to pre host app-pre-1 using prodctl.",
                    ),
                    _message("user", "ok, proceed with that plan"),
                ],
            )

            result = _run_gate(
                _payload(
                    "./prodctl deploy --host app-pre-1 --service example-app --ref feature/demo",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "allow", result)

    def test_adjacent_plan_cannot_authorize_a_different_host_in_the_same_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = json.loads(POLICY.read_text(encoding="utf-8"))
            policy["server_policy"]["hosts"]["app-pre-2"] = {
                "env": "pre",
                "aliases": ["second pre app host"],
                "allowed_app_dirs": ["/srv/example-app"],
                "services": ["example-app"],
            }
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            transcript = _write_transcript_records(
                root,
                [
                    _message("assistant", "Restart example-app on pre host app-pre-1 using prodctl."),
                    _message("user", "ok, proceed with that plan"),
                ],
            )

            result = _run_gate(
                _payload(
                    "./prodctl restart-service --host app-pre-2 --service example-app",
                    cwd=root,
                    policy_path=str(policy_path),
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "block")

    def test_configured_pre_routine_ssh_can_restart_multiple_configured_services_without_repeated_language(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy = json.loads(POLICY.read_text(encoding="utf-8"))
            policy["server_policy"]["hosts"]["app-pre-1"]["services"] = ["example-app", "worker"]
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            transcript = _write_transcript_records(
                root,
                [
                    _message("assistant", "Restart example-app on pre host app-pre-1 using ssh."),
                    _message("user", "ok, proceed with that plan"),
                ],
            )

            result = _run_gate(
                _payload(
                    "ssh app-pre-1 'cd /srv/example-app && systemctl restart example-app && systemctl restart worker'",
                    cwd=root,
                    policy_path=str(policy_path),
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "allow", result)
            self.assertTrue(result.get("silent"), result)

    def test_english_current_turn_negation_cannot_approve_adjacent_restart_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript_records(
                root,
                [
                    _message("assistant", "Restart example-app on pre host app-pre-1 using prodctl."),
                    _message("user", "yes, no restart"),
                ],
            )

            result = _run_gate(
                _payload(
                    "./prodctl restart-service --host app-pre-1 --service example-app",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "block")

    def test_without_migration_is_an_exclusion_not_a_rejection_of_the_deploy_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript_records(
                root,
                [
                    _message(
                        "assistant",
                        "Deploy example-app to pre host app-pre-1 with prodctl without migration, then verify.",
                    ),
                    _message("user", "ok, proceed with that plan"),
                ],
            )

            result = _run_gate(
                _payload(
                    "./prodctl deploy --host app-pre-1 --service example-app",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "allow", result)

    def test_remote_copy_writes_are_blocked_but_remote_reads_remain_allowed(self) -> None:
        cases = (
            ("scp runtime.conf app-pre-1:/srv/example-app/runtime.conf", "block"),
            ("rsync runtime.conf app-pre-1:/srv/example-app/runtime.conf", "block"),
            ("scp app-pre-1:/srv/example-app/runtime.conf ./runtime.conf", "allow"),
            ("rsync app-pre-1:/srv/example-app/runtime.conf ./runtime.conf", "allow"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for command, expected in cases:
                with self.subTest(command=command):
                    result = _run_gate(_payload(command, cwd=root))
                    self.assertEqual(result.get("decision"), expected, result)

    def test_natural_language_ssh_transport_is_not_misparsed_as_host(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript_records(
                root,
                [
                    _message("user", "先列计划"),
                    _message("assistant", "Restart example-app on pre app-pre-1 using ssh only."),
                    _message("user", "可以，按上述计划执行"),
                ],
            )

            result = _run_gate(
                _payload(
                    "ssh app-pre-1 'cd /srv/example-app && systemctl restart example-app'",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "allow")
            self.assertTrue(result.get("silent"), result)

    def test_chinese_pre_restart_authorization_works_with_discovered_policy_without_payload_policy_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / "codex-home"
            policy_dir = codex_home / "critical-action-policies"
            policy_dir.mkdir(parents=True)
            (policy_dir / "generic-server.json").write_text(POLICY.read_text(encoding="utf-8"), encoding="utf-8")
            transcript = _write_transcript(root, "重启预发环境的 example-app 服务并验证")
            payload = _payload(
                "./prodctl restart-service --host app-pre-1 --service example-app",
                cwd=root,
                transcript_path=str(transcript),
            )
            payload.pop("policy_path")
            env = os.environ.copy()
            env["CODEX_HOME"] = str(codex_home)

            result = _run_gate(payload, env=env)

            self.assertEqual(result.get("decision"), "allow")
            self.assertTrue(result.get("silent"), result)

    def test_chinese_pre_restart_authorization_works_without_any_policy_path_or_policy_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / "empty-codex-home"
            transcript = _write_transcript(root, "允许重启预发 aiserver 服务并验证")
            payload = _payload(
                "./prodctl restart-service --host app-pre-1 --service aiserver",
                cwd=root,
                transcript_path=str(transcript),
            )
            payload.pop("policy_path")
            env = os.environ.copy()
            env["CODEX_HOME"] = str(codex_home)
            env["HOME"] = str(root / "isolated-home")

            result = _run_gate(payload, env=env)

            self.assertEqual(result.get("decision"), "allow")
            self.assertTrue(result.get("silent"), result)

    def test_chinese_pre_deploy_authorization_works_without_any_policy_path_or_policy_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / "empty-codex-home"
            transcript = _write_transcript(root, "允许部署到预发")
            payload = _payload(
                "./prodctl deploy --host app-pre-1 --ref feature/demo",
                cwd=root,
                transcript_path=str(transcript),
            )
            payload.pop("policy_path")
            env = os.environ.copy()
            env["CODEX_HOME"] = str(codex_home)
            env["HOME"] = str(root / "isolated-home")

            result = _run_gate(payload, env=env)

            self.assertEqual(result.get("decision"), "allow")
            self.assertTrue(result.get("silent"), result)

    def test_adjacent_plan_approval_blocks_without_current_positive_exact_single_plan(self) -> None:
        exact_plan = "Plan: restart aiserver on pre host app-pre-1 using prodctl only."
        cases = (
            (
                "no_current_user_approval",
                [_message("user", "先给计划"), _message("assistant", exact_plan)],
                "./prodctl restart-service --host app-pre-1 --service aiserver",
                "current-turn",
            ),
            (
                "assistant_only_plan_text",
                [_message("user", "只检查状态"), _message("assistant", exact_plan)],
                "./prodctl restart-service --host app-pre-1 --service aiserver",
                "current-turn",
            ),
            (
                "negative_do_not_restart",
                [_message("user", "先给计划"), _message("assistant", exact_plan), _message("user", "不要重启")],
                "./prodctl restart-service --host app-pre-1 --service aiserver",
                "current-turn",
            ),
            (
                "negative_do_not_execute_yet",
                [_message("user", "先给计划"), _message("assistant", exact_plan), _message("user", "先别执行")],
                "./prodctl restart-service --host app-pre-1 --service aiserver",
                "current-turn",
            ),
            (
                "negative_short_do_not_restart",
                [_message("user", "先给计划"), _message("assistant", exact_plan), _message("user", "好，别重启")],
                "./prodctl restart-service --host app-pre-1 --service aiserver",
                "current-turn",
            ),
            (
                "negative_contrast_do_not_restart",
                [_message("user", "先给计划"), _message("assistant", exact_plan), _message("user", "可以，但别重启")],
                "./prodctl restart-service --host app-pre-1 --service aiserver",
                "current-turn",
            ),
            (
                "question_is_not_approval",
                [_message("user", "先给计划"), _message("assistant", exact_plan), _message("user", "允许吗？")],
                "./prodctl restart-service --host app-pre-1 --service aiserver",
                "current-turn",
            ),
            (
                "request_for_more_plan_detail_is_not_approval",
                [_message("user", "先给计划"), _message("assistant", exact_plan), _message("user", "可以说说重启计划吗？")],
                "./prodctl restart-service --host app-pre-1 --service aiserver",
                "current-turn",
            ),
            (
                "stale_non_adjacent_plan",
                [
                    _message("user", "先给计划"),
                    _message("assistant", exact_plan),
                    _message("user", "等一下"),
                    _message("assistant", "I checked status only; no restart plan is active."),
                    _message("user", "可以，按上述计划执行"),
                ],
                "./prodctl restart-service --host app-pre-1 --service aiserver",
                "current-turn",
            ),
            (
                "multiple_conflicting_plans",
                [
                    _message("user", "先给计划"),
                    _message(
                        "assistant",
                        "Plan A: restart aiserver on pre host app-pre-1. Plan B: deploy billing on test host app-test-1.",
                    ),
                    _message("user", "可以，按上述计划执行"),
                ],
                "./prodctl restart-service --host app-pre-1 --service aiserver",
                "matching",
            ),
            (
                "command_mismatch",
                [_message("user", "先给计划"), _message("assistant", exact_plan), _message("user", "可以，按上述计划执行")],
                "./prodctl restart-service --host app-test-1 --service aiserver",
                "matching",
            ),
        )

        for case_name, records, command, reason_marker in cases:
            with self.subTest(case=case_name):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    policy_path = _write_aiserver_policy(root)
                    transcript = _write_transcript_records(root, records)

                    result = _run_gate(_payload(command, cwd=root, policy_path=str(policy_path), transcript_path=str(transcript)))

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn(reason_marker, str(result.get("reason", "")).lower())

    def test_negated_adjacent_plan_scope_still_blocks_prodctl_semantic_authorization(self) -> None:
        cases = (
            (
                "negated_service",
                "Plan: do not restart aiserver; restart billing only on pre host app-pre-1 using prodctl.",
                "./prodctl restart-service --host app-pre-1 --service aiserver",
                "current-turn",
            ),
        )

        for case_name, plan, command, reason_marker in cases:
            with self.subTest(case=case_name):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    policy_path = _write_aiserver_policy(root)
                    transcript = _write_transcript_records(
                        root,
                        [_message("user", "先给计划"), _message("assistant", plan), _message("user", "可以，按上述计划执行")],
                    )

                    result = _run_gate(_payload(command, cwd=root, policy_path=str(policy_path), transcript_path=str(transcript)))

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn(reason_marker, str(result.get("reason", "")).lower())

    def test_no_policy_pre_and_test_raw_ssh_writes_fail_closed(self) -> None:
        commands = (
            "ssh app-pre-1 'cd /srv/example-app && systemctl restart aiserver'",
            "ssh app-test-1 'cd /srv/example-app && git pull'",
        )
        for command in commands:
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    codex_home = root / "empty-codex-home"
                    codex_home.mkdir()
                    payload = _payload(command, cwd=root)
                    payload.pop("policy_path")
                    env = os.environ.copy()
                    env["CODEX_HOME"] = str(codex_home)

                    result = _run_gate(payload, env=env)

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn("policy", str(result.get("reason", "")).lower())

    def test_assistant_or_tool_text_after_user_cannot_self_authorize_adjacent_plan(self) -> None:
        tails = (
            _message("assistant", "Plan: restart aiserver on pre host app-pre-1 using prodctl only."),
            _tool_call("exec_command", {"cmd": "./prodctl restart-service --host app-pre-1 --service aiserver"}),
        )

        for tail in tails:
            with self.subTest(tail=tail):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    policy_path = _write_aiserver_policy(root)
                    transcript = _write_transcript_records(root, [_message("user", "只检查状态，不要执行"), tail])

                    result = _run_gate(
                        _payload(
                            "./prodctl restart-service --host app-pre-1 --service aiserver",
                            cwd=root,
                            policy_path=str(policy_path),
                            transcript_path=str(transcript),
                        )
                    )

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn("current-turn", str(result.get("reason", "")).lower())

    def test_current_exact_single_message_english_prodctl_restart_authorization_still_allows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = _write_aiserver_policy(root)
            transcript = _write_transcript(root, "restart aiserver in pre using prodctl")

            result = _run_gate(
                _payload(
                    "./prodctl restart-service --host app-pre-1 --service aiserver",
                    cwd=root,
                    policy_path=str(policy_path),
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "allow")
            self.assertTrue(result.get("silent"), result)

    def test_prodctl_prod_writes_require_matching_current_turn_environment_and_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deploy = "./prodctl deploy --host app-prod-1 --ref resolved-ref --approval-token internal-token"

            missing_auth = _run_gate(_payload(deploy, cwd=root))
            self.assertEqual(missing_auth.get("decision"), "block")
            self.assertIn("current-turn", str(missing_auth.get("reason", "")).lower())

            wrong_env = _write_transcript(root, "deploy the current branch to pre using prodctl")
            wrong_env_result = _run_gate(_payload(deploy, cwd=root, transcript_path=str(wrong_env)))
            self.assertEqual(wrong_env_result.get("decision"), "block")

            wrong_action = _write_transcript(root, "restart example-app in prod using prodctl")
            wrong_action_result = _run_gate(_payload(deploy, cwd=root, transcript_path=str(wrong_action)))
            self.assertEqual(wrong_action_result.get("decision"), "block")

            deploy_auth = _write_transcript(root, "deploy the current branch to prod using prodctl")
            deploy_allowed = _run_gate(_payload(deploy, cwd=root, transcript_path=str(deploy_auth)))
            self.assertEqual(deploy_allowed.get("decision"), "allow")
            self.assertTrue(deploy_allowed.get("silent"), deploy_allowed)

            restart = "./prodctl restart-service --host app-prod-1 --service example-app --approval-token internal-token"
            restart_missing_auth = _run_gate(_payload(restart, cwd=root))
            self.assertEqual(restart_missing_auth.get("decision"), "block")
            self.assertIn("current-turn", str(restart_missing_auth.get("reason", "")).lower())

            restart_auth = _write_transcript(root, "restart example-app in prod using prodctl")
            restart_allowed = _run_gate(_payload(restart, cwd=root, transcript_path=str(restart_auth)))
            self.assertEqual(restart_allowed.get("decision"), "allow")
            self.assertTrue(restart_allowed.get("silent"), restart_allowed)

    def test_prodctl_prod_exact_commit_deploy_requires_detached_exception_language(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ref = "89a038be0cdf4525f9ea651c326f67470b365352"
            command = f"./prodctl deploy --host app-prod-1 --ref {ref} --approval-token internal-token"

            ordinary_auth = _write_transcript(root, "deploy the current branch to prod using prodctl")
            ordinary = _run_gate(_payload(command, cwd=root, transcript_path=str(ordinary_auth)))
            self.assertEqual(ordinary.get("decision"), "block")
            self.assertIn("detached", str(ordinary.get("reason", "")).lower())

            assistant_plan = (
                f"Plan: deploy commit {ref} to prod host app-prod-1 using prodctl. "
                "This will checkout-only and will not restart."
            )
            vague_auth = _write_transcript(root, "可以，按照规划推进吧", assistant_plan)
            vague = _run_gate(_payload(command, cwd=root, transcript_path=str(vague_auth)))
            self.assertEqual(vague.get("decision"), "block")
            self.assertIn("detached", str(vague.get("reason", "")).lower())

            explicit_auth = _write_transcript(
                root,
                f"允许生产 app-prod-1 使用 emergency detached commit 部署 ref={ref}，不走 master。",
            )
            explicit = _run_gate(_payload(command, cwd=root, transcript_path=str(explicit_auth)))
            self.assertEqual(explicit.get("decision"), "allow")
            self.assertTrue(explicit.get("silent"), explicit)

    def test_conditional_user_text_is_not_current_turn_server_write_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ref = "89a038be0cdf4525f9ea651c326f67470b365352"
            command = f"./prodctl deploy --host app-prod-1 --ref {ref} --approval-token internal-token"
            transcript = _write_transcript(
                root,
                "如果确认其他的也要部署，可以先不重启服务。现在 master 已经同步了修改，对吧？",
                f"Plan: deploy commit {ref} to prod host app-prod-1 using prodctl without restart.",
            )

            result = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

            self.assertEqual(result.get("decision"), "block")
            self.assertIn("current-turn", str(result.get("reason", "")).lower())

    def test_prodctl_prod_deploy_accepts_chinese_environment_action_aliases_without_user_plan_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript(root, "部署到正式应用机器对应区域")
            result = _run_gate(
                _payload(
                    "./prodctl deploy --host app-prod-1 --plan-id deploy-internal-1234",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "allow")
            self.assertTrue(result.get("silent"), result)
            rendered = json.dumps(result, ensure_ascii=False).lower()
            self.assertNotIn("plan-id", rendered)
            self.assertNotIn("token", rendered)

    def test_ops_top_level_policy_schema_aliases_authorize_us_prodctl_and_bounded_ssh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = root / "ops-policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "version": "ops-policy-test-v1",
                        "protected_refs": ["pre", "test", "master", "main"],
                        "hosts": {
                            "us": {
                                "env": "prod",
                                "ssh_aliases": ["aiserver-us-codex"],
                                "allowed_app_dirs": ["/opt/project/aiserver"],
                                "services": ["aiserver"],
                            }
                        },
                        "semantic_aliases": {
                            "env": {
                                "us": ["us", "美服", "美国"],
                                "prod": ["prod", "production", "正式", "生产", "线上"],
                            },
                            "action": {
                                "deploy": ["deploy", "部署", "发布", "上线"],
                                "restart": ["restart", "重启", "重启服务"],
                                "emergency_bypass": ["emergency bypass", "bypass prodctl", "紧急绕过", "应急直连"],
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            deploy_transcript = _write_transcript(root, "把当前分支部署到美服")
            deploy = _run_gate(
                _payload(
                    "./prodctl deploy --host us --plan-id deploy-internal-us-1234",
                    cwd=root,
                    policy_path=str(policy_path),
                    transcript_path=str(deploy_transcript),
                )
            )
            self.assertEqual(deploy.get("decision"), "allow")
            self.assertTrue(deploy.get("silent"), deploy)

            emergency_transcript = _write_transcript(root, "紧急绕过 prodctl，在美服重启 aiserver")
            bounded = _run_gate(
                _payload(
                    "ssh aiserver-us-codex 'cd /opt/project/aiserver && supervisorctl restart aiserver'",
                    cwd=root,
                    policy_path=str(policy_path),
                    transcript_path=str(emergency_transcript),
                )
            )
            self.assertEqual(bounded.get("decision"), "allow")
            self.assertTrue(bounded.get("silent"), bounded)

            wrong_dir = _run_gate(
                _payload(
                    "ssh aiserver-us-codex 'cd /tmp/aiserver && supervisorctl restart aiserver'",
                    cwd=root,
                    policy_path=str(policy_path),
                    transcript_path=str(emergency_transcript),
                )
            )
            self.assertEqual(wrong_dir.get("decision"), "block")
            self.assertIn("bounds", str(wrong_dir.get("reason", "")).lower())

    def test_prodctl_prod_plan_id_apply_without_host_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript(root, "部署到正式应用机器对应区域")
            result = _run_gate(
                _payload(
                    "./prodctl deploy --plan-id deploy-internal-1234",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "block")
            reason = str(result.get("reason", "")).lower()
            self.assertIn("host", reason)
            self.assertIn("plan", reason)

    def test_prodctl_plan_and_read_actions_are_allowed_without_write_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            commands = (
                "./prodctl plan-deploy --host app-prod-1 --ref feature/demo",
                "./prodctl status --host app-prod-1",
            )

            for command in commands:
                with self.subTest(command=command):
                    result = _run_gate(_payload(command, cwd=root))
                    self.assertEqual(result.get("decision"), "allow")
                    self.assertTrue(result.get("silent"), result)

    def test_prodctl_pre_and_test_mutations_use_semantic_authorization_without_user_plan_id(self) -> None:
        cases = (
            (
                "./prodctl deploy --host app-pre-1 --plan-id internal-pre-123",
                "把当前分支部署到预发环境并验证",
                "pre",
                "deploy",
            ),
            (
                "./prodctl restart-service --host app-test-1 --service example-app --plan-id internal-test-123",
                "重启测试环境的 example-app 服务并验证",
                "test",
                "restart",
            ),
        )

        for command, user_text, env_name, action_name in cases:
            with self.subTest(env=env_name, action=action_name):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)

                    blocked = _run_gate(_payload(command, cwd=root))
                    self.assertEqual(blocked.get("decision"), "block")
                    self.assertIn("current-turn", str(blocked.get("reason", "")).lower())

                    transcript = _write_transcript(root, user_text)
                    allowed = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(allowed.get("decision"), "allow")
                    self.assertTrue(allowed.get("silent"), allowed)
                    rendered = json.dumps(allowed, ensure_ascii=False).lower()
                    self.assertNotIn("hash", rendered)
                    self.assertNotIn("token", rendered)
                    self.assertNotIn("plan-id", rendered)

    def test_bounded_pre_and_test_prodctl_tokens_do_not_require_repeated_user_text(self) -> None:
        cases = (
            "./prodctl deploy --host app-pre-1 --service aiserver --ref abc1234 --approval-token bounded-pre",
            "./prodctl restart-service --host app-test-1 --service aiserver --approval-token bounded-test",
        )
        for command in cases:
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    policy_path = _write_aiserver_policy(root)
                    result = _run_gate(_payload(command, cwd=root, policy_path=str(policy_path)))
                    self.assertEqual(result.get("decision"), "allow", result)
                    self.assertTrue(result.get("silent"), result)

    def test_prodctl_mutation_to_unknown_host_fails_closed_when_policy_has_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = _write_transcript(root, "部署当前分支到正式环境")

            result = _run_gate(
                _payload(
                    "./prodctl deploy --host app-prod-unknown --plan-id internal-unknown-123",
                    cwd=root,
                    transcript_path=str(transcript),
                )
            )

            self.assertEqual(result.get("decision"), "block")
            reason = str(result.get("reason", "")).lower()
            self.assertIn("host", reason)
            self.assertIn("policy", reason)

    def test_malformed_discovered_policy_fails_closed_for_server_mutations_but_not_fast_path_or_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / "codex-home"
            policy_dir = codex_home / "critical-action-policies"
            policy_dir.mkdir(parents=True)
            (policy_dir / "bad.json").write_text("{ malformed policy", encoding="utf-8")
            env = os.environ.copy()
            env["CODEX_HOME"] = str(codex_home)

            ordinary_payload = _payload("python -V", cwd=root)
            ordinary_payload.pop("policy_path")
            ordinary = _run_gate(ordinary_payload, env=env)
            self.assertEqual(ordinary.get("decision"), "allow")
            self.assertTrue(ordinary.get("silent"), ordinary)

            status_payload = _payload("./prodctl status --host app-prod-1", cwd=root)
            status_payload.pop("policy_path")
            status = _run_gate(status_payload, env=env)
            self.assertEqual(status.get("decision"), "allow")
            self.assertTrue(status.get("silent"), status)

            plan_payload = _payload("./prodctl plan-deploy --host app-prod-1 --ref feature/demo", cwd=root)
            plan_payload.pop("policy_path")
            plan = _run_gate(plan_payload, env=env)
            self.assertEqual(plan.get("decision"), "allow")
            self.assertTrue(plan.get("silent"), plan)

            transcript = _write_transcript(root, "部署当前分支到正式环境")
            prodctl_payload = _payload(
                "./prodctl deploy --host app-prod-1 --plan-id internal-prod-123",
                cwd=root,
                transcript_path=str(transcript),
            )
            prodctl_payload.pop("policy_path")
            prodctl = _run_gate(prodctl_payload, env=env)
            self.assertEqual(prodctl.get("decision"), "block")
            self.assertIn("policy", str(prodctl.get("reason", "")).lower())

            ssh_payload = _payload(
                "ssh app-prod-1 'cd /srv/example-app && systemctl restart example-app'",
                cwd=root,
                transcript_path=str(transcript),
            )
            ssh_payload.pop("policy_path")
            ssh = _run_gate(ssh_payload, env=env)
            self.assertEqual(ssh.get("decision"), "block")
            self.assertIn("policy", str(ssh.get("reason", "")).lower())

    def test_project_policy_does_not_claim_unrelated_ssh_hosts_but_fails_closed_for_env_like_unknown_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            unrelated = _run_gate(
                _payload(
                    "ssh docs-box 'cd /srv/docs && git pull && systemctl restart docs'",
                    cwd=root,
                )
            )
            self.assertEqual(unrelated.get("decision"), "allow")
            self.assertTrue(unrelated.get("silent"), unrelated)

            prod_like = _run_gate(
                _payload(
                    "ssh app-prod-unknown 'cd /srv/example-app && git pull && systemctl restart example-app'",
                    cwd=root,
                )
            )
            self.assertEqual(prod_like.get("decision"), "block")
            reason = str(prod_like.get("reason", "")).lower()
            self.assertIn("host", reason)
            self.assertIn("policy", reason)

            pre_like = _run_gate(
                _payload(
                    "ssh app-pre-unknown 'cd /srv/example-app && git pull && systemctl restart example-app'",
                    cwd=root,
                )
            )
            self.assertEqual(pre_like.get("decision"), "block")
            reason = str(pre_like.get("reason", "")).lower()
            self.assertIn("host", reason)
            self.assertIn("policy", reason)

            test_like = _run_gate(
                _payload(
                    "ssh app-test-unknown 'cd /srv/example-app && git pull && systemctl restart example-app'",
                    cwd=root,
                )
            )
            self.assertEqual(test_like.get("decision"), "block")
            reason = str(test_like.get("reason", "")).lower()
            self.assertIn("host", reason)
            self.assertIn("policy", reason)

    def test_known_prod_ssh_write_cannot_bypass_gate_with_common_transparent_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            commands = (
                "env ssh app-prod-1 'cd /srv/example-app && systemctl restart example-app'",
                "command ssh app-prod-1 'cd /srv/example-app && systemctl restart example-app'",
                "timeout 5 ssh app-prod-1 'cd /srv/example-app && systemctl restart example-app'",
            )

            for command in commands:
                with self.subTest(command=command):
                    result = _run_gate(_payload(command, cwd=root))

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn("prodctl", str(result.get("reason", "")).lower())

    def test_ordinary_git_push_stays_on_fast_path_with_common_transparent_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            commands = (
                "env TEST_MODE=1 git push origin feature/demo",
                "command git push origin feature/demo",
                "timeout 5 git push origin feature/demo",
            )

            for command in commands:
                with self.subTest(command=command):
                    result = _run_gate(_payload(command, cwd=root))

                    self.assertEqual(result.get("decision"), "allow")
                    self.assertTrue(result.get("silent"), result)

    def test_absolute_executable_paths_cannot_bypass_ssh_or_git_guards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cases = (
                (
                    "/usr/bin/ssh app-prod-1 'cd /srv/example-app && systemctl restart example-app'",
                    "prodctl",
                ),
                (
                    "env PILOT=1 /usr/bin/ssh app-prod-1 'cd /srv/example-app && systemctl restart example-app'",
                    "prodctl",
                ),
                (
                    "/usr/bin/env PILOT=1 /usr/bin/ssh app-prod-1 'cd /srv/example-app && systemctl restart example-app'",
                    "prodctl",
                ),
                (
                    "env -i /usr/bin/ssh app-prod-1 'cd /srv/example-app && systemctl restart example-app'",
                    "prodctl",
                ),
                (
                    "env --ignore-environment /usr/bin/ssh app-prod-1 'cd /srv/example-app && systemctl restart example-app'",
                    "prodctl",
                ),
                (
                    "env -S \"/usr/bin/ssh app-prod-1 'cd /srv/example-app && systemctl restart example-app'\"",
                    "prodctl",
                ),
                (
                    "nohup /usr/bin/ssh app-prod-1 'cd /srv/example-app && systemctl restart example-app'",
                    "prodctl",
                ),
                ("/usr/bin/git push origin feature/demo", None),
                ("env PILOT=1 /usr/bin/git push origin feature/demo", None),
                ("/usr/bin/env PILOT=1 /usr/bin/git push origin feature/demo", None),
                ("env -i /usr/bin/git push origin feature/demo", None),
                ("env -S '/usr/bin/git push origin feature/demo'", None),
                ("nohup /usr/bin/git push origin feature/demo", None),
                ("env -S 'prodctl deploy --host app-prod-1 --plan-id pilot-plan'", "current-turn"),
            )

            for command, reason_fragment in cases:
                with self.subTest(command=command):
                    result = _run_gate(_payload(command, cwd=root))

                    if reason_fragment is None:
                        self.assertEqual(result.get("decision"), "allow")
                        self.assertTrue(result.get("silent"), result)
                    else:
                        self.assertEqual(result.get("decision"), "block")
                        self.assertIn(reason_fragment, str(result.get("reason", "")).lower())

            for command in (
                "env -i /usr/bin/git status --short",
                "nohup /usr/bin/git status --short",
            ):
                with self.subTest(command=command):
                    result = _run_gate(_payload(command, cwd=root))
                    self.assertEqual(result.get("decision"), "allow")
                    self.assertTrue(result.get("silent"), result)

    def test_high_risk_commands_cannot_bypass_gate_through_shell_command_strings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cases = (
                ("bash -c 'git push origin feature/demo'", None),
                ("sh -lc 'git push origin feature/demo'", None),
                (
                    'bash -c "ssh app-prod-1 \'cd /srv/example-app && systemctl restart example-app\'"',
                    "prodctl",
                ),
                (
                    'sh -lc "ssh app-prod-1 \'cd /srv/example-app && systemctl restart example-app\'"',
                    "prodctl",
                ),
                (
                    "bash -c './prodctl deploy --host app-prod-1 --plan-id internal-prod-123'",
                    "current-turn",
                ),
                (
                    "sh -lc './prodctl deploy --host app-prod-1 --plan-id internal-prod-123'",
                    "current-turn",
                ),
            )

            for command, reason_marker in cases:
                with self.subTest(command=command):
                    result = _run_gate(_payload(command, cwd=root))

                    if reason_marker is None:
                        self.assertEqual(result.get("decision"), "allow")
                        self.assertTrue(result.get("silent"), result)
                    else:
                        self.assertEqual(result.get("decision"), "block")
                        self.assertIn(reason_marker, str(result.get("reason", "")).lower())

    def test_prodctl_write_cannot_bypass_current_turn_authorization_through_python_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            commands = (
                "python ./prodctl deploy --host app-prod-1 --plan-id internal-prod-123",
                "python3 ./prodctl deploy --host app-prod-1 --plan-id internal-prod-123",
            )

            for command in commands:
                with self.subTest(command=command):
                    result = _run_gate(_payload(command, cwd=root))

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn("current-turn", str(result.get("reason", "")).lower())

    def test_protected_multi_ref_push_cannot_use_single_or_misaligned_intent(self) -> None:
        cases = (
            (
                "git push origin feature/demo HEAD:pre",
                "push feature/demo and HEAD to pre",
                "pre",
                "feature/demo",
            ),
            (
                "git push origin HEAD:pre HEAD:main",
                "push HEAD to pre and main",
                "pre",
                "HEAD",
            ),
        )

        for command, user_text, state_target, state_source in cases:
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "repo"
                    _init_git_repo(root)
                    transcript = _write_transcript(root, user_text)
                    _write_merge_safety_state(
                        root,
                        operation="push",
                        target_ref=state_target,
                        source_ref=state_source,
                        precheck_ok=True,
                        validation_ok=True,
                        review_ok=True,
                        lineage_ok=True,
                        postcheck_ok=True,
                    )

                    result = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn("protected", str(result.get("reason", "")).lower())

    def test_all_ordinary_multi_ref_pushes_require_separate_single_ref_commands(self) -> None:
        cases = (
            ("git push origin feature/demo feature/other", "push feature/demo to origin"),
            ("git push origin feature/demo feature/other", "push feature/demo and feature/other to origin"),
            (
                "git push origin feature/demo:feature/demo feature/other:feature/other",
                "push feature/demo to origin",
            ),
            (
                "git push origin feature/demo:feature/demo feature/other:feature/other",
                "push feature/demo and feature/other to origin",
            ),
        )

        for command, user_text in cases:
            with self.subTest(command=command, user_text=user_text):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "repo"
                    _init_git_repo(root)
                    transcript = _write_transcript(root, user_text)

                    result = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(result.get("decision"), "block")
                    reason = str(result.get("reason", "")).lower()
                    self.assertIn("multiple", reason)
                    self.assertIn("single-ref", reason)

    def test_protected_ref_deletion_is_blocked_for_empty_source_and_delete_flag(self) -> None:
        cases = (
            ("git push origin :pre", "push deletion of pre to origin", "HEAD"),
            ("git push origin --delete pre", "push delete pre to origin", "pre"),
        )

        for command, user_text, state_source in cases:
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "repo"
                    _init_git_repo(root)
                    transcript = _write_transcript(root, user_text)
                    _write_merge_safety_state(
                        root,
                        operation="push",
                        target_ref="pre",
                        source_ref=state_source,
                        precheck_ok=True,
                        validation_ok=True,
                        review_ok=True,
                        lineage_ok=True,
                        postcheck_ok=True,
                    )

                    result = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn("delete", str(result.get("reason", "")).lower())

    def test_push_all_and_mirror_are_rejected_as_unsafe_bulk_writes(self) -> None:
        cases = (
            ("git push --all origin", "push all branches to origin", ("--all", "all refs", "bulk")),
            ("git push --mirror origin", "push mirror to origin", ("--mirror", "mirror", "bulk")),
        )

        for command, user_text, reason_markers in cases:
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "repo"
                    _init_git_repo(root)
                    transcript = _write_transcript(root, user_text)

                    result = _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

                    self.assertEqual(result.get("decision"), "block")
                    reason = str(result.get("reason", "")).lower()
                    self.assertTrue(any(marker in reason for marker in reason_markers), result)

    def test_prodctl_write_cannot_bypass_current_turn_authorization_with_common_transparent_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            commands = (
                "env ./prodctl deploy --host app-prod-1 --plan-id internal-prod-123",
                "command ./prodctl deploy --host app-prod-1 --plan-id internal-prod-123",
                "timeout 5 ./prodctl deploy --host app-prod-1 --plan-id internal-prod-123",
            )

            for command in commands:
                with self.subTest(command=command):
                    result = _run_gate(_payload(command, cwd=root))

                    self.assertEqual(result.get("decision"), "block")
                    self.assertIn("current-turn", str(result.get("reason", "")).lower())

    def test_risky_git_ssh_and_prodctl_cannot_bypass_gate_through_shell_or_python_wrappers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _init_git_repo(root, branch="feature/demo")
            commands = (
                ("bash -lc 'git push origin feature/demo'", None),
                ("sh -c \"ssh app-prod-1 'cd /srv/example-app && systemctl restart example-app'\"", "prodctl"),
                ("env bash -lc './prodctl deploy --host app-prod-1 --plan-id internal-prod-123'", "current-turn"),
                ("python3 ./prodctl deploy --host app-prod-1 --plan-id internal-prod-123", "current-turn"),
            )

            for command, expected_reason in commands:
                with self.subTest(command=command):
                    result = _run_gate(_payload(command, cwd=root))

                    if expected_reason is None:
                        self.assertEqual(result.get("decision"), "allow")
                        self.assertTrue(result.get("silent"), result)
                    else:
                        self.assertEqual(result.get("decision"), "block")
                        self.assertIn(expected_reason, str(result.get("reason", "")).lower())


if __name__ == "__main__":
    unittest.main()
