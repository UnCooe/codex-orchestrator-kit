import importlib.util
import json
import re
import shlex
import subprocess
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "merge_guard.py"
SPEC = importlib.util.spec_from_file_location("merge_guard", SCRIPT)
merge_guard = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(merge_guard)


def run(cmd, cwd, **kwargs):
    return subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, **kwargs)


def init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    run(["git", "init"], path)
    run(["git", "config", "user.email", "test@example.com"], path)
    run(["git", "config", "user.name", "Test User"], path)
    (path / "README.md").write_text("base\n", encoding="utf-8")
    run(["git", "add", "README.md"], path)
    run(["git", "commit", "-m", "base"], path)


def current_branch(repo: Path) -> str:
    return run(["git", "symbolic-ref", "--short", "HEAD"], repo).stdout.strip()


def guard(*args, cwd: Path | None = None, input_payload: dict | None = None):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd) if cwd else None,
        input=json.dumps(input_payload) if input_payload is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout, proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def init_intent(
    repo: Path,
    source_ref: str = "HEAD",
    target_ref: str = "HEAD",
    merge_mode: str = "merge --no-commit",
    allowed_paths: str = "README.md",
) -> None:
    code, payload = guard(
        "init",
        "--cwd",
        str(repo),
        "--goal",
        "test merge",
        "--target-ref",
        target_ref,
        "--source-ref",
        source_ref,
        "--merge-mode",
        merge_mode,
        "--allowed-paths",
        allowed_paths,
        "--acceptance",
        "tests",
    )
    assert code == 0, payload


def commit_file(repo: Path, path: str, text: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    run(["git", "add", path], repo)
    run(["git", "commit", "-m", message], repo)
    return run(["git", "rev-parse", "HEAD"], repo).stdout.strip()


def test_tool_state_only_dirty_is_ignored_for_precheck(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    init_intent(repo)
    state = repo / ".codex-local" / "merge-safety" / "current.json"
    state.write_text(state.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    code, payload = guard("check-pre", "--cwd", str(repo))

    assert code == 0, payload
    assert payload["dirty"] == []
    assert payload["dirty_classification"] == "tool_state_only"
    assert payload["dirty_ignored_tool_state"]


def test_code_dirty_blocks_precheck_with_classification(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    init_intent(repo)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    code, payload = guard("check-pre", "--cwd", str(repo))

    assert code == 1
    assert "worktree is dirty before merge execution" in payload["errors"]
    assert payload["dirty_classification"] == "code_dirty"
    assert payload["checked_root"] == str(repo.resolve())


def test_hook_pretool_uses_tool_workdir_instead_of_payload_cwd(tmp_path):
    main = tmp_path / "main"
    other = tmp_path / "other"
    init_repo(main)
    init_repo(other)
    init_intent(other)
    (main / "README.md").write_text("dirty main\n", encoding="utf-8")

    payload = {
        "cwd": str(main),
        "tool_name": "functions.exec_command",
        "tool_input": {
            "workdir": str(other),
            "cmd": "git merge --no-commit HEAD",
        },
    }
    code, result = guard("hook-pretool", input_payload=payload)

    assert code == 0
    assert result["should_block"] is False, result
    assert result["checked_root"] == str(other.resolve())
    assert result["root_source"] == "tool_input.workdir"
    assert result["dirty_classification"] == "tool_state_only"


def test_hook_pretool_uses_git_dash_c_root(tmp_path):
    main = tmp_path / "main"
    other = tmp_path / "other"
    init_repo(main)
    init_repo(other)
    init_intent(other)
    (main / "README.md").write_text("dirty main\n", encoding="utf-8")

    payload = {
        "cwd": str(main),
        "tool_name": "functions.exec_command",
        "tool_input": {
            "cmd": f"git -C {other} merge --no-commit HEAD",
        },
    }
    code, result = guard("hook-pretool", input_payload=payload)

    assert code == 0
    assert result["should_block"] is False, result
    assert result["checked_root"] == str(other.resolve())
    assert result["root_source"] == "git.invocation"


def test_hook_pretool_uses_quoted_git_dash_c_root_with_spaces(tmp_path):
    main = tmp_path / "main repo"
    other = tmp_path / "other repo"
    init_repo(main)
    init_repo(other)
    init_intent(other)
    (main / "README.md").write_text("dirty main\n", encoding="utf-8")

    payload = {
        "cwd": str(main),
        "tool_name": "functions.exec_command",
        "tool_input": {
            "cmd": f'git -C "{other}" merge --no-commit HEAD',
        },
    }
    code, result = guard("hook-pretool", input_payload=payload)

    assert code == 0
    assert result["should_block"] is False, result
    assert result["checked_root"] == str(other.resolve())
    assert result["root_source"] == "git.invocation"


def test_hook_pretool_blocks_git_apply_during_active_merge_safety(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    init_intent(repo)

    payload = {
        "cwd": str(repo),
        "tool_name": "functions.exec_command",
        "tool_input": {
            "cmd": "git apply /tmp/change.patch",
        },
    }
    code, result = guard("hook-pretool", input_payload=payload)

    assert code == 0
    assert result["should_block"] is True
    assert "manual patch application is blocked" in result["message"]


def test_status_classifies_tool_state_only(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    init_intent(repo)

    code, payload = guard("status", "--cwd", str(repo))

    assert code == 0
    assert payload["dirty"]["classification"] == "tool_state_only"
    assert payload["can_start_merge"] is True


def test_plan_clean_repo_recommends_init_and_check_pre(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)

    code, payload = guard(
        "plan",
        "--cwd",
        str(repo),
        "--goal",
        "merge feature to pre",
        "--target-ref",
        "origin/pre",
        "--source-ref",
        "feature",
        "--merge-mode",
        "merge --no-commit",
    )

    assert code == 0, payload
    assert payload["lane"] == "protected_integration"
    assert payload["can_initialize_intent"] is True
    assert payload["can_mutate"] is False
    assert payload["missing_fields"] == []
    assert "reviewer" in payload["required_subagents"]
    assert "check-pre" in "\n".join(payload["recommended_commands"])
    assert "Initialize merge intent" in payload["next_action"]


def test_plan_recommended_commands_use_current_python_and_quote_space_paths(tmp_path):
    repo = tmp_path / "repo with space"
    init_repo(repo)

    code, payload = guard(
        "plan",
        "--cwd",
        str(repo),
        "--goal",
        "merge feature to pre",
        "--target-ref",
        "origin/pre",
        "--source-ref",
        "feature",
        "--merge-mode",
        "merge --no-commit",
    )

    assert code == 0, payload
    commands = "\n".join(payload["recommended_commands"])
    assert sys.executable in commands
    assert str(SCRIPT) in commands
    assert shlex.quote(str(repo)) in commands or f'"{repo}"' in commands
    assert not re.search(r"(^|\s)(python|python3)\s+.+merge_guard\.py", commands)


def test_plan_dirty_repo_blocks_intent_initialization(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    code, payload = guard(
        "plan",
        "--cwd",
        str(repo),
        "--goal",
        "merge feature",
        "--target-ref",
        "HEAD",
        "--source-ref",
        "feature",
        "--merge-mode",
        "merge --no-commit",
    )

    assert code == 0, payload
    assert payload["can_initialize_intent"] is False
    assert "worktree is dirty before merge intent initialization" in payload["blockers"]
    assert payload["dirty"]["classification"] == "code_dirty"
    assert "clean dedicated integration worktree" in payload["next_action"]


def test_plan_missing_fields_asks_once_for_intent(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)

    code, payload = guard("plan", "--cwd", str(repo), "--goal", "merge something")

    assert code == 0, payload
    assert payload["can_initialize_intent"] is False
    assert payload["missing_fields"] == ["target_ref", "source_ref", "merge_mode"]
    assert "merge intent fields are missing" in payload["blockers"]


def test_plan_active_intent_allows_mutating_command_after_precheck(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    init_intent(repo)

    code, payload = guard("plan", "--cwd", str(repo))

    assert code == 0, payload
    assert payload["intent_active"] is True
    assert payload["can_mutate"] is True
    assert payload["blockers"] == []
    assert "declared mutating" in payload["next_action"]
    assert "record-evidence" in "\n".join(payload["recommended_commands"])


def test_conflict_marker_detection_ignores_plain_separator_lines(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    init_intent(repo)
    (repo / "README.md").write_text("title\n=======\nbody\n", encoding="utf-8")
    run(["git", "add", "README.md"], repo)
    run(["git", "commit", "-m", "doc separator"], repo)

    code, payload = guard(
        "check-post",
        "--cwd",
        str(repo),
        "--assistant-message",
        "tests: py_compile passed; review: no P1-now; subagent_status=not_used",
    )

    assert code == 0, payload
    assert payload["conflict_files"] == []
    assert payload["conflict_markers"] == []


def test_codex_local_handoff_notes_are_ignored_for_precheck(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    init_intent(repo)
    note = repo / ".codex-local" / "handoff.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("local handoff only\n", encoding="utf-8")

    code, payload = guard("check-pre", "--cwd", str(repo))

    assert code == 0, payload
    assert payload["dirty"] == []
    assert payload["dirty_classification"] == "tool_state_only"
    assert any(".codex-local/handoff.md" in item for item in payload["dirty_ignored_tool_state"])


def test_hook_pretool_uses_leading_cd_root(tmp_path):
    main = tmp_path / "main"
    other = tmp_path / "other"
    init_repo(main)
    init_repo(other)
    init_intent(other)
    (main / "README.md").write_text("dirty main\n", encoding="utf-8")

    payload = {
        "cwd": str(main),
        "tool_name": "functions.exec_command",
        "tool_input": {
            "cmd": f"cd {other} && git merge --no-commit HEAD",
        },
    }
    code, result = guard("hook-pretool", input_payload=payload)

    assert code == 0
    assert result["should_block"] is False, result
    assert result["checked_root"] == str(other.resolve())
    assert result["root_source"] == "shell.cd"


def test_hook_pretool_uses_quoted_leading_cd_root_with_spaces(tmp_path):
    main = tmp_path / "main repo"
    other = tmp_path / "other repo"
    init_repo(main)
    init_repo(other)
    init_intent(other)
    (main / "README.md").write_text("dirty main\n", encoding="utf-8")

    payload = {
        "cwd": str(main),
        "tool_name": "functions.exec_command",
        "tool_input": {
            "cmd": f'cd "{other}" && git merge --no-commit HEAD',
        },
    }
    code, result = guard("hook-pretool", input_payload=payload)

    assert code == 0
    assert result["should_block"] is False, result
    assert result["checked_root"] == str(other.resolve())
    assert result["root_source"] == "shell.cd"


def test_hook_pretool_blocks_git_pull(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)

    payload = {
        "cwd": str(repo),
        "tool_name": "functions.exec_command",
        "tool_input": {"cmd": "git pull"},
    }
    code, result = guard("hook-pretool", input_payload=payload)

    assert code == 0
    assert result["should_block"] is True
    assert "implicit git pull is not allowed" in result["errors"]


def test_hook_pretool_blocks_guard_setup_and_merge_in_one_command(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)

    payload = {
        "cwd": str(repo),
        "tool_name": "functions.exec_command",
        "tool_input": {
            "cmd": (
                f"{sys.executable} {SCRIPT} init --cwd {repo} --goal test --target-ref HEAD "
                "--source-ref HEAD --merge-mode 'merge --no-commit' --allowed-paths README.md "
                "--acceptance tests && git merge --no-commit HEAD"
            ),
        },
    }
    code, result = guard("hook-pretool", input_payload=payload)

    assert code == 0
    assert result["should_block"] is True
    assert "do not combine merge_guard.py init/check-pre and git merge/rebase/cherry-pick/pull in one shell command" in result["errors"]
    assert "command separately" in result["next_action"]


def test_hook_pretool_blocks_whole_file_resolution(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)

    payload = {
        "cwd": str(repo),
        "tool_name": "functions.exec_command",
        "tool_input": {"cmd": "git checkout --ours README.md"},
    }
    code, result = guard("hook-pretool", input_payload=payload)

    assert code == 0
    assert result["should_block"] is True
    assert "git checkout --ours/--theirs requires explicit user authorization and manual guard update" in result["errors"]


def test_hook_pretool_blocks_stale_intent_for_merge(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    init_intent(repo)
    state = repo / ".codex-local" / "merge-safety" / "current.json"
    data = json.loads(state.read_text(encoding="utf-8"))
    data["updated_at"] = "2000-01-01T00:00:00Z"
    data["created_at"] = "2000-01-01T00:00:00Z"
    state.write_text(json.dumps(data), encoding="utf-8")

    payload = {
        "cwd": str(repo),
        "tool_name": "functions.exec_command",
        "tool_input": {"cmd": "git merge --no-commit HEAD"},
    }
    code, result = guard("hook-pretool", input_payload=payload)

    assert code == 0
    assert result["should_block"] is True
    assert "stale merge safety intent" in result["errors"]


def write_transcript(path: Path, tool_payloads: list[dict]) -> None:
    items = [
        {"payload": {"type": "message", "role": "user", "content": "merge please"}},
    ]
    for payload in tool_payloads:
        items.append({"payload": payload})
    path.write_text("\n".join(json.dumps(item) for item in items) + "\n", encoding="utf-8")


def test_hook_stop_uses_transcript_merge_workdir(tmp_path):
    main = tmp_path / "main"
    other = tmp_path / "other"
    transcript = tmp_path / "session.jsonl"
    init_repo(main)
    init_repo(other)
    init_intent(other)
    (other / "README.md").write_text("integrated\n", encoding="utf-8")
    run(["git", "add", "README.md"], other)
    run(["git", "commit", "-m", "integrated"], other)
    write_transcript(transcript, [{
        "name": "functions.exec_command",
        "arguments": {"workdir": str(other), "cmd": "git merge --no-commit HEAD"},
        "cwd": str(main),
    }])

    payload = {
        "cwd": str(main),
        "transcript_path": str(transcript),
        "last_assistant_message": "tests: py_compile passed; review: no P1-now; subagent_status=not_used",
    }
    code, result = guard("hook-stop", input_payload=payload)

    assert code == 0
    assert result["should_block"] is False, result
    assert result["checked_root"] == str(other.resolve())
    assert result["stop_root_candidates"][0]["root"] == str(other.resolve())


def test_hook_stop_uses_transcript_git_dash_c_root(tmp_path):
    main = tmp_path / "main"
    other = tmp_path / "other"
    transcript = tmp_path / "session.jsonl"
    init_repo(main)
    init_repo(other)
    init_intent(other)
    (other / "README.md").write_text("integrated\n", encoding="utf-8")
    run(["git", "add", "README.md"], other)
    run(["git", "commit", "-m", "integrated"], other)
    write_transcript(transcript, [{
        "name": "functions.exec_command",
        "arguments": {"cmd": f"git -C {other} merge --no-commit HEAD"},
        "cwd": str(main),
    }])

    payload = {
        "cwd": str(main),
        "transcript_path": str(transcript),
        "last_assistant_message": "tests: py_compile passed; review: no P1-now; subagent_status=not_used",
    }
    code, result = guard("hook-stop", input_payload=payload)

    assert code == 0
    assert result["should_block"] is False, result
    assert result["checked_root"] == str(other.resolve())
    assert result["root_source"] == "git.invocation"


def test_hook_stop_uses_transcript_leading_cd_root(tmp_path):
    main = tmp_path / "main"
    other = tmp_path / "other"
    transcript = tmp_path / "session.jsonl"
    init_repo(main)
    init_repo(other)
    init_intent(other)
    (other / "README.md").write_text("integrated\n", encoding="utf-8")
    run(["git", "add", "README.md"], other)
    run(["git", "commit", "-m", "integrated"], other)
    write_transcript(transcript, [{
        "name": "functions.exec_command",
        "arguments": {"cmd": f"cd {other} && git merge --no-commit HEAD"},
        "cwd": str(main),
    }])

    payload = {
        "cwd": str(main),
        "transcript_path": str(transcript),
        "last_assistant_message": "tests: py_compile passed; review: no P1-now; subagent_status=not_used",
    }
    code, result = guard("hook-stop", input_payload=payload)

    assert code == 0
    assert result["should_block"] is False, result
    assert result["checked_root"] == str(other.resolve())
    assert result["root_source"] == "shell.cd"


def test_hook_stop_blocks_invalid_active_state(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    state = repo / ".codex-local" / "merge-safety" / "current.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{not json", encoding="utf-8")

    payload = {
        "cwd": str(repo),
        "last_assistant_message": "done",
    }
    code, result = guard("hook-stop", input_payload=payload)

    assert code == 0
    assert result["should_block"] is True
    assert result["checked_root"] == str(repo.resolve())


def test_hook_stop_postcheck_exception_fails_closed(tmp_path, monkeypatch=None):
    repo = tmp_path / "repo"
    init_repo(repo)
    init_intent(repo)
    payload = {
        "cwd": str(repo),
        "last_assistant_message": "tests: py_compile passed; review: no P1-now; subagent_status=not_used",
    }
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    original = merge_guard._worktree_changed_files
    merge_guard._worktree_changed_files = lambda root: (_ for _ in ()).throw(RuntimeError("forced post failure"))
    try:
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = merge_guard.cmd_hook_stop(type("Args", (), {"payload": str(payload_path)})())
        direct = json.loads(buf.getvalue())
    finally:
        merge_guard._worktree_changed_files = original

    assert rc == 0
    assert direct["should_block"] is True
    assert direct["message"] == "merge safety post-check failed closed"
    assert "forced post failure" in direct["errors"]


def test_check_post_includes_next_action_for_missing_evidence(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    init_intent(repo)
    commit_file(repo, "README.md", "integrated\n", "integrated")

    code, payload = guard("check-post", "--cwd", str(repo))

    assert code == 1
    assert "test or verification evidence is missing" in payload["errors"]
    assert "Record missing evidence" in payload["next_action"]
    assert "test" in payload["next_action"]
    assert "review" in payload["next_action"]


def test_report_includes_next_action_for_allowed_path_mismatch(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    base_branch = current_branch(repo)
    run(["git", "checkout", "-b", "feature"], repo)
    commit_file(repo, "extra.txt", "extra\n", "extra")
    run(["git", "checkout", base_branch], repo)
    init_intent(repo, "feature")

    code, payload = guard("report", "--cwd", str(repo), "--skip-post")

    assert code == 0
    assert payload["pre"]["ok"] is False
    assert "source diff contains files outside allowed_paths" in payload["pre"]["errors"]
    assert "fresh integration worktree" in payload["pre"]["next_action"]


def prepare_master_based_cherry_pick(repo: Path) -> tuple[str, str]:
    base_branch = current_branch(repo)
    run(["git", "checkout", "-b", "pre"], repo)
    commit_file(repo, "pre.txt", "pre only\n", "pre diverges")
    run(["git", "checkout", base_branch], repo)
    commit_file(repo, "master-only.txt", "master only\n", "master only")
    run(["git", "checkout", "-b", "feature"], repo)
    feature_sha = commit_file(repo, "README.md", "base\nfeature\n", "feature change")
    run(["git", "checkout", "-b", "integration", "pre"], repo)
    return feature_sha, "feature"


def test_check_pre_cherry_pick_uses_exact_commit_files_not_branch_history(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    feature_sha, _feature_ref = prepare_master_based_cherry_pick(repo)
    init_intent(repo, source_ref=feature_sha, merge_mode="cherry-pick -x")

    code, payload = guard("check-pre", "--cwd", str(repo))

    assert code == 0, payload
    assert payload["changed_files"] == ["README.md"]
    assert "master-only.txt" not in payload["changed_files"]


def test_check_post_allows_narrow_cherry_pick_with_explicit_patch_equivalent_lineage(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    feature_sha, feature_ref = prepare_master_based_cherry_pick(repo)
    init_intent(repo, source_ref=feature_sha, merge_mode="cherry-pick -x")
    code, payload = guard("check-pre", "--cwd", str(repo))
    assert code == 0, payload
    run(["git", "cherry-pick", "-x", feature_sha], repo)
    code, payload = guard(
        "record-evidence",
        "--cwd",
        str(repo),
        "--test",
        "focused tests passed",
        "--review",
        "reviewed; no P1-now",
        "--subagents-closed",
    )
    assert code == 0, payload
    code, payload = guard(
        "record-lineage",
        "--cwd",
        str(repo),
        "--validated-ref",
        feature_sha,
        "--source-ref",
        feature_ref,
        "--allow-patch-equivalent",
    )
    assert code == 0, payload

    code, payload = guard("check-post", "--cwd", str(repo))

    assert code == 0, payload
    assert payload["lineage"]["source_in_result"]["ok"] is False
    assert payload["lineage"]["validated_in_result"]["single_patch_equivalent"] is True
    assert payload["lineage"]["patch_equivalent_result_allowed"] is True


def test_check_post_blocks_broad_source_cherry_pick_without_explicit_patch_equivalence(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    feature_sha, feature_ref = prepare_master_based_cherry_pick(repo)
    init_intent(repo, source_ref=feature_sha, merge_mode="cherry-pick -x")
    code, payload = guard("check-pre", "--cwd", str(repo))
    assert code == 0, payload
    run(["git", "cherry-pick", "-x", feature_sha], repo)
    code, payload = guard(
        "record-evidence",
        "--cwd",
        str(repo),
        "--test",
        "focused tests passed",
        "--review",
        "reviewed; no P1-now",
        "--subagents-closed",
    )
    assert code == 0, payload
    code, payload = guard(
        "record-lineage",
        "--cwd",
        str(repo),
        "--validated-ref",
        feature_sha,
        "--source-ref",
        feature_ref,
    )
    assert code == 0, payload

    code, payload = guard("check-post", "--cwd", str(repo))

    assert code == 1
    assert "final merge result does not include declared source_ref" in payload["errors"]
    assert "validated_ref is not included in final merge result" in payload["errors"]
    assert "record lineage with --allow-patch-equivalent" in payload["next_action"]


def test_check_post_blocks_when_declared_source_not_in_result(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    base_branch = current_branch(repo)
    run(["git", "checkout", "-b", "feature"], repo)
    commit_file(repo, "README.md", "feature\n", "feature change")
    run(["git", "checkout", base_branch], repo)
    init_intent(repo, "feature")
    commit_file(repo, "README.md", "unrelated\n", "unrelated integration")

    code, payload = guard(
        "check-post",
        "--cwd",
        str(repo),
        "--assistant-message",
        "tests: passed; review: no P1-now; subagent_status=not_used",
    )

    assert code == 1
    assert "final merge result does not include declared source_ref" in payload["errors"]
    assert payload["lineage"]["source_ref"] == "feature"


def test_check_lineage_blocks_validated_ref_not_returned_to_feature(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    run(["git", "checkout", "-b", "feature"], repo)
    commit_file(repo, "README.md", "feature\n", "feature change")
    run(["git", "checkout", "-b", "pre-validated"], repo)
    commit_file(repo, "README.md", "feature\npre hotfix\n", "pre hotfix")
    run(["git", "checkout", "feature"], repo)
    init_intent(repo, "feature")
    guard("record-lineage", "--cwd", str(repo), "--validated-ref", "pre-validated", "--source-ref", "feature")

    code, payload = guard("check-lineage", "--cwd", str(repo), "--require-validated")

    assert code == 1
    assert "validated_ref contains commits not present in source_ref; return fixes to the feature branch" in payload["errors"]
    assert payload["lineage"]["validated_ref"] == "pre-validated"


def test_prepare_protected_sync_freezes_checked_head_to_master_push(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    base_branch = current_branch(repo)
    run(["git", "branch", "-m", base_branch, "master"], repo)
    run(["git", "checkout", "-b", "feature"], repo)
    commit_file(repo, "README.md", "base\nfeature\n", "feature change")
    run(["git", "checkout", "-b", "integration", "master"], repo)
    run(["git", "merge", "--no-ff", "feature", "-m", "merge feature"], repo)

    code, payload = guard(
        "prepare-protected-sync",
        "--cwd",
        str(repo),
        "--target-ref",
        "master",
        "--source-ref",
        "HEAD",
        "--validated-ref",
        "feature",
        "--test",
        "focused tests passed",
        "--review",
        "reviewed; no P1-now",
        "--push-auth",
        "sync checked result to master only; no deploy or restart",
    )

    assert code == 0, payload
    assert payload["push_command"] == "git push origin HEAD:master"
    assert payload["forbidden_actions"] == ["deploy", "restart", "force-push", "server-mutation"]
    state = json.loads((repo / ".codex-local" / "merge-safety" / "current.json").read_text(encoding="utf-8"))
    assert state["operation"] == "push"
    assert state["target_ref"] == "master"
    assert state["source_ref"] == "HEAD"
    assert state["validated_ref"] == "feature"
    assert state["last_post_check"]["ok"] is True
    assert state["lineage_proof"]["validated_in_result"]["ok"] is True


def test_prepare_protected_sync_rejects_deploy_or_restart_scope(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)

    code, payload = guard(
        "prepare-protected-sync",
        "--cwd",
        str(repo),
        "--target-ref",
        "master",
        "--deploy",
    )

    assert code == 1
    assert "does not authorize deploy or restart" in payload["message"]


def test_prepare_protected_sync_rejects_unresolved_refs(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)

    code, payload = guard(
        "prepare-protected-sync",
        "--cwd",
        str(repo),
        "--target-ref",
        "missing-master",
        "--source-ref",
        "HEAD",
    )

    assert code == 1
    assert payload["message"] == "protected sync refs must resolve"
    assert "target_ref missing-master" in payload["missing_refs"]


def test_hotfix_like_intent_requires_explicit_user_flag(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    code, payload = guard(
        "init",
        "--cwd",
        str(repo),
        "--goal",
        "hotfix directly on pre",
        "--target-ref",
        "HEAD",
        "--source-ref",
        "HEAD",
        "--merge-mode",
        "merge --no-commit",
        "--allowed-paths",
        "README.md",
        "--acceptance",
        "tests",
    )
    assert code == 0, payload

    code, payload = guard("check-pre", "--cwd", str(repo))

    assert code == 1
    assert "hotfix-like intent requires explicit user authorization" in payload["errors"]


def test_hotfix_like_intent_allowed_with_user_flag(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    code, payload = guard(
        "init",
        "--cwd",
        str(repo),
        "--goal",
        "hotfix directly on pre",
        "--target-ref",
        "HEAD",
        "--source-ref",
        "HEAD",
        "--merge-mode",
        "merge --no-commit",
        "--allowed-paths",
        "README.md",
        "--acceptance",
        "tests",
        "--hotfix-requested-by-user",
    )
    assert code == 0, payload

    code, payload = guard("check-pre", "--cwd", str(repo))

    assert code == 0, payload
    state = json.loads((repo / ".codex-local" / "merge-safety" / "current.json").read_text(encoding="utf-8"))
    assert state["hotfix_requested_by_user"] is True
    assert state["intent_origin"] == "hotfix-requested-by-user"


def test_record_evidence_populates_post_gate_evidence(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    init_intent(repo)
    commit_file(repo, "README.md", "integrated\n", "integrated")

    code, payload = guard(
        "record-evidence",
        "--cwd",
        str(repo),
        "--test",
        "pytest focused passed",
        "--review",
        "reviewed; no P1-now",
        "--subagents-closed",
    )
    assert code == 0, payload
    code, payload = guard("check-post", "--cwd", str(repo))

    assert code == 0, payload


def test_hook_pretool_blocks_plain_edit_after_merge_started(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    init_intent(repo)
    state = repo / ".codex-local" / "merge-safety" / "current.json"
    data = json.loads(state.read_text(encoding="utf-8"))
    data["merge_started"] = True
    state.write_text(json.dumps(data), encoding="utf-8")

    payload = {
        "cwd": str(repo),
        "tool_name": "functions.apply_patch",
        "tool_input": {"patch": "*** Begin Patch\n*** Update File: README.md\n@@\n-base\n+patched\n*** End Patch\n"},
    }
    code, result = guard("hook-pretool", input_payload=payload)

    assert code == 0
    assert result["should_block"] is True
    assert "code edits are blocked in an integration worktree after merge has started" in result["message"]


class MergeGuardTests(unittest.TestCase):
    def run_tmp_test(self, func):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            func(Path(td))

    def test_tool_state_only_dirty_is_ignored_for_precheck(self):
        self.run_tmp_test(test_tool_state_only_dirty_is_ignored_for_precheck)

    def test_code_dirty_blocks_precheck_with_classification(self):
        self.run_tmp_test(test_code_dirty_blocks_precheck_with_classification)

    def test_hook_pretool_uses_tool_workdir_instead_of_payload_cwd(self):
        self.run_tmp_test(test_hook_pretool_uses_tool_workdir_instead_of_payload_cwd)

    def test_hook_pretool_uses_git_dash_c_root(self):
        self.run_tmp_test(test_hook_pretool_uses_git_dash_c_root)

    def test_hook_pretool_uses_quoted_git_dash_c_root_with_spaces(self):
        self.run_tmp_test(test_hook_pretool_uses_quoted_git_dash_c_root_with_spaces)

    def test_hook_pretool_blocks_git_apply_during_active_merge_safety(self):
        self.run_tmp_test(test_hook_pretool_blocks_git_apply_during_active_merge_safety)

    def test_status_classifies_tool_state_only(self):
        self.run_tmp_test(test_status_classifies_tool_state_only)

    def test_plan_clean_repo_recommends_init_and_check_pre(self):
        self.run_tmp_test(test_plan_clean_repo_recommends_init_and_check_pre)

    def test_plan_recommended_commands_use_current_python_and_quote_space_paths(self):
        self.run_tmp_test(test_plan_recommended_commands_use_current_python_and_quote_space_paths)

    def test_plan_dirty_repo_blocks_intent_initialization(self):
        self.run_tmp_test(test_plan_dirty_repo_blocks_intent_initialization)

    def test_plan_missing_fields_asks_once_for_intent(self):
        self.run_tmp_test(test_plan_missing_fields_asks_once_for_intent)

    def test_plan_active_intent_allows_mutating_command_after_precheck(self):
        self.run_tmp_test(test_plan_active_intent_allows_mutating_command_after_precheck)

    def test_conflict_marker_detection_ignores_plain_separator_lines(self):
        self.run_tmp_test(test_conflict_marker_detection_ignores_plain_separator_lines)

    def test_codex_local_handoff_notes_are_ignored_for_precheck(self):
        self.run_tmp_test(test_codex_local_handoff_notes_are_ignored_for_precheck)

    def test_hook_pretool_uses_leading_cd_root(self):
        self.run_tmp_test(test_hook_pretool_uses_leading_cd_root)

    def test_hook_pretool_uses_quoted_leading_cd_root_with_spaces(self):
        self.run_tmp_test(test_hook_pretool_uses_quoted_leading_cd_root_with_spaces)

    def test_hook_pretool_blocks_git_pull(self):
        self.run_tmp_test(test_hook_pretool_blocks_git_pull)

    def test_hook_pretool_blocks_guard_setup_and_merge_in_one_command(self):
        self.run_tmp_test(test_hook_pretool_blocks_guard_setup_and_merge_in_one_command)

    def test_hook_pretool_blocks_whole_file_resolution(self):
        self.run_tmp_test(test_hook_pretool_blocks_whole_file_resolution)

    def test_hook_pretool_blocks_stale_intent_for_merge(self):
        self.run_tmp_test(test_hook_pretool_blocks_stale_intent_for_merge)

    def test_hook_stop_uses_transcript_merge_workdir(self):
        self.run_tmp_test(test_hook_stop_uses_transcript_merge_workdir)

    def test_hook_stop_uses_transcript_git_dash_c_root(self):
        self.run_tmp_test(test_hook_stop_uses_transcript_git_dash_c_root)

    def test_hook_stop_uses_transcript_leading_cd_root(self):
        self.run_tmp_test(test_hook_stop_uses_transcript_leading_cd_root)

    def test_hook_stop_blocks_invalid_active_state(self):
        self.run_tmp_test(test_hook_stop_blocks_invalid_active_state)

    def test_hook_stop_postcheck_exception_fails_closed(self):
        self.run_tmp_test(test_hook_stop_postcheck_exception_fails_closed)

    def test_check_post_includes_next_action_for_missing_evidence(self):
        self.run_tmp_test(test_check_post_includes_next_action_for_missing_evidence)

    def test_report_includes_next_action_for_allowed_path_mismatch(self):
        self.run_tmp_test(test_report_includes_next_action_for_allowed_path_mismatch)

    def test_check_pre_cherry_pick_uses_exact_commit_files_not_branch_history(self):
        self.run_tmp_test(test_check_pre_cherry_pick_uses_exact_commit_files_not_branch_history)

    def test_check_post_allows_narrow_cherry_pick_with_explicit_patch_equivalent_lineage(self):
        self.run_tmp_test(test_check_post_allows_narrow_cherry_pick_with_explicit_patch_equivalent_lineage)

    def test_check_post_blocks_broad_source_cherry_pick_without_explicit_patch_equivalence(self):
        self.run_tmp_test(test_check_post_blocks_broad_source_cherry_pick_without_explicit_patch_equivalence)

    def test_check_post_blocks_when_declared_source_not_in_result(self):
        self.run_tmp_test(test_check_post_blocks_when_declared_source_not_in_result)

    def test_check_lineage_blocks_validated_ref_not_returned_to_feature(self):
        self.run_tmp_test(test_check_lineage_blocks_validated_ref_not_returned_to_feature)

    def test_hotfix_like_intent_requires_explicit_user_flag(self):
        self.run_tmp_test(test_hotfix_like_intent_requires_explicit_user_flag)

    def test_hotfix_like_intent_allowed_with_user_flag(self):
        self.run_tmp_test(test_hotfix_like_intent_allowed_with_user_flag)

    def test_record_evidence_populates_post_gate_evidence(self):
        self.run_tmp_test(test_record_evidence_populates_post_gate_evidence)

    def test_hook_pretool_blocks_plain_edit_after_merge_started(self):
        self.run_tmp_test(test_hook_pretool_blocks_plain_edit_after_merge_started)


if __name__ == "__main__":
    unittest.main()
