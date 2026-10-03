#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import posixpath
import re
import shlex
import subprocess
import sys
import os
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
SESSION_CONTEXT_DIR = Path(
    os.environ.get("CODEX_SESSION_CONTEXT_DIR", "/Users/a123/.codex/hooks/logs/session_context")
)
DEFAULT_PROTECTED_REFS = ("pre", "test", "master", "main")
DEFAULT_ENVIRONMENT_ALIASES = {
    "prod": ("prod", "production", "正式", "生产", "线上"),
    "pre": ("pre", "staging", "预发", "预发布"),
    "test": ("test", "testing", "测试"),
}
DEFAULT_ACTION_ALIASES = {
    "push": ("push", "推送", "推上去", "推到", "推倒"),
    "deploy": ("deploy", "部署", "发布", "上线", "切换到", "切换版本"),
    "restart": ("restart", "restart-service", "重启", "重启服务"),
    "rollback": ("rollback", "回滚"),
}
RISK_CANDIDATE_RE = re.compile(
    r"(?is)(?:\bgit\b[^\n;|&]*\b(?:merge|rebase|cherry-pick|pull|push)\b|\bgh\s+pr\s+merge\b|\bssh\s+\S+|\bprodctl\b|\b(?:scp|rsync)\b)"
)
SHELL_WRAPPER_NAMES = {"bash", "dash", "ksh", "sh", "zsh"}
PYTHON_WRAPPER_RE = re.compile(r"^python(?:\d+(?:\.\d+)?)?$")
REMOTE_FS_ACTIONS = {"touch", "remove", "edit", "mkdir", "copy", "move", "chmod", "chown", "symlink", "install"}


@dataclass(frozen=True)
class AuthorizationContext:
    current_user_text: str = ""
    adjacent_assistant_text: str = ""


def _read_payload() -> dict[str, Any]:
    if sys.stdin.isatty():
        return {}
    text = sys.stdin.read()
    if not text.strip():
        return {}
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def _tool_name(payload: dict[str, Any]) -> str:
    raw = payload.get("tool_name")
    if not isinstance(raw, str):
        return ""
    return raw.rsplit(".", 1)[-1].rsplit("__", 1)[-1].strip()


def _payload_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if value.get("type") in {"input_text", "output_text", "text"} and isinstance(value.get("text"), str):
            return value["text"]
        return "\n".join(_payload_text(item) for item in value.values())
    if isinstance(value, list):
        return "\n".join(_payload_text(item) for item in value)
    return "" if value is None else str(value)


def _command(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input", {})
    if isinstance(tool_input, dict):
        value = tool_input.get("cmd") or tool_input.get("command")
        if isinstance(value, str):
            return value
    return _payload_text(tool_input)


def _allow() -> int:
    return 0


def _block(reason: str, **extra: Any) -> int:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


class MergeGuardNotFound(RuntimeError):
    def __init__(self, checked_paths: list[Path]) -> None:
        self.checked_paths = checked_paths
        super().__init__("merge_guard.py helper not found")


def _merge_guard_candidates() -> list[Path]:
    relative = Path("skills") / "merge-safety" / "scripts" / "merge_guard.py"
    candidates: list[Path] = [ROOT / relative]
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        codex_root = Path(codex_home).expanduser()
        candidates.append(codex_root / relative)
        candidates.append(codex_root.parent / ".agents" / relative)
    agents_home = os.environ.get("AGENTS_HOME")
    if agents_home:
        candidates.append(Path(agents_home).expanduser() / relative)
    home = os.environ.get("HOME")
    if home:
        candidates.append(Path(home).expanduser() / ".agents" / relative)

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.expanduser()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(resolved)
    return deduped


def _load_merge_guard() -> Any:
    checked = _merge_guard_candidates()
    path = next((candidate for candidate in checked if candidate.is_file()), None)
    if path is None:
        raise MergeGuardNotFound(checked)
    spec = importlib.util.spec_from_file_location("merge_guard_for_critical_gate", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load merge_guard.py from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_policy(payload: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    errors: list[str] = []
    codex_home = os.environ.get("CODEX_HOME") or str(Path(os.environ.get("HOME", "")).expanduser() / ".codex")
    policy_dir = Path(codex_home).expanduser() / "critical-action-policies"
    if policy_dir.is_dir():
        for item in sorted(policy_dir.glob("*.json")):
            try:
                data = json.loads(item.read_text(encoding="utf-8-sig"))
            except Exception as exc:
                errors.append(f"{item}: {exc}")
                continue
            if isinstance(data, dict):
                merged = _merge_policy(merged, data)
            else:
                errors.append(f"{item}: policy must be a JSON object")
    path = payload.get("policy_path")
    if isinstance(path, str) and path.strip():
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                merged = _merge_policy(merged, data)
            else:
                errors.append(f"{path}: policy must be a JSON object")
        except Exception as exc:
            errors.append(f"{path}: {exc}")
    if errors:
        merged["_policy_errors"] = errors
    return merged


def _merge_policy(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    refs = list(result.get("protected_refs") or [])
    refs.extend(item for item in overlay.get("protected_refs") or [] if item not in refs)
    if refs:
        result["protected_refs"] = refs
    server = dict(result.get("server_policy") or {})
    overlay_server = overlay.get("server_policy") if isinstance(overlay.get("server_policy"), dict) else {}
    if isinstance(overlay.get("hosts"), dict):
        overlay_server = {**overlay_server, "hosts": {**(overlay_server.get("hosts") or {}), **overlay["hosts"]}}
    semantic_aliases = overlay.get("semantic_aliases") if isinstance(overlay.get("semantic_aliases"), dict) else {}
    env_aliases = semantic_aliases.get("env") or semantic_aliases.get("environment")
    action_aliases = semantic_aliases.get("action") or semantic_aliases.get("actions")
    if isinstance(env_aliases, dict):
        overlay_server = {**overlay_server, "environment_aliases": {**(overlay_server.get("environment_aliases") or {}), **env_aliases}}
    if isinstance(action_aliases, dict):
        overlay_server = {**overlay_server, "action_aliases": {**(overlay_server.get("action_aliases") or {}), **action_aliases}}
    if isinstance(overlay.get("guard_defaults"), dict):
        server["guard_defaults"] = {**(server.get("guard_defaults") or {}), **overlay["guard_defaults"]}
    hosts = dict(server.get("hosts") or {})
    hosts.update(overlay_server.get("hosts") or {})
    if hosts:
        server["hosts"] = hosts
    environment_aliases = dict(server.get("environment_aliases") or {})
    environment_aliases.update(overlay_server.get("environment_aliases") or {})
    if environment_aliases:
        server["environment_aliases"] = environment_aliases
    action_aliases = dict(server.get("action_aliases") or {})
    action_aliases.update(overlay_server.get("action_aliases") or {})
    if action_aliases:
        server["action_aliases"] = action_aliases
    prodctl = dict(server.get("prodctl") or {})
    prodctl.update(overlay_server.get("prodctl") or {})
    if prodctl:
        server["prodctl"] = prodctl
    if server:
        result["server_policy"] = server
    return result


def _server_section(policy: dict[str, Any]) -> dict[str, Any]:
    server = policy.get("server_policy") if isinstance(policy.get("server_policy"), dict) else {}
    return server


def _policy_errors(policy: dict[str, Any]) -> list[str]:
    errors = policy.get("_policy_errors")
    return [str(item) for item in errors] if isinstance(errors, list) else []


def _policy_has_hosts(policy: dict[str, Any]) -> bool:
    hosts = _server_section(policy).get("hosts")
    return isinstance(hosts, dict) and bool(hosts)


def _aliases(policy: dict[str, Any], kind: str, canonical: str) -> list[str]:
    server = _server_section(policy)
    key = "environment_aliases" if kind == "env" else "action_aliases"
    data = server.get(key)
    defaults = DEFAULT_ENVIRONMENT_ALIASES if kind == "env" else DEFAULT_ACTION_ALIASES
    aliases = [canonical, *defaults.get(canonical, ())]
    if isinstance(data, dict):
        raw = data.get(canonical)
        if isinstance(raw, list):
            aliases.extend(str(item) for item in raw)
        elif isinstance(raw, str):
            aliases.append(raw)
    return list(dict.fromkeys(alias.lower() for alias in aliases if alias))


def _text_has_alias(text: str, aliases: list[str]) -> bool:
    lower = text.lower()
    for alias in aliases:
        clean = alias.lower().strip()
        if not clean:
            continue
        if re.search(r"[A-Za-z0-9]", clean):
            if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(clean)}(?![A-Za-z0-9_-])", lower):
                return True
        elif clean in lower:
            return True
    return False


def _protected_patterns(policy: dict[str, Any]) -> list[str]:
    refs = policy.get("protected_refs")
    if isinstance(refs, list) and refs:
        return [str(ref).strip() for ref in refs if str(ref).strip()]
    return list(DEFAULT_PROTECTED_REFS)


def _ref_name(ref: str) -> str:
    value = str(ref or "").strip()
    if value.startswith("refs/heads/"):
        value = value.removeprefix("refs/heads/")
    elif value.startswith("refs/remotes/"):
        value = value.removeprefix("refs/remotes/")
        parts = value.split("/", 1)
        value = parts[1] if len(parts) == 2 else value
    elif "/" in value:
        remote, branch = value.split("/", 1)
        if remote in {"origin", "upstream"}:
            value = branch
    return value


def _is_protected_ref(ref: str, patterns: list[str]) -> bool:
    name = _ref_name(ref)
    if not name:
        return False
    return any(name == pattern or fnmatch(name, pattern) for pattern in patterns)


def _resolve_cwd(path: str | None, base: str | None = None) -> str | None:
    if not path or not str(path).strip():
        return None
    raw = Path(str(path).strip()).expanduser()
    if not raw.is_absolute() and base:
        raw = Path(base).expanduser() / raw
    return str(raw.resolve())


def _payload_tool_workdir(payload: dict[str, Any]) -> str | None:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    for key in ("workdir", "cwd"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _payload_git_base_cwd(payload: dict[str, Any]) -> str | None:
    payload_cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    tool_workdir = _resolve_cwd(_payload_tool_workdir(payload), payload_cwd)
    return tool_workdir or _resolve_cwd(payload_cwd)


def _git_invocation_cwd(tokens: list[str], base_cwd: str | None) -> str | None:
    if not tokens or Path(tokens[0]).name != "git":
        return None
    index = 1
    effective = base_cwd
    while index < len(tokens):
        token = tokens[index]
        if token == "-c" and index + 1 < len(tokens):
            index += 2
            continue
        if token == "-C" and index + 1 < len(tokens):
            effective = _resolve_cwd(tokens[index + 1], effective or base_cwd)
            index += 2
            continue
        if token == "--work-tree" and index + 1 < len(tokens):
            effective = _resolve_cwd(tokens[index + 1], base_cwd)
            index += 2
            continue
        if token == "--git-dir" and index + 1 < len(tokens):
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return effective


def _git_repo_root(cwd: str | None) -> str | None:
    if not cwd:
        return None
    result = subprocess.run(
        ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _git_current_branch(cwd: str | None) -> str:
    if not cwd:
        return ""
    result = subprocess.run(
        ["git", "-C", cwd, "symbolic-ref", "--quiet", "--short", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_config_values(cwd: str | None, key: str) -> list[str]:
    if not cwd:
        return []
    result = subprocess.run(
        ["git", "-C", cwd, "config", "--get-all", key],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode not in {0, 1}:
        return ["<unreadable>"]
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _git_commands(merge_guard: Any, command: str, base_cwd: str | None = None) -> list[tuple[str, list[str], str | None]]:
    parsed: list[tuple[str, list[str], str | None]] = []
    for statement in _candidate_statements(command, merge_guard):
        # Candidate discovery includes both the full shell expression and its
        # atomic statements. Only parse the latter as Git argv; otherwise a
        # later command becomes a spurious push refspec.
        if len(merge_guard._shell_statements(statement)) > 1:
            continue
        try:
            tokens = shlex.split(statement, posix=True)
        except ValueError:
            continue
        tokens = _strip_transparent_prefixes(tokens)
        invocation_cwd = _git_invocation_cwd(tokens, base_cwd) or base_cwd
        if tokens:
            tokens[0] = Path(tokens[0]).name
        try:
            subcommand, args = merge_guard._git_subcommand(tokens)
        except Exception:
            continue
        if subcommand:
            parsed.append((subcommand, args, invocation_cwd))
    return parsed


def _load_transcript_context(path: str | None) -> AuthorizationContext:
    if not path:
        return AuthorizationContext()
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return AuthorizationContext()
    items: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            items.append(item)
    last_user = -1
    for index, item in enumerate(items):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if payload.get("type") == "message" and payload.get("role") == "user":
            text = str(payload.get("content", ""))
            if "<hook_prompt" not in text:
                last_user = index
    if last_user < 0:
        return AuthorizationContext()
    payload = items[last_user].get("payload") if isinstance(items[last_user].get("payload"), dict) else {}
    current_user_text = _payload_text(payload.get("content", "")).lower()
    adjacent_assistant_text = ""
    for item in reversed(items[:last_user]):
        candidate = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if candidate.get("type") != "message":
            continue
        if candidate.get("role") == "user" and "<hook_prompt" not in str(candidate.get("content", "")):
            break
        if candidate.get("role") == "assistant":
            adjacent_assistant_text = _payload_text(candidate.get("content", "")).lower()
            break
    return AuthorizationContext(current_user_text, adjacent_assistant_text)


def _load_session_context(payload: dict[str, Any]) -> AuthorizationContext:
    session_id = ""
    for key in ("session_id", "thread_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            session_id = value.strip()
            break
    session_id = session_id or os.environ.get("CODEX_SESSION_ID", "").strip()
    if not session_id:
        return AuthorizationContext()
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)
    path = SESSION_CONTEXT_DIR / f"{safe_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AuthorizationContext()
    if data.get("session_id") != session_id:
        return AuthorizationContext()
    return AuthorizationContext(
        str(data.get("user_text") or "").lower(),
        str(data.get("assistant_text") or "").lower(),
    )


def _load_transcript_text(path: str | None) -> str:
    return _load_transcript_context(path).current_user_text


def _transcript_text(payload: dict[str, Any]) -> str:
    path = payload.get("transcript_path")
    return _load_transcript_text(path if isinstance(path, str) else None)


def _transcript_context(payload: dict[str, Any]) -> AuthorizationContext:
    path = payload.get("transcript_path")
    if isinstance(path, str) and path:
        context = _load_transcript_context(path)
        if context.current_user_text:
            return context
    return _load_session_context(payload)


def _is_positive_plan_approval(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if not normalized:
        return False
    if re.search(r"(?:^|[\s，。；;,.])(?:if\b|如果|若|假如|倘若)", normalized):
        return False
    if any(marker in normalized for marker in ("?", "？", "是否", "能否", "可否", "吗", "如何", "怎么")):
        return False
    negative = (
        "不允许",
        "不要",
        "不能",
        "不可",
        "先别",
        "别",
        "暂停",
        "取消",
        "别执行",
        "do not",
        "don't",
        "not approved",
        "stop",
        "cancel",
    )
    if any(marker in normalized for marker in negative):
        return False
    if re.fullmatch(r"(?:可以|ok(?:ay)?)[\s，,]*开始(?:吧)?[。.!！]?", normalized):
        return True
    if re.search(r"\b(?:no|without)\s+(?:push|deploy|deployment|restart|rollback|release|write)\b", normalized):
        return False
    if re.search(r"\b(?:approve|approved|authorize|authorized|proceed|yes)\b", normalized):
        return True
    if any(marker in normalized for marker in ("允许", "同意", "批准", "授权")):
        return True
    if re.search(r"合(?:并到|入)\s*(?:origin/)?(?:master|main)\b", normalized):
        return True
    if re.search(r"(?:\bok(?:ay)?\b|可以|确认|好的?|没问题).{0,20}(?:按|按照).{0,20}(?:推进|执行|继续)", normalized):
        return True
    if re.search(r"(?:按|按照)\s*(?:上述|这个|该|当前)?\s*规划\s*(?:推进|执行|继续)(?:吧|了)?$", normalized):
        return True
    if re.search(r"(?:可以|确认|好的?|没问题|继续).{0,16}(?:按|执行|继续|重启|部署|发布|回滚)", normalized):
        return True
    if re.search(r"(?:上|推到|部署到|发布到)\s*(?:预发|预发布|pre|测试|test)(?:环境)?\s*(?:验证|测试|跑|吧|了)?$", normalized):
        return True
    if re.search(r"(?:推送|推上去|推到|推倒|推|部署|发布|重启|执行|继续)(?:吧|了)?$", normalized):
        return True
    return normalized.strip(" ，。！？,.!?") in {"可以", "确认", "好的", "好", "没问题", "继续", "执行"}


def _positive_plan_clauses(text: str) -> list[str]:
    """Keep affirmative clause prefixes while removing explicit exclusions."""
    negative = re.compile(
        r"(?:不要|不能|不可|禁止|先别|别|无需|不使用|不执行|不重启|不部署|不发布|不回滚|不推送|不推)"
        r"|\b(?:do not|don't|must not|never|without)\b",
        re.IGNORECASE,
    )
    positive: list[str] = []
    for raw_clause in re.split(r"[\n,，;；。]+", text):
        clause = raw_clause.strip()
        if not clause:
            continue
        match = negative.search(clause)
        if match:
            clause = re.sub(r"(?:\bbut\b|但|但是)\s*$", "", clause[: match.start()].strip(), flags=re.IGNORECASE)
        if clause and clause.rstrip(":：").strip().lower() not in {"plan", "计划"}:
            positive.append(clause)
    return positive


def _positive_plan_text(text: str) -> str:
    return "\n".join(_positive_plan_clauses(text))


def _explicit_plan_refs(text: str) -> list[str]:
    ref_token = r"([A-Za-z0-9][A-Za-z0-9._/-]{0,254})"
    refish_token = r"([A-Za-z0-9._-]+/[A-Za-z0-9._/-]+|[0-9a-fA-F]{7,40})"
    patterns = (
        rf"(?:^|\s)--ref(?:=|\s+){ref_token}(?=$|\s)",
        rf"按正常\s*`?{ref_token}`?\s*路径同步",
        rf"(?:commit|sha|提交|版本)\s*(?:为|是|:|：|=)?\s*{ref_token}",
        rf"(?:from|source)\s+(?:branch|ref)\s+{ref_token}",
        rf"(?:deploy|push)\s+(?:the\s+)?(?:branch|ref|commit)\s+{ref_token}",
        rf"(?:将|从)\s*{refish_token}\s*(?:部署|发布|推送|推到|推倒)",
        r"(?<![A-Za-z0-9])([0-9a-fA-F]{7,40})(?![A-Za-z0-9])",
    )
    refs: list[str] = []
    for pattern in patterns:
        refs.extend(match.group(1).lower() for match in re.finditer(pattern, text, re.IGNORECASE))
    return list(dict.fromkeys(refs))


def _ref_matches(actual: str, expected: str) -> bool:
    left = actual.lower().strip()
    right = expected.lower().strip()
    if re.fullmatch(r"[0-9a-f]{7,40}", left) and re.fullmatch(r"[0-9a-f]{7,40}", right):
        return left.startswith(right) or right.startswith(left)

    def normalize(value: str) -> str:
        value = value.removeprefix("refs/heads/")
        if value.startswith("refs/remotes/"):
            value = value.removeprefix("refs/remotes/").split("/", 1)[-1]
        if value.startswith(("origin/", "upstream/")):
            value = value.split("/", 1)[1]
        return value

    return bool(left and right) and normalize(left) == normalize(right)


def _is_exact_hex_ref(ref: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", ref.strip()))


def _prod_detached_ref_authorized(text: str, ref: str) -> bool:
    """Require explicit exception language for prod deploys pinned to a commit.

    The normal production release lane should deploy the shared branch state.
    A commit-SHA deploy is still possible, but it must be named as an
    emergency/pinned/detached exception by the current user text, not merely by
    an assistant-generated plan.
    """
    if not _is_exact_hex_ref(ref):
        return True
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    exception_markers = (
        "detached",
        "pinned",
        "pin sha",
        "pinned sha",
        "commit hash",
        "指定 commit",
        "指定 sha",
        "固定 sha",
        "固定提交",
        "精确 commit",
        "精确 sha",
        "直接 checkout",
        "直接切",
        "不走 master",
        "未合入 master",
        "脱离 master",
        "应急",
        "紧急",
        "例外",
    )
    if not any(marker in normalized for marker in exception_markers):
        return False
    explicit_refs = _explicit_plan_refs(text)
    return any(_ref_matches(ref, expected) for expected in explicit_refs)


def _git_resolve_ref(cwd: str | None, ref: str) -> str:
    if not cwd or not ref:
        return ref
    result = subprocess.run(
        ["git", "-C", cwd, "rev-parse", ref],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return result.stdout.strip() if result.returncode == 0 else ref


def _mentioned_server_scopes(policy: dict[str, Any], text: str) -> tuple[set[str], set[str]]:
    environment_text = re.sub(r"\b(?:origin|upstream)/[A-Za-z0-9._/-]+", " ", text, flags=re.IGNORECASE)
    environment_text = re.sub(r"\brefs/(?:heads|remotes)/[A-Za-z0-9._/-]+", " ", environment_text, flags=re.IGNORECASE)
    # A plan often records excluded environments alongside the selected one.
    # Do not turn "不会操作 test/pre" into a multi-environment authorization.
    environment_text = re.sub(
        r"(?:不会|不|未)\s*(?:操作|触碰|涉及|改动)\s*[^\n。；;]*",
        " ",
        environment_text,
        flags=re.IGNORECASE,
    )
    # A completed validation result is not a request targeting the test fleet.
    # Keep explicit '测试环境' and all other test mentions untouched.
    environment_text = re.sub(
        r"(^|[。；;\n])\s*(?:工具修复)?已通过测试(?:和复审)?(?=[，,。；;\n]|$)",
        r"\1已完成验证", environment_text,
    )
    environments = {
        canonical
        for canonical in DEFAULT_ENVIRONMENT_ALIASES
        if _text_has_alias(environment_text, _aliases(policy, "env", canonical))
    }
    actions = {
        canonical
        for canonical in DEFAULT_ACTION_ALIASES
        if _text_has_alias(text, _aliases(policy, "action", canonical))
    }
    return environments, actions


def _mentioned_server_hosts(text: str) -> set[str]:
    hosts: set[str] = set()
    patterns = (
        r"(?:^|\s)--host(?:=|\s+)([A-Za-z0-9_.-]+)",
        r"(?:^|[;&|]\s*|commands?:\s*)ssh\s+([A-Za-z0-9_.@-]+)",
    )
    for pattern in patterns:
        hosts.update(match.group(1).split("@")[-1] for match in re.finditer(pattern, text, re.IGNORECASE))
    return hosts


def _mentioned_policy_hosts(policy: dict[str, Any], text: str) -> set[str]:
    """Resolve registered host names/aliases mentioned in a batch plan.

    Command parsing only sees ``--host``/SSH argv.  Production rollout plans
    commonly name a frozen host batch in prose (for example ``cn-sz1 → cn-sz8``).
    Resolve those names against the loaded policy so the execution gate and the
    planner use the same host vocabulary.
    """
    hosts = _server_section(policy).get("hosts")
    if not isinstance(hosts, dict):
        return set()
    found: set[str] = set()
    normalized = text.lower()
    for canonical, candidate in hosts.items():
        if not isinstance(candidate, dict):
            continue
        canonical_name = str(canonical)
        # ``pre host`` and ``test environment`` mention an environment, not a
        # host whose canonical key happens to be ``pre``/``test``.
        names = [] if canonical_name.lower() in DEFAULT_ENVIRONMENT_ALIASES else [canonical_name]
        names.extend(str(item) for item in candidate.get("aliases") or [])
        names.extend(str(item) for item in candidate.get("ssh_aliases") or [])
        if any(re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(name.lower())}(?![A-Za-z0-9_.-])", normalized) for name in names if name):
            found.add(str(canonical))
    return found


def _is_batch_rollout_plan(text: str, hosts: set[str]) -> bool:
    if len(hosts) < 2:
        return False
    normalized = text.lower()
    return any(marker in normalized for marker in ("按顺序", "依次", "逐台", "批量", "全部", "滚动", "→", "->"))


def _mentioned_server_services(text: str) -> set[str]:
    services: set[str] = set()
    patterns = (
        r"(?:^|\s)--service(?:=|\s+)([A-Za-z0-9_.@:-]+)",
        r"\bsystemctl\s+(?:restart|start|stop|reload)\s+([A-Za-z0-9_.@:-]+)",
        r"\bservice\s+([A-Za-z0-9_.@:-]+)\s+(?:restart|start|stop|reload)\b",
        r"\bsupervisorctl\s+(?:restart|start|stop|reload)\s+([A-Za-z0-9_.@:-]+)",
    )
    for pattern in patterns:
        services.update(match.group(1).lower() for match in re.finditer(pattern, text, re.IGNORECASE))
    return services


def _server_authorization_text(
    policy: dict[str, Any],
    context: AuthorizationContext,
    *,
    environment: str,
    actions: list[str],
    service: str = "",
    services: list[str] | None = None,
    host: str = "",
    transport: str = "",
    ref: str = "",
) -> str:
    current = context.current_user_text
    raw_plan = context.adjacent_assistant_text
    if not raw_plan or not _is_positive_plan_approval(current):
        return current
    plan = _positive_plan_text(raw_plan)
    if not plan:
        return current

    mentioned_envs, mentioned_actions = _mentioned_server_scopes(policy, plan)
    if not mentioned_envs and host:
        # A registered, uniquely named host already has a policy environment.
        # Do not require the user/plan to spell the environment a second time.
        named_hosts = {
            name: _server_policy(policy, name)
            for name in _server_section(policy).get("hosts", {})
            if _host_authorized(policy, name, _server_policy(policy, name), plan)
        }
        selected = _server_policy(policy, host)
        if len(named_hosts) == 1 and selected.get("_canonical_host") in named_hosts:
            declared_env = str(selected.get("env", ""))
            if declared_env in DEFAULT_ENVIRONMENT_ALIASES:
                mentioned_envs = {declared_env}
    expected_actions = {
        "deploy" if action == "git" else action
        for action in actions
        if action == "git" or action in DEFAULT_ACTION_ALIASES
    }
    if environment not in mentioned_envs or not expected_actions.issubset(mentioned_actions):
        return current
    if len(mentioned_envs) > 1 and not re.search(
        rf"--host(?:=|\s+)\S*{re.escape(environment)}\b",
        plan,
        re.IGNORECASE,
    ):
        return current

    mentioned_hosts = _mentioned_server_hosts(plan) | _mentioned_policy_hosts(policy, plan)
    batch_plan = _is_batch_rollout_plan(plan, mentioned_hosts)
    if mentioned_hosts:
        if len(mentioned_envs) > 1:
            if not host or host not in mentioned_hosts:
                return current
        elif not host or (mentioned_hosts != {host} and not (environment == "prod" and batch_plan and host in mentioned_hosts)):
            return current
    if host:
        host_policy = _server_policy(policy, host)
        if host_policy and not _host_authorized(policy, host, host_policy, plan):
            return current
    mentioned_services = _mentioned_server_services(plan)
    expected_services = {item.lower() for item in (services or ([service] if service else [])) if item}
    if mentioned_services and mentioned_services != expected_services:
        return current
    if any(item not in plan for item in expected_services):
        # For non-production validation, a single service on the selected host
        # is enough to bind the plan. Production keeps the stricter explicit
        # service requirement.
        host_services = [str(item).lower() for item in (host_policy.get("services") or [])] if host else []
        batch_service_allowed = batch_plan and expected_services.issubset(set(host_services))
        if not batch_service_allowed and (
            environment == "prod"
            or len(host_services) != 1
            or expected_services != set(host_services)
            or any(marker in plan.lower() for marker in ("only", "仅", "只", "不要", "不重启", "不部署"))
        ):
            return current

    transports = {name for name in ("prodctl", "ssh") if _text_has_alias(plan, [name])}
    if transports and transport not in transports:
        return current
    if ref:
        if (
            environment == "prod"
            and "deploy" in expected_actions
            and _is_exact_hex_ref(ref)
            and not _prod_detached_ref_authorized(current, ref)
        ):
            return current
        action_clauses = [
            clause
            for clause in _positive_plan_clauses(raw_plan)
            if any(_text_has_alias(clause, _aliases(policy, "action", action)) for action in expected_actions)
            or ("deploy" in expected_actions and "路径同步" in clause)
        ]
        explicit_refs = _explicit_plan_refs("\n".join(action_clauses))
        if explicit_refs and not any(_ref_matches(ref, expected) for expected in explicit_refs):
            return current
    return f"{current}\n{plan}"


def _git_push_authorization_text(
    policy: dict[str, Any],
    context: AuthorizationContext,
    *,
    protected_targets: list[str],
    sources: list[str],
    remote: str,
    cwd: str | None,
) -> str:
    current = context.current_user_text
    raw_plan = context.adjacent_assistant_text
    if not protected_targets or not raw_plan or not _is_positive_plan_approval(current):
        return current
    matching_clauses = [
        clause
        for clause in _positive_plan_clauses(raw_plan)
        if _text_has_alias(clause, _aliases(policy, "action", "push"))
        and all(_text_has_alias(clause, [target]) for target in protected_targets)
    ]
    if not matching_clauses:
        return current
    plan = "\n".join(matching_clauses)
    for target in protected_targets:
        mentioned_remotes = {
            match.group(1).lower()
            for match in re.finditer(rf"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]+)/{re.escape(target)}(?![A-Za-z0-9_.-])", plan)
        }
        if mentioned_remotes and remote.lower() not in mentioned_remotes:
            return current
    explicit_refs = _explicit_plan_refs(plan)
    source_candidates = [*sources, *[_git_resolve_ref(cwd, source) for source in sources]]
    expected_candidates = [*explicit_refs, *[_git_resolve_ref(cwd, expected) for expected in explicit_refs]]
    if explicit_refs and not any(
        _ref_matches(source, expected)
        for source in source_candidates
        for expected in expected_candidates
    ):
        return current
    return f"{current}\n{plan}"


def _state_path(cwd: str | None) -> Path | None:
    if not cwd:
        return None
    root = _git_repo_root(cwd) or cwd
    return Path(root) / ".codex-local" / "merge-safety" / "current.json"


def _load_state(cwd: str | None) -> dict[str, Any]:
    path = _state_path(cwd)
    if not path or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _state_ok(state: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value: Any = state
        for part in key.split("."):
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        if value is True:
            return True
    return False


def _precheck_ok(state: dict[str, Any]) -> bool:
    return _state_ok(state, "precheck.ok", "last_pre_check.ok")


def _evidence_ok(state: dict[str, Any], name: str) -> bool:
    evidence = state.get("evidence")
    if isinstance(evidence, dict):
        direct = evidence.get(f"{name}_ok")
        if direct is True:
            return True
        value = evidence.get(name)
        if value is True:
            return True
        if isinstance(value, dict) and (value.get("ok") is True or value.get("recorded_at")):
            return True
        if name == "lineage" and isinstance(evidence.get("lineage_check"), dict):
            return True
    if name == "validation":
        return bool(state.get("tests")) or _state_ok(state, "validation.ok")
    if name == "review":
        review = state.get("review")
        return bool(review) and review != {"status": "", "summary": "", "p1_now": []}
    if name == "lineage":
        return bool(state.get("lineage_proof")) or _state_ok(state, "lineage.ok")
    if name == "postcheck":
        return _state_ok(state, "postcheck.ok", "last_post_check.ok")
    return False


def _pretest_checked_result_source_ok(state: dict[str, Any], *, target: str, source: str, cwd: str | None) -> bool:
    if _ref_name(target) not in {"pre", "test"}:
        return False
    postcheck = state.get("last_post_check")
    if not isinstance(postcheck, dict) or postcheck.get("ok") is not True:
        return False
    recorded_head = str(postcheck.get("current_head") or "").strip()
    if not recorded_head or _git_resolve_ref(cwd, "HEAD") != recorded_head:
        return False
    if _git_resolve_ref(cwd, source) != recorded_head:
        return False
    lineage = postcheck.get("lineage")
    if not isinstance(lineage, dict):
        return False
    source_in_result = lineage.get("source_in_result")
    if not isinstance(source_in_result, dict) or source_in_result.get("ok") is not True:
        return False
    result_oid = str(source_in_result.get("result_oid") or "").strip()
    if not result_oid or result_oid != recorded_head:
        return False
    required_ref = str(source_in_result.get("required_ref") or "").strip()
    state_source = str(state.get("source_ref") or "").strip()
    if not required_ref or _ref_name(required_ref) != _ref_name(state_source):
        return False
    return True


def _matches_intent(state: dict[str, Any], *, target: str, source: str, operation: str, cwd: str | None = None) -> bool:
    if state.get("kind") != "merge_safety_intent":
        return False
    state_operation = str(state.get("operation") or "").strip()
    if not state_operation:
        return False
    state_target = _ref_name(str(state.get("target_ref") or ""))
    state_source = _ref_name(str(state.get("source_ref") or "").strip())
    if not state_target or not state_source:
        return False
    target_ok = state_target == _ref_name(target)
    source_ok = state_source == _ref_name(source) or _pretest_checked_result_source_ok(
        state,
        target=target,
        source=source,
        cwd=cwd,
    )
    operation_ok = state_operation == operation
    return target_ok and source_ok and operation_ok


def _protected_push_missing_requirements(
    state: dict[str, Any],
    *,
    target: str,
    source: str,
    cwd: str | None = None,
) -> list[str]:
    checked_result_source_ok = _pretest_checked_result_source_ok(state, target=target, source=source, cwd=cwd)
    missing = [
        label
        for label, key in (
            ("validation", "validation"),
            ("review", "review"),
            ("lineage", "lineage"),
            ("post-check", "postcheck"),
        )
        if not (key == "lineage" and checked_result_source_ok) and not _evidence_ok(state, key)
    ]
    if not _matches_intent(state, target=target, source=source, operation="push", cwd=cwd):
        missing.append("matching intent")
    return missing


def _target_from_transcript(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        if any(ch in pattern for ch in "*?[]"):
            continue
        if _text_has_alias(text, [pattern]):
            return pattern
    for fallback in DEFAULT_PROTECTED_REFS:
        if _text_has_alias(text, [fallback]):
            return fallback
    return ""


def _push_refspecs(args: list[str]) -> list[str]:
    refspecs: list[str] = []
    skip_next = False
    remote_seen = False
    delete_mode = False
    options_with_values = {"--repo", "--receive-pack", "--exec", "--push-option", "-o"}
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in options_with_values:
            skip_next = True
            continue
        if arg == "--delete":
            delete_mode = True
            continue
        if arg in {"--all", "--mirror"}:
            continue
        if arg.startswith("-"):
            continue
        if not remote_seen:
            remote_seen = True
            continue
        refspecs.append(f":{arg}" if delete_mode and ":" not in arg else arg)
    return refspecs


def _push_remote(args: list[str]) -> str:
    skip_next = False
    options_with_values = {"--repo", "--receive-pack", "--exec", "--push-option", "-o"}
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in options_with_values:
            skip_next = True
            continue
        if arg.startswith("--repo=") or arg.startswith("--receive-pack=") or arg.startswith("--exec="):
            continue
        if arg.startswith("--push-option="):
            continue
        if arg == "--delete":
            continue
        if arg in {"--all", "--mirror"}:
            continue
        if arg.startswith("-"):
            continue
        return arg
    return ""


def _push_targets(args: list[str]) -> list[str]:
    targets: list[str] = []
    for refspec in _push_refspecs(args):
        if ":" in refspec:
            target = refspec.rsplit(":", 1)[-1]
        else:
            target = refspec
        targets.append(_ref_name(target))
    return targets


def _push_sources(args: list[str]) -> list[str]:
    sources: list[str] = []
    for refspec in _push_refspecs(args):
        if ":" in refspec:
            source = refspec.split(":", 1)[0]
        else:
            source = refspec
        sources.append(source)
    return sources


def _has_push_delete(args: list[str]) -> bool:
    if "--delete" in args:
        return True
    return any(":" in refspec and not refspec.split(":", 1)[0] for refspec in _push_refspecs(args))


def _has_push_all_or_mirror(args: list[str]) -> str:
    for option in ("--mirror", "--all", "--tags", "--follow-tags", "--prune"):
        if option in args:
            return option
    return ""


def _push_config_hazard(args: list[str], *, cwd: str | None, refspecs: list[str]) -> str:
    remote = _push_remote(args)
    false_values = {"false", "no", "off", "0"}
    for key in ("push.followTags", f"remote.{remote}.mirror" if remote else ""):
        if key and any(value.lower() not in false_values for value in _git_config_values(cwd, key)):
            return key
    if refspecs:
        return ""
    if remote and _git_config_values(cwd, f"remote.{remote}.push"):
        return f"remote.{remote}.push"
    defaults = _git_config_values(cwd, "push.default")
    mode = defaults[-1].lower() if defaults else "simple"
    if mode not in {"simple", "current"}:
        return f"push.default={mode}"
    return ""


def _has_force(args: list[str]) -> bool:
    return any(arg == "--force" or arg == "-f" or arg.startswith("+") for arg in args)


def _has_force_with_lease(args: list[str]) -> bool:
    return any(arg == "--force-with-lease" or arg.startswith("--force-with-lease=") for arg in args)


def _is_ordinary_single_unprotected_push(
    args: list[str],
    *,
    cwd: str | None,
    patterns: list[str],
    refspecs: list[str],
    targets: list[str],
) -> bool:
    if _push_remote(args) != "origin":
        return False
    if _has_push_all_or_mirror(args) or _has_push_delete(args) or _has_force(args) or _has_force_with_lease(args):
        return False
    if len(refspecs) > 1:
        return False
    resolved_targets = list(targets)
    if not refspecs:
        current_branch = _git_current_branch(cwd)
        if not current_branch:
            return False
        resolved_targets = [current_branch]
    if len(resolved_targets) != 1:
        return False
    return not _is_protected_ref(resolved_targets[0], patterns)


def _auth_mentions(text: str, *needles: str) -> bool:
    return all(needle.lower() in text for needle in needles if needle)


def _text_has_word(text: str, word: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z0-9_-]){re.escape(word.lower())}(?![A-Za-z0-9_-])", text))


def _mentions_current_branch(text: str) -> bool:
    return "当前分支" in text or "current branch" in text


def _push_authorized(text: str, args: list[str], *, cwd: str | None = None, protected_targets: list[str] | None = None) -> bool:
    ref_text = " ".join(_push_refspecs(args) + _push_targets(args) + _push_sources(args)).lower()
    push_word = bool(
        re.search(r"\bpush\b|推送|推上去|推到|推倒|推吧", text, re.IGNORECASE)
        or re.search(r"合(?:并到|入)\s*(?:origin/)?(?:master|main)\b", text, re.IGNORECASE)
    )
    if not push_word:
        return False
    if not _push_refspecs(args):
        current_branch = _git_current_branch(cwd)
        if current_branch and _mentions_current_branch(text):
            return "origin" in text or any(_text_has_alias(text, [target]) for target in protected_targets or [])
    if any(part and part.lower() in text for part in ref_text.split()):
        return True
    if protected_targets and any(_text_has_alias(text, [target]) for target in protected_targets):
        return True
    current_branch = _git_current_branch(cwd)
    if current_branch and _mentions_current_branch(text) and any(source in {current_branch, "HEAD"} for source in _push_sources(args)):
        return "origin" in text or any(target and target in text for target in _push_targets(args))
    return False


def _force_with_lease_authorized(text: str, args: list[str]) -> bool:
    return _push_authorized(text, args) and "force-with-lease" in text


def _ssh_invocations(command: str) -> list[tuple[str, str]]:
    invocations: list[tuple[str, str]] = []
    try:
        merge_guard = _load_merge_guard()
    except Exception:
        merge_guard = None
    for statement in _candidate_statements(command, merge_guard):
        try:
            tokens = shlex.split(statement, posix=True)
        except ValueError:
            continue
        tokens = _strip_transparent_prefixes(tokens)
        if not tokens or Path(tokens[0]).name != "ssh":
            continue
        host = ""
        remote_parts: list[str] = []
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if not host and token.startswith("-"):
                if token in {"-i", "-p", "-l", "-o", "-F", "-J"} and index + 1 < len(tokens):
                    index += 2
                    continue
                index += 1
                continue
            if not host:
                host = token
            else:
                # Redirections after the SSH payload are evaluated by the
                # local shell. Their file operands are not remote targets.
                if (
                    token.startswith(("<", ">", "&>"))
                    or re.match(r"^\d+(?:<|>|>>|<&|>&)", token)
                ):
                    break
                remote_parts.append(token)
            index += 1
        if host:
            invocations.append((host, " ".join(remote_parts)))
    return invocations


def _remote_copy_write_destinations(command: str) -> list[tuple[str, str]]:
    """Return the remote destinations of ordinary scp/rsync uploads."""
    destinations: list[tuple[str, str]] = []
    try:
        merge_guard = _load_merge_guard()
    except Exception:
        merge_guard = None
    remote_operand = re.compile(r"^(?:[^/@:\s]+@)?(?P<host>[A-Za-z0-9_.-]+):(?P<path>/.*)$")
    for statement in _candidate_statements(command, merge_guard):
        try:
            tokens = _strip_transparent_prefixes(shlex.split(statement, posix=True))
        except ValueError:
            continue
        if not tokens or Path(tokens[0]).name not in {"scp", "rsync"}:
            continue
        operands, uncertain = _option_operands(
            tokens[1:],
            value_options={"-F", "-i", "-J", "-l", "-o", "-P", "-S", "-e", "--rsh", "--rsync-path"},
        )
        if uncertain or not operands:
            continue
        # For both tools the final positional operand is the destination.
        destination = operands[-1]
        if destination == "--" or destination.startswith("-"):
            continue
        match = remote_operand.fullmatch(destination)
        if match:
            destinations.append((match.group("host"), match.group("path")))
    return destinations


def _is_remote_copy_write(command: str) -> bool:
    return bool(_remote_copy_write_destinations(command))


def _strip_transparent_prefixes(tokens: list[str]) -> list[str]:
    current = list(tokens)
    changed = True
    while changed and current:
        changed = False
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", current[0]):
            current = current[1:]
            changed = True
            continue
        executable = Path(current[0]).name
        if executable == "command":
            current = current[1:]
            changed = True
            continue
        if executable == "nohup":
            current = current[1:]
            if current[:1] == ["--"]:
                current = current[1:]
            changed = True
            continue
        if executable == "env":
            index = 1
            while index < len(current):
                token = current[index]
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", token):
                    index += 1
                    continue
                if token == "--":
                    index += 1
                    break
                if token in {"-S", "--split-string"}:
                    if index + 1 >= len(current):
                        return []
                    try:
                        expanded = shlex.split(current[index + 1], posix=True)
                    except ValueError:
                        return []
                    current = [*expanded, *current[index + 2 :]]
                    changed = True
                    break
                if token.startswith("--split-string="):
                    try:
                        expanded = shlex.split(token.split("=", 1)[1], posix=True)
                    except ValueError:
                        return []
                    current = [*expanded, *current[index + 1 :]]
                    changed = True
                    break
                if token.startswith("-S") and token != "-S":
                    try:
                        expanded = shlex.split(token[2:], posix=True)
                    except ValueError:
                        return []
                    current = [*expanded, *current[index + 1 :]]
                    changed = True
                    break
                if token in {"-u", "--unset", "-C", "--chdir", "-a", "--argv0"}:
                    if index + 1 >= len(current):
                        return []
                    index += 2
                    continue
                if token.startswith(("--unset=", "--chdir=", "--argv0=")):
                    index += 1
                    continue
                if token.startswith("-"):
                    index += 1
                    continue
                break
            else:
                return []
            if not changed:
                current = current[index:]
                changed = True
            continue
        if executable == "timeout":
            index = 1
            while index < len(current) and current[index].startswith("-"):
                option = current[index]
                index += 1
                if option in {"-s", "--signal", "-k", "--kill-after"} and index < len(current):
                    index += 1
            if index < len(current):
                index += 1
            current = current[index:]
            changed = True
            continue
    return current


def _candidate_statements(command: str, merge_guard: Any | None = None, *, _depth: int = 0) -> list[str]:
    if _depth > 3:
        return [command]
    statements: list[str] = [command]
    try:
        if merge_guard is not None:
            statements.extend(str(item) for item in merge_guard._shell_statements(command))
        else:
            statements.extend(re.split(r"\s*(?:&&|;|\n)\s*", command))
    except Exception:
        statements.extend(re.split(r"\s*(?:&&|;|\n)\s*", command))

    expanded: list[str] = []
    seen: set[str] = set()
    for statement in statements:
        statement = statement.strip()
        if not statement or statement in seen:
            continue
        seen.add(statement)
        expanded.append(statement)
        try:
            tokens = shlex.split(statement, posix=True)
        except ValueError:
            continue
        wrapped = _shell_wrapped_command(tokens)
        if not wrapped:
            continue
        for nested in _candidate_statements(wrapped, merge_guard, _depth=_depth + 1):
            if nested not in seen:
                seen.add(nested)
                expanded.append(nested)
    return expanded


def _shell_wrapped_command(tokens: list[str]) -> str:
    current = _strip_transparent_prefixes(tokens)
    if not current:
        return ""
    executable = Path(current[0]).name
    if executable not in SHELL_WRAPPER_NAMES:
        return ""
    index = 1
    while index < len(current):
        token = current[index]
        if token == "-c":
            return current[index + 1] if index + 1 < len(current) else ""
        if token.startswith("-") and "c" in token[1:]:
            return current[index + 1] if index + 1 < len(current) else ""
        index += 1
    return ""


def _prodctl_tokens(tokens: list[str]) -> list[str]:
    current = _strip_transparent_prefixes(tokens)
    if not current:
        return []
    executable = Path(current[0]).name
    if executable == "prodctl":
        return current
    if not PYTHON_WRAPPER_RE.match(executable):
        return []
    index = 1
    while index < len(current):
        token = current[index]
        if token == "--":
            index += 1
            break
        if not token.startswith("-"):
            break
        if token in {"-c", "-m"}:
            return []
        if token in {"-W", "-X"} and index + 1 < len(current):
            index += 2
            continue
        index += 1
    if index < len(current) and Path(current[index]).name == "prodctl":
        return ["prodctl", *current[index + 1 :]]
    return []


def _remote_write_actions(remote: str) -> list[str]:
    actions: list[str] = []
    lower = remote.lower()
    lower_without_devnull_redirects = re.sub(r"(?<![<])(?:\b\d+)?>{1,2}\s*/dev/null\b", "", lower)
    # Duplicating an existing standard descriptor is not a filesystem write.
    lower_without_devnull_redirects = re.sub(r"(?<![\w])(?:[012])?>&[012](?![\w])", "", lower_without_devnull_redirects)
    if re.search(
        r"\bgit\s+(?:fetch|pull|checkout|switch|merge|rebase|cherry-pick|reset)\b"
        r"|\bgit\s+branch\s+(?:-[A-Za-z]*[dD][A-Za-z]*|--delete\b)",
        lower,
    ):
        actions.append("git")
    if re.search(r"\b(?:pnpm|npm|yarn)\s+(?:run\s+)?build\b", lower):
        actions.append("build")
    if re.search(
        r"\bsystemctl\s+(?:restart|start|stop|reload)\b"
        r"|\bservice\s+\S+\s+(?:restart|start|stop|reload)\b"
        r"|\bsupervisorctl\s+(?:restart|start|stop|reload)\b",
        lower,
    ):
        actions.append("restart")
    if re.search(r"(?<![A-Za-z0-9_-])touch\s+", lower):
        actions.append("touch")
    if re.search(r"(?<![A-Za-z0-9_-])(?:rmdir|rm|unlink)\s+", lower):
        actions.append("remove")
    if re.search(r"(?<![A-Za-z0-9_-])mkdir\s+", lower):
        actions.append("mkdir")
    if re.search(r"(?<![A-Za-z0-9_-])cp\s+", lower):
        actions.append("copy")
    if re.search(r"(?<![A-Za-z0-9_-])mv\s+", lower):
        actions.append("move")
    if re.search(r"(?<![A-Za-z0-9_-])chmod\s+", lower):
        actions.append("chmod")
    if re.search(r"(?<![A-Za-z0-9_-])chown\s+", lower):
        actions.append("chown")
    if re.search(r"(?<![A-Za-z0-9_-])ln\s+(?:-[A-Za-z]*s[A-Za-z]*\s+|--symbolic\s+)", lower):
        actions.append("symlink")
    if re.search(r"(?<![A-Za-z0-9_-])install\s+", lower):
        actions.append("install")
    if re.search(
        r"(?<![A-Za-z0-9_-])sed\s+-i(?:\b|[.=\s])"
        r"|(?<![A-Za-z0-9_-])sed\s+[^;&|]*\s-i(?:\b|[.=\s])"
        r"|(?<![A-Za-z0-9_-])tee\s+"
        r"|(?<![<>])>{1,2}(?!>)",
        lower_without_devnull_redirects,
    ):
        actions.append("edit")
    return actions


def _remote_cd_dir(remote: str) -> str:
    match = re.search(r"(?is)(?:^|[;&]\s*)cd\s+([^;&]+)", remote)
    if not match:
        return ""
    try:
        tokens = shlex.split("cd " + match.group(1), posix=True)
    except ValueError:
        return ""
    return tokens[1] if len(tokens) > 1 else ""


def _remote_services(remote: str) -> list[str]:
    services: list[str] = []
    for match in re.finditer(r"\bsystemctl\s+(?:restart|start|stop|reload)\s+([A-Za-z0-9_.@:-]+)", remote):
        services.append(match.group(1))
    for match in re.finditer(r"\bservice\s+([A-Za-z0-9_.@:-]+)\s+(?:restart|start|stop|reload)\b", remote):
        services.append(match.group(1))
    for match in re.finditer(r"\bsupervisorctl\s+(?:restart|start|stop|reload)\s+([A-Za-z0-9_.@:-]+)", remote):
        services.append(match.group(1))
    return services


def _server_policy(policy: dict[str, Any], host: str) -> dict[str, Any]:
    server = _server_section(policy)
    hosts = server.get("hosts") if isinstance(server, dict) else {}
    if not isinstance(hosts, dict):
        return {}
    data = hosts.get(host)
    if isinstance(data, dict):
        result = dict(data)
        result.setdefault("_canonical_host", host)
        return result
    for canonical, candidate in hosts.items():
        if not isinstance(candidate, dict):
            continue
        aliases = [str(canonical), *[str(item) for item in candidate.get("aliases") or []], *[str(item) for item in candidate.get("ssh_aliases") or []]]
        if host in aliases:
            result = dict(candidate)
            result.setdefault("_canonical_host", str(canonical))
            return result
    return {}


def _host_authorized(policy: dict[str, Any], host: str, host_policy: dict[str, Any], transcript: str) -> bool:
    canonical = str(host_policy.get("_canonical_host") or "").strip()
    aliases = [host]
    if canonical:
        aliases.append(canonical)
        aliases.extend(_aliases(policy, "env", canonical))
    aliases.extend(str(item) for item in host_policy.get("aliases") or [])
    aliases.extend(str(item) for item in host_policy.get("ssh_aliases") or [])
    return _text_has_alias(transcript, aliases)


def _host_env_guess(host: str) -> str:
    lower = host.lower()
    if "prod" in lower:
        return "prod"
    if "pre" in lower or "staging" in lower:
        return "pre"
    if "test" in lower:
        return "test"
    return ""


def _policy_claims_host(policy: dict[str, Any], host: str, host_policy: dict[str, Any]) -> bool:
    if host_policy:
        return True
    return _host_env_guess(host) in {"prod", "pre", "test"}


def _path_allowed(path: str, allowed: list[Any]) -> bool:
    return bool(path) and any(path == str(item) or path.startswith(str(item).rstrip("/") + "/") for item in allowed)


def _services_allowed(services: list[str], allowed: list[Any]) -> bool:
    return all(service in {str(item) for item in allowed} for service in services)


def _remote_shell_segments(remote: str) -> list[str]:
    try:
        merge_guard = _load_merge_guard()
        segments = merge_guard._shell_statements(remote)
    except Exception:
        segments = re.split(r"\s*(?:&&|;|\n|\|)\s*", remote)
    return [str(segment).strip() for segment in segments if str(segment).strip()]


def _strip_remote_prefixes(tokens: list[str]) -> list[str]:
    current = list(tokens)
    changed = True
    while changed and current:
        changed = False
        if current[0] == "command":
            current = current[1:]
            changed = True
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", current[0]):
            current = current[1:]
            changed = True
            continue
        if current[0] == "env":
            index = 1
            while index < len(current) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", current[index]):
                index += 1
            current = current[index:]
            changed = True
            continue
        if Path(current[0]).name == "sudo":
            # Only the option-free form is transparent. Other sudo forms can
            # change identity or execution behavior and stay unclassified.
            if len(current) > 1 and not current[1].startswith("-"):
                current = current[1:]
                changed = True
                continue
    return current


def _option_operands(args: list[str], *, value_options: set[str] | None = None) -> tuple[list[str], bool]:
    value_options = value_options or set()
    operands: list[str] = []
    index = 0
    options_done = False
    uncertain = False
    while index < len(args):
        token = args[index]
        if not options_done and token == "--":
            options_done = True
            index += 1
            continue
        if not options_done and token.startswith("--"):
            name = token.split("=", 1)[0]
            if name in value_options and "=" not in token:
                if index + 1 >= len(args):
                    uncertain = True
                index += 2
                continue
            index += 1
            continue
        if not options_done and token.startswith("-") and token != "-":
            if token in value_options:
                if index + 1 >= len(args):
                    uncertain = True
                index += 2
                continue
            index += 1
            continue
        operands.append(token)
        index += 1
    return operands, uncertain


def _option_value_from_tokens(args: list[str], names: set[str]) -> str:
    for index, token in enumerate(args):
        name = token.split("=", 1)[0]
        if name in names:
            if "=" in token:
                return token.split("=", 1)[1]
            if index + 1 < len(args):
                return args[index + 1]
    return ""


def _remote_cd_target(tokens: list[str]) -> str:
    if not tokens or tokens[0] != "cd":
        return ""
    operands, _ = _option_operands(tokens[1:])
    return operands[0] if operands else ""


def _posix_normalize_remote_path(path: str, cwd: str) -> str | None:
    raw = path.strip()
    if not raw:
        return None
    if raw.startswith("&") or raw.startswith("~") or raw == "-":
        return None
    if re.search(r"[$`*?\[\]{}]", raw):
        return None
    if raw.startswith("/"):
        candidate = raw
    else:
        if not cwd.startswith("/"):
            return None
        candidate = posixpath.join(cwd, raw)
    normalized = posixpath.normpath(candidate)
    if not normalized.startswith("/"):
        return None
    if normalized.startswith("//"):
        return None
    return normalized


def _normalized_allowed_app_dirs(allowed: list[Any]) -> list[str]:
    normalized: list[str] = []
    for item in allowed:
        path = _posix_normalize_remote_path(str(item), "/")
        if path:
            normalized.append(path.rstrip("/") or "/")
    return normalized


def _remote_path_allowed(path: str, allowed: list[str]) -> bool:
    return bool(path) and any(path == item or path.startswith(item.rstrip("/") + "/") for item in allowed)


def _redirection_targets(segment: str) -> tuple[list[str], bool]:
    targets: list[str] = []
    uncertain = False
    index = 0
    in_single = False
    in_double = False
    escaped = False
    while index < len(segment):
        char = segment[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            index += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            index += 1
            continue
        if in_single or in_double:
            index += 1
            continue
        start = index
        if char == "&" and index + 1 < len(segment) and segment[index + 1] == ">":
            index += 2
            if index < len(segment) and segment[index] == ">":
                index += 1
        elif char == ">" and (index == 0 or segment[index - 1] != "<"):
            index += 1
            if index < len(segment) and segment[index] == ">":
                index += 1
        else:
            index += 1
            continue
        if start > 0 and segment[start - 1].isdigit():
            scan = start - 1
            while scan >= 0 and segment[scan].isdigit():
                scan -= 1
            if scan >= 0 and not segment[scan].isspace():
                continue
        rest = segment[index:].lstrip()
        if not rest:
            uncertain = True
            break
        try:
            token = shlex.split(rest, posix=True)[0]
        except (IndexError, ValueError):
            uncertain = True
            break
        if token.startswith("&"):
            continue
        if token != "/dev/null":
            targets.append(token)
    return targets, uncertain


def _sed_in_place_targets(args: list[str]) -> tuple[list[str], bool]:
    has_in_place = False
    script_consumed = False
    targets: list[str] = []
    index = 0
    uncertain = False
    while index < len(args):
        token = args[index]
        if token == "--":
            targets.extend(args[index + 1 :])
            break
        if token == "-i":
            has_in_place = True
            if index + 1 < len(args) and args[index + 1] == "":
                index += 2
            else:
                index += 1
            continue
        if token.startswith("-i") or token.startswith("--in-place"):
            has_in_place = True
            index += 1
            continue
        if token in {"-e", "--expression", "-f", "--file"}:
            if index + 1 >= len(args):
                uncertain = True
            script_consumed = True
            index += 2
            continue
        if token.startswith("--expression=") or token.startswith("--file="):
            script_consumed = True
            index += 1
            continue
        if token.startswith("-") and token != "-":
            index += 1
            continue
        if not script_consumed:
            script_consumed = True
            index += 1
            continue
        targets.append(token)
        index += 1
    if has_in_place and not targets:
        uncertain = True
    return targets if has_in_place else [], uncertain


def _remote_command_write_targets(tokens: list[str]) -> tuple[list[str], bool]:
    if not tokens:
        return [], False
    command = Path(tokens[0]).name
    args = tokens[1:]
    if command == "touch":
        return _option_operands(args, value_options={"-d", "--date", "-r", "--reference", "-t"})
    if command == "mkdir":
        return _option_operands(args, value_options={"-m", "--mode", "-Z", "--context"})
    if command in {"rm", "rmdir", "unlink"}:
        return _option_operands(args)
    if command == "chmod":
        operands, uncertain = _option_operands(args, value_options={"--reference"})
        if any(token == "--reference" or token.startswith("--reference=") for token in args):
            return operands, uncertain or not operands
        return operands[1:], uncertain or len(operands) < 2
    if command == "chown":
        operands, uncertain = _option_operands(args, value_options={"--reference", "--from"})
        if any(token == "--reference" or token.startswith("--reference=") for token in args):
            return operands, uncertain or not operands
        return operands[1:], uncertain or len(operands) < 2
    if command == "cp":
        target_dir = _option_value_from_tokens(args, {"-t", "--target-directory"})
        if target_dir:
            return [target_dir], False
        operands, uncertain = _option_operands(args, value_options={"-t", "--target-directory", "-S", "--suffix"})
        return operands[-1:], uncertain or len(operands) < 2
    if command == "install":
        target_dir = _option_value_from_tokens(args, {"-t", "--target-directory"})
        if target_dir:
            return [target_dir], False
        operands, uncertain = _option_operands(
            args,
            value_options={"-m", "--mode", "-o", "--owner", "-g", "--group", "-S", "--suffix", "-t", "--target-directory"},
        )
        if any(token == "-d" or token == "--directory" for token in args):
            return operands, uncertain or not operands
        return operands[-1:], uncertain or len(operands) < 2
    if command == "mv":
        operands, uncertain = _option_operands(args, value_options={"-S", "--suffix", "-t", "--target-directory"})
        target_dir = _option_value_from_tokens(args, {"-t", "--target-directory"})
        if target_dir:
            operands.append(target_dir)
        return operands, uncertain or len(operands) < 2
    if command == "ln":
        operands, uncertain = _option_operands(args, value_options={"-S", "--suffix", "-t", "--target-directory"})
        symbolic = any(token == "-s" or token == "--symbolic" or (token.startswith("-") and "s" in token[1:]) for token in args)
        if not symbolic:
            return [], uncertain
        target_dir = _option_value_from_tokens(args, {"-t", "--target-directory"})
        if target_dir:
            return [*operands, target_dir], uncertain or not operands
        return operands, uncertain or len(operands) < 2
    if command == "tee":
        return _option_operands(args)
    if command == "sed":
        return _sed_in_place_targets(args)
    return [], False


def _remote_mutation_bounds(remote: str, allowed_app_dirs: list[Any], allowed_services: list[Any]) -> bool:
    allowed_dirs = _normalized_allowed_app_dirs(allowed_app_dirs)
    if not allowed_dirs:
        return False
    cwd = ""
    for segment in _remote_shell_segments(remote):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            if _remote_write_actions(segment):
                return False
            continue
        tokens = _strip_remote_prefixes(tokens)
        if not tokens:
            continue
        cd_target = _remote_cd_target(tokens)
        if cd_target:
            normalized_cd = _posix_normalize_remote_path(cd_target, cwd)
            cwd = normalized_cd or ""
            continue
        command = Path(tokens[0]).name
        segment_actions = _remote_write_actions(segment)
        if {"build", "git", "restart"}.intersection(segment_actions):
            if not _remote_path_allowed(cwd, allowed_dirs):
                return False
            if not _services_allowed(_remote_services(segment), allowed_services):
                return False
        redirect_targets, redirect_uncertain = _redirection_targets(segment)
        command_targets, command_uncertain = _remote_command_write_targets(tokens)
        targets = [*redirect_targets, *command_targets]
        if redirect_uncertain or command_uncertain:
            return False
        if REMOTE_FS_ACTIONS.intersection(segment_actions) and not targets:
            return False
        for target in targets:
            normalized = _posix_normalize_remote_path(target, cwd)
            if not normalized or not _remote_path_allowed(normalized, allowed_dirs):
                return False
    return True


def _remote_git_invocations(remote: str) -> list[tuple[str, list[str]]]:
    invocations: list[tuple[str, list[str]]] = []
    for segment in _remote_shell_segments(remote):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            continue
        tokens = _strip_remote_prefixes(tokens)
        if not tokens or Path(tokens[0]).name != "git":
            continue
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token in {"-C", "-c", "--git-dir", "--work-tree"} and index + 1 < len(tokens):
                index += 2
                continue
            if token.startswith("-"):
                index += 1
                continue
            args = tokens[index + 1 :]
            if token in {"symbolic-ref", "ls-remote"} and index != 1:
                # Global options can redirect execution/configuration. Do not
                # erase them when classifying these newly admitted probes.
                args = ["--unvalidated-global-options", *args]
            invocations.append((token, args))
            break
    return invocations


def _remote_git_subcommands(remote: str) -> list[str]:
    subcommands: list[str] = []
    for subcommand, _ in _remote_git_invocations(remote):
        subcommands.append(subcommand)
    return subcommands


def _pretest_readonly_git_probe(subcommand: str, args: list[str], env: str) -> bool:
    if subcommand == "symbolic-ref":
        # One ref operand queries; a second operand or -d/-m mutates it.
        flags = {"--short", "-q", "--quiet", "--no-recurse", "--recurse"}
        operands = [arg for arg in args if arg not in flags]
        return len(operands) == 1 and bool(operands[0]) and not operands[0].startswith("-")
    if subcommand == "ls-remote":
        # Ordinary ref inspection only; do not admit custom upload-pack commands.
        flags = {"--heads", "--tags", "--refs", "--exit-code", "--symref", "-q", "--quiet"}
        operands = [arg for arg in args if arg not in flags]
        return bool(operands) and operands[0] in {"origin", "upstream"} and all(arg and not arg.startswith("-") for arg in operands)
    if subcommand == "branch":
        return args in ([], ["--show-current"], ["--list"])
    if subcommand in {"rev-parse", "status", "merge-base"}:
        return True
    return False


def _pretest_routine_git_invocation(subcommand: str, args: list[str], env: str) -> bool:
    if _pretest_readonly_git_probe(subcommand, args, env):
        return True
    if subcommand in {"checkout", "fetch", "switch"}:
        return True
    if subcommand == "merge":
        # Host classification is not a branch name: a test machine may host
        # the project's shared pre checkout in its configured app directory.
        return args in (["--ff-only", "origin/pre"], ["--ff-only", "origin/test"])
    return False


def _pretest_nonroutine_git_reason(remote: str, env: str) -> str:
    # Risk detection must also cover shell control constructs which the narrow
    # routine-command parser does not interpret (for example `if git reset`).
    if re.search(r"\bgit\s+(?:[^\n;&|]*\s)?reset\b[^\n;&|]*--hard\b", remote):
        return "reset --hard may discard work; use non-destructive shared-branch synchronization or explicitly assess destructive exception"
    git_invocations = _remote_git_invocations(remote)
    for subcommand, args in git_invocations:
        if subcommand == "reset" and "--hard" in args:
            return "reset --hard may discard work; use non-destructive shared-branch synchronization or explicitly assess destructive exception"
    for subcommand, args in git_invocations:
        if not _pretest_routine_git_invocation(subcommand, args, env):
            return f"git {subcommand} is outside routine nonproduction branch sync bounds"
    return ""


def _pretest_runtime_tee_targets(remote: str, allowed_dirs: list[Any]) -> bool:
    """Allow an option-free tee only for fresh nonproduction runtime artifacts."""
    roots = _normalized_allowed_app_dirs(allowed_dirs)
    cwd = ""
    saw_tee = False
    for segment in _remote_shell_segments(remote):
        try:
            tokens = _strip_remote_prefixes(shlex.split(segment, posix=True))
        except ValueError:
            return False
        if not tokens:
            continue
        cd = _remote_cd_target(tokens)
        if cd:
            cwd = _posix_normalize_remote_path(cd, cwd) or ""
            continue
        actions = _remote_write_actions(segment)
        if "edit" not in actions:
            continue
        if Path(tokens[0]).name != "tee":
            return False
        # Shell tokenization keeps a no-space `>/dev/null` next to tee as an
        # argument. It is only an output sink, not another file target.
        tee_args = [arg for arg in tokens[1:] if not re.fullmatch(r"(?:\d+)?>{1,2}/dev/null", arg)]
        if any(arg.startswith("-") for arg in tee_args):
            return False
        targets, uncertain = _remote_command_write_targets([tokens[0], *tee_args])
        if uncertain or not targets:
            return False
        for target in targets:
            normalized = _posix_normalize_remote_path(target, cwd)
            if not normalized or not any(normalized.startswith(root.rstrip("/") + "/.runtime/") for root in roots):
                return False
        saw_tee = True
    return saw_tee


def _pretest_runtime_cleanup_targets(remote: str, allowed_dirs: list[Any]) -> bool:
    """Allow explicit cleanup only inside one nonproduction task runtime directory."""
    roots = _normalized_allowed_app_dirs(allowed_dirs)
    cwd = ""
    saw_cleanup = False
    for segment in _remote_shell_segments(remote):
        try:
            tokens = _strip_remote_prefixes(shlex.split(segment, posix=True))
        except ValueError:
            return False
        if not tokens:
            continue
        cd = _remote_cd_target(tokens)
        if cd:
            cwd = _posix_normalize_remote_path(cd, cwd) or ""
            continue
        command = Path(tokens[0]).name
        if command not in {"rm", "rmdir", "unlink"}:
            continue
        targets, uncertain = _remote_command_write_targets(tokens)
        if uncertain or not targets or any(token.startswith("-") or any(char in token for char in "*?[") for token in tokens[1:]):
            return False
        for target in targets:
            normalized = _posix_normalize_remote_path(target, cwd)
            matching_root = next((root for root in roots if normalized and normalized.startswith(root.rstrip("/") + "/.runtime/")), None)
            if not matching_root:
                return False
            relative = normalized[len(matching_root.rstrip("/") + "/.runtime/") :]
            parts = [part for part in relative.split("/") if part]
            # A run directory makes cleanup task-scoped. `rm` must not remove
            # the run directory itself; `rmdir` may remove it only when empty.
            if not parts or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{7,}", parts[0]):
                return False
            if command in {"rm", "unlink"} and len(parts) < 2:
                return False
        saw_cleanup = True
    return saw_cleanup


def _pretest_routine_ssh_actions(actions: list[str], remote: str, env: str, allowed_dirs: list[Any]) -> bool:
    if not actions or not set(actions).issubset({"build", "git", "restart", "touch", "mkdir", "chmod", "edit", "remove"}):
        return False
    if "edit" in actions and not _pretest_runtime_tee_targets(remote, allowed_dirs):
        return False
    if "remove" in actions and not _pretest_runtime_cleanup_targets(remote, allowed_dirs):
        return False
    git_invocations = _remote_git_invocations(remote)
    if "git" in actions:
        return bool(git_invocations) and all(
            _pretest_routine_git_invocation(subcommand, args, env) for subcommand, args in git_invocations
        )
    return all(_pretest_readonly_git_probe(subcommand, args, env) for subcommand, args in git_invocations)


def _pretest_filesystem_risk(remote: str, allowed_dirs: list[Any]) -> str:
    """Technical checks only; approval wording cannot waive these risks."""
    actions = set(_remote_write_actions(remote))
    if actions & {"chown", "symlink"}:
        return "deletion, ownership or symlink mutation needs a scoped safety review; repeating authorization cannot waive this boundary"
    if "remove" in actions and not _pretest_runtime_cleanup_targets(remote, allowed_dirs):
        return "deletion outside an explicit task .runtime directory needs a scoped safety review"
    cwd = ""
    roots = _normalized_allowed_app_dirs(allowed_dirs)
    for segment in _remote_shell_segments(remote):
        try:
            tokens = _strip_remote_prefixes(shlex.split(segment, posix=True))
        except ValueError:
            return "cannot determine filesystem mutation bounds"
        if not tokens:
            continue
        cd = _remote_cd_target(tokens)
        if cd:
            cwd = _posix_normalize_remote_path(cd, cwd) or ""
            continue
        targets, _ = _remote_command_write_targets(tokens)
        targets += _redirection_targets(segment)[0]
        if any(_posix_normalize_remote_path(target, cwd) in roots for target in targets):
            return "mutation of the application root itself needs a scoped safety review"
        command, args = Path(tokens[0]).name, tokens[1:]
        if command == "chmod":
            if len(args) < 2 or not re.fullmatch(r"0?[0-7]{3}", args[0]):
                return "recursive, symbolic or special permission changes need a scoped safety review"
            if int(args[0], 8) & 0o002 or any(arg.startswith("-") for arg in args[1:]):
                return "world-writable or option-bearing permission changes need a scoped safety review"
        if command in {"install", "mkdir"}:
            mode = _option_value_from_tokens(args, {"-m", "--mode"})
            if mode and (not re.fullmatch(r"0?[0-7]{3}", mode) or int(mode, 8) & 0o002):
                return "special or world-writable creation mode needs a scoped safety review"
            if command == "install" and any(arg in {"-o", "-g", "--owner", "--group"} or arg.startswith(("--owner=", "--group=")) for arg in args):
                return "ownership changes need a scoped safety review"
    return ""


def _handle_nonprod_remote_copy(policy: dict[str, Any], transcript_text: Callable[[], str], command: str) -> tuple[bool, str]:
    destinations = _remote_copy_write_destinations(command)
    if not destinations:
        return True, ""
    for host, target in destinations:
        host_policy = _server_policy(policy, host)
        env = str(host_policy.get("env") or "").lower()
        if env not in {"pre", "test"}:
            return False, "remote scp/rsync writes are limited to registered pre/test hosts"
        if _current_text_denies_server_action(transcript_text(), ["copy"]):
            return False, f"{env} file transfer conflicts with the current stop/restriction"
        normalized = _posix_normalize_remote_path(target, "/")
        roots = _normalized_allowed_app_dirs(list(host_policy.get("allowed_app_dirs") or []))
        if not normalized or not any(normalized.startswith(root.rstrip("/") + "/.runtime/") for root in roots):
            return False, f"{env} file transfer must target a configured application .runtime directory"
    return True, ""


def _current_text_denies_server_action(text: str, actions: list[str], *, transport: str = "") -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if not normalized:
        return False
    negative = r"(?:不要|不能|不可|禁止|先别|别|不执行|do not|don't|must not|never|no)"

    def denied_near(terms: tuple[str, ...]) -> bool:
        term_pattern = "|".join(re.escape(term) for term in terms)
        return bool(
            re.search(rf"{negative}[^。；;,.!?\n]{{0,24}}(?:{term_pattern})", normalized)
            or re.search(rf"(?:{term_pattern})[^。；;,.!?\n]{{0,24}}{negative}", normalized)
        )

    if transport and denied_near((transport,)):
        return True
    action_terms = {
        "build": ("build", "构建", "编译"),
        "git": ("git", "deploy", "部署", "发布", "上线", "更新代码", "拉取代码", "切换分支"),
        "restart": ("restart", "重启", "reload", "启动", "停止"),
        "mkdir": ("mkdir", "创建目录", "建目录"),
        "touch": ("touch", "创建", "新建"),
        "chmod": ("chmod", "权限"),
        "copy": ("copy", "cp", "复制"),
        "move": ("move", "mv", "移动"),
        "edit": ("edit", "write", "写入", "修改"),
        "install": ("install", "安装"),
    }
    if normalized.strip(" ，。！？,.!?") in {"停止", "暂停", "先停下", "先等等", "只读排查", "仅只读", "只读"}:
        return True
    if any(denied_near(action_terms.get(action, (action,))) for action in actions):
        return True
    return denied_near(("execute", "执行", "计划"))


def _handle_ssh(
    policy: dict[str, Any],
    transcript_text: Callable[[], str],
    server_transcript_text: Callable[..., str],
    command: str,
) -> tuple[bool, str]:
    for host, remote in _ssh_invocations(command):
        host_policy = _server_policy(policy, host)
        actions = _remote_write_actions(remote)
        if not actions:
            continue
        transcript = transcript_text()
        errors = _policy_errors(policy)
        if errors:
            return False, "server policy is malformed for server mutation: " + "; ".join(errors)
        if _policy_has_hosts(policy) and not host_policy and _policy_claims_host(policy, host, host_policy):
            return False, f"server host {host} is not present in policy"
        if not host_policy:
            guessed_env = _host_env_guess(host)
            if guessed_env == "prod":
                return False, f"prod raw SSH writes to {host} require prodctl unless a current-turn emergency bypass matches target and action"
            if guessed_env in {"pre", "test"}:
                return False, f"{guessed_env} raw SSH writes to {host} require a host policy with configured bounds"
            continue
        env = str(host_policy.get("env") or "").lower() or _host_env_guess(host)
        if env == "prod" and not host_policy:
            return False, f"prod raw SSH writes to {host} require prodctl unless a current-turn emergency bypass matches target and action"
        services = _remote_services(remote)
        services = list(dict.fromkeys(services))
        service = services[0] if len(services) == 1 else ""
        transcript = server_transcript_text(
            environment=env,
            actions=actions,
            service=service,
            services=services,
            host=host,
            transport="ssh",
        )
        bounded = _remote_mutation_bounds(
            remote,
            list(host_policy.get("allowed_app_dirs") or []),
            list(host_policy.get("services") or []),
        )
        if env == "prod":
            if not bounded:
                return False, f"prod raw SSH write to {host} is outside configured bounds"
            action_ok = all(_action_authorized(policy, transcript, action) for action in actions)
            host_ok = _host_authorized(policy, host, host_policy, transcript)
            env_ok = _text_has_alias(transcript, _aliases(policy, "env", env)) or host_ok
            emergency = (
                _action_authorized(policy, transcript, "emergency_bypass")
                and env_ok
                and host_ok
                and action_ok
                and all(service.lower() in transcript for service in services)
            )
            if not emergency:
                return False, f"prod raw SSH writes to {host} require prodctl unless a current-turn emergency bypass matches target and action"
            continue
        if env in {"pre", "test"}:
            if not bounded:
                return False, f"{env} SSH write to {host} is outside the configured app directory or service bounds"
            nonroutine_git_reason = _pretest_nonroutine_git_reason(remote, env)
            if nonroutine_git_reason:
                return False, f"{env} SSH write to {host} {nonroutine_git_reason}"
            if _current_text_denies_server_action(
                transcript_text(),
                actions,
                transport="ssh",
            ):
                return False, f"{env} SSH mutation conflicts with the current stop/restriction"
            risk = _pretest_filesystem_risk(remote, list(host_policy.get("allowed_app_dirs") or []))
            if risk:
                return False, f"{env} SSH write to {host}: {risk}"
            if _pretest_routine_ssh_actions(actions, remote, env, list(host_policy.get("allowed_app_dirs") or [])):
                continue
            return False, f"{env} SSH operation has unresolved technical bounds; inspect the operation instead of requesting approval keywords"
    return True, ""


def _action_authorized(policy: dict[str, Any], transcript: str, action: str) -> bool:
    if action == "git":
        return _text_has_alias(transcript, _aliases(policy, "action", "git")) or _text_has_alias(
            transcript, _aliases(policy, "action", "deploy")
        )
    if action == "restart":
        return _text_has_alias(transcript, _aliases(policy, "action", "restart"))
    if action == "touch":
        return _text_has_alias(transcript, ["touch", "create", "创建", "新建"])
    if action == "remove":
        return _text_has_alias(transcript, ["remove", "delete", "rm", "删除"])
    if action == "edit":
        return _text_has_alias(transcript, ["edit", "write", "sed -i", "tee", "修改", "写入"])
    if action == "mkdir":
        return _text_has_alias(transcript, ["mkdir", "create directory", "创建目录"])
    if action == "copy":
        return _text_has_alias(transcript, ["copy", "cp", "复制"])
    if action == "move":
        return _text_has_alias(transcript, ["move", "mv", "rename", "移动", "重命名"])
    if action == "chmod":
        return _text_has_alias(transcript, ["chmod", "permission", "权限"])
    if action == "chown":
        return _text_has_alias(transcript, ["chown", "owner", "ownership", "所属"])
    if action == "symlink":
        return _text_has_alias(transcript, ["ln -s", "symlink", "symbolic link", "链接"])
    if action == "install":
        return _text_has_alias(transcript, ["install", "安装"])
    return _text_has_alias(transcript, _aliases(policy, "action", action))


def _prodctl_invocations(command: str) -> list[tuple[str, list[str]]]:
    invocations: list[tuple[str, list[str]]] = []
    try:
        merge_guard = _load_merge_guard()
    except Exception:
        merge_guard = None
    for statement in _candidate_statements(command, merge_guard):
        if merge_guard and len(merge_guard._shell_statements(statement)) > 1:
            continue
        try:
            tokens = shlex.split(statement, posix=True)
        except ValueError:
            continue
        tokens = _prodctl_tokens(tokens)
        tokens = [token for token in tokens if token]
        if not tokens:
            continue
        subcommand = tokens[1] if len(tokens) > 1 else ""
        invocations.append((subcommand, tokens[2:]))
    return invocations


def _restart_preview(args: list[str]) -> bool:
    # Only the audited restart clients are covered. Never treat an arbitrary
    # command containing the words dry-run (or an option value) as read-only.
    valued = {'--fleet', '--host', '--service', '--approval-token', '--net'}
    index = 0
    preview = False
    while index < len(args):
        arg = args[index]
        if arg == '--dry-run':
            preview = True
            index += 1
        elif arg in valued and index + 1 < len(args):
            index += 2
        elif any(arg.startswith(option + '=') for option in valued):
            index += 1
        else:
            return False
    return preview


def _option_value(args: list[str], *names: str) -> str:
    for index, arg in enumerate(args):
        for name in names:
            if arg == name and index + 1 < len(args):
                return args[index + 1]
            if arg.startswith(name + "="):
                return arg.split("=", 1)[1]
    return ""


def _prodctl_env(policy: dict[str, Any], args: list[str]) -> str:
    env = _option_value(args, "--env", "--environment")
    if env:
        return env.lower()
    host = _option_value(args, "--host")
    host_policy = _server_policy(policy, host) if host else {}
    return str(host_policy.get("env") or "").lower() or _host_env_guess(host)


def _prodctl_host_policy(policy: dict[str, Any], args: list[str]) -> tuple[str, dict[str, Any]]:
    host = _option_value(args, "--host")
    return host, _server_policy(policy, host) if host else {}


def _handle_prodctl(
    policy: dict[str, Any],
    server_transcript_text: Callable[..., str],
    command: str,
    current_user_text: Callable[[], str] | None = None,
) -> tuple[bool, str]:
    read_only = {"plan", "plan-deploy", "status", "show", "inspect", "check", "validate", "dry-run"}
    mutating_actions = {
        "deploy": "deploy",
        "restart": "restart",
        "restart-service": "restart",
        "rollback": "rollback",
        "start": "restart",
        "stop": "restart",
        "reload": "restart",
        "safe-git": "git",
    }
    for subcommand, args in _prodctl_invocations(command):
        if subcommand in read_only or subcommand.startswith("plan-"):
            continue
        action = mutating_actions.get(subcommand)
        if not action:
            continue
        errors = _policy_errors(policy)
        if errors:
            return False, "server policy is malformed for prodctl mutation: " + "; ".join(errors)
        if _option_value(args, "--plan-id") and not _option_value(args, "--host"):
            return False, f"prodctl {subcommand} with plan-id requires --host to resolve the plan target"
        env = _prodctl_env(policy, args)
        host, host_policy = _prodctl_host_policy(policy, args)
        if host and _policy_has_hosts(policy) and not host_policy:
            return False, f"prodctl host {host} is not present in policy"
        if subcommand in {'restart', 'restart-service'} and _restart_preview(args):
            continue
        if env in {"prod", "pre", "test"}:
            service = _option_value(args, "--service")
            ref = _option_value(args, "--ref")
            if env in {"pre", "test"}:
                allowed_services = {str(item).lower() for item in (host_policy.get("services") or [])}
                if host_policy and service and service.lower() not in allowed_services:
                    return False, f"{env} prodctl target/service is outside configured bounds"
                if host_policy:
                    if current_user_text and _current_text_denies_server_action(current_user_text(), ["git" if action == "deploy" else action]):
                        return False, f"{env} prodctl mutation conflicts with the current stop/restriction"
                    # The Agent owns user intent; the client/server validates plans.
                    # Presence of an arbitrary token is not authorization evidence.
                    continue
            transcript = server_transcript_text(
                environment=env,
                actions=[action],
                service=service,
                host=host,
                transport="prodctl",
                ref=ref,
            )
            if env == "prod" and subcommand == "deploy" and _is_exact_hex_ref(ref):
                if not _prod_detached_ref_authorized(transcript, ref):
                    return (
                        False,
                        "prod detached commit deploy requires explicit current-turn emergency/pinned-detached authorization matching ref; use master deploy by default",
                    )
            env_alias_ok = _text_has_alias(transcript, _aliases(policy, "env", env))
            host_ok = not host_policy or _host_authorized(policy, host, host_policy, transcript) or env_alias_ok
            env_ok = env_alias_ok or host_ok
            action_ok = _action_authorized(policy, transcript, action)
            if not (env_ok and host_ok and action_ok):
                return False, f"prodctl {subcommand} requires current-turn authorization matching environment and action"
            if service and service.lower() not in transcript:
                host_services = [str(item).lower() for item in (host_policy.get("services") or [])]
                batch_hosts = _mentioned_policy_hosts(policy, transcript)
                batch_service_allowed = (
                    env == "prod"
                    and host in batch_hosts
                    and _is_batch_rollout_plan(transcript, batch_hosts)
                    and service.lower() in host_services
                )
                if not batch_service_allowed and (env == "prod" or len(host_services) != 1 or host_services[0] != service.lower()):
                    return False, f"prodctl {subcommand} requires current-turn authorization matching service"
    return True, ""


def _handle_git(
    policy: dict[str, Any],
    payload: dict[str, Any],
    merge_guard: Any,
    transcript_text: Callable[..., str],
    command: str,
) -> tuple[bool, str]:
    base_cwd = _payload_git_base_cwd(payload)
    patterns = _protected_patterns(policy)
    for subcommand, args, cwd in _git_commands(merge_guard, command, base_cwd):
        if subcommand == "gh pr merge":
            transcript = transcript_text()
            state = _load_state(cwd)
            pr_number = next((arg for arg in args if not arg.startswith("-")), "")
            pr_auth = "merge" in transcript and ("pr" in transcript or "pull request" in transcript) and (not pr_number or pr_number in transcript)
            if not pr_auth:
                return False, "gh pr merge requires matching merge-safety intent/precheck and current-turn PR merge authorization"
            target = _target_from_transcript(transcript, patterns)
            if not _matches_intent(state, target=target, source=pr_number, operation="merge"):
                return False, "gh pr merge requires matching merge-safety intent and current-turn PR merge authorization"
            if not _precheck_ok(state):
                return False, "gh pr merge requires merge-safety pre-check"
            continue
        if subcommand in {"merge", "rebase", "cherry-pick"} and "--abort" in args:
            continue
        if subcommand == "pull":
            target = ""
            if len(args) >= 2:
                target = args[1]
            branch = target or _git_current_branch(cwd)
            if _is_protected_ref(branch, patterns):
                return False, "protected git pull is blocked; use git fetch plus an explicit merge/rebase under merge-safety"
            continue
        if subcommand in {"merge", "rebase", "cherry-pick"}:
            target = _git_current_branch(cwd)
            if not _is_protected_ref(target, patterns):
                continue
            state = _load_state(cwd)
            source = next((arg for arg in args if not arg.startswith("-")), "HEAD")
            if not (_matches_intent(state, target=target, source=source, operation=subcommand) and _precheck_ok(state)):
                return False, "protected merge/rebase/cherry-pick requires matching merge-safety intent and successful pre-check"
            continue
        if subcommand == "push":
            all_or_mirror = _has_push_all_or_mirror(args)
            if all_or_mirror:
                return False, f"git push {all_or_mirror} is blocked; use explicit single-ref pushes with matching authorization"
            refspecs = _push_refspecs(args)
            config_hazard = _push_config_hazard(args, cwd=cwd, refspecs=refspecs)
            if config_hazard:
                return False, f"git push configuration {config_hazard} can expand or redirect the write; use an explicit single branch refspec"
            targets = _push_targets(args)
            sources = _push_sources(args)
            if not refspecs:
                current_branch = _git_current_branch(cwd)
                if current_branch:
                    targets = [current_branch]
                    sources = [current_branch]
            elif len(refspecs) == 1 and refspecs[0] in {"HEAD", "@"}:
                current_branch = _git_current_branch(cwd)
                if current_branch:
                    targets = [current_branch]
                    sources = [refspecs[0]]
            protected = [target for target in targets if _is_protected_ref(target, patterns)]
            if len(refspecs) > 1:
                if protected:
                    return False, "protected git push with multiple refspecs is blocked; use separate single-ref pushes"
                return False, "git push with multiple refspecs is blocked; use separate single-ref pushes"
            if _has_force(args):
                return False, "--force is blocked; use an exact current-turn --force-with-lease authorization if rewriting is intended"
            if _has_push_delete(args):
                return False, "push delete is blocked; use a dedicated explicit deletion workflow"
            if _is_ordinary_single_unprotected_push(args, cwd=cwd, patterns=patterns, refspecs=refspecs, targets=targets):
                continue
            transcript = transcript_text(
                protected_targets=protected,
                sources=sources,
                remote=_push_remote(args),
                cwd=cwd,
            )
            if _has_force_with_lease(args) and not _force_with_lease_authorized(transcript, args):
                return False, "--force-with-lease requires exact current-turn authorization"
            if len(protected) > 1:
                return False, "protected push with multiple protected targets is blocked; use separate single-ref pushes"
            protected_push_missing: list[str] = []
            pretest_evidence_authorized = False
            if protected:
                state = _load_state(cwd)
                source = sources[0] if sources else "HEAD"
                target = protected[0]
                protected_push_missing = _protected_push_missing_requirements(state, target=target, source=source, cwd=cwd)
                pretest_evidence_authorized = _ref_name(target) in {"pre", "test"} and not protected_push_missing
            if not _push_authorized(transcript, args, cwd=cwd, protected_targets=protected) and not pretest_evidence_authorized:
                return False, "shell git push requires current-turn authorization"
            if protected:
                if protected_push_missing:
                    return False, "protected push requires " + ", ".join(dict.fromkeys(protected_push_missing))
            continue
    return True, ""


def main() -> int:
    payload = _read_payload()
    if _tool_name(payload) not in {"Bash", "exec_command"}:
        return _allow()
    command = _command(payload)
    if not RISK_CANDIDATE_RE.search(command):
        return _allow()
    policy = _load_policy(payload)
    transcript_context_cache: AuthorizationContext | None = None

    def transcript_context() -> AuthorizationContext:
        nonlocal transcript_context_cache
        if transcript_context_cache is None:
            transcript_context_cache = _transcript_context(payload)
        return transcript_context_cache

    def transcript_text(**scope: Any) -> str:
        protected_targets = scope.get("protected_targets")
        if isinstance(protected_targets, list):
            return _git_push_authorization_text(
                policy,
                transcript_context(),
                protected_targets=[str(target) for target in protected_targets],
                sources=[str(source) for source in scope.get("sources", [])],
                remote=str(scope.get("remote") or ""),
                cwd=str(scope.get("cwd") or "") or None,
            )
        return transcript_context().current_user_text

    def server_transcript_text(**scope: Any) -> str:
        return _server_authorization_text(policy, transcript_context(), **scope)

    ok, reason = _handle_nonprod_remote_copy(policy, transcript_text, command)
    if not ok:
        return _block(reason)

    try:
        merge_guard = _load_merge_guard()
    except MergeGuardNotFound as exc:
        paths = [str(path) for path in exc.checked_paths]
        return _block(
            "critical action gate cannot find merge_guard.py helper; checked_paths="
            + ", ".join(paths)
            + "; next action: install merge-safety into CODEX_HOME/skills or AGENTS_HOME/skills, then rerun the command",
            checked_paths=paths,
            next_action="Install merge-safety into CODEX_HOME/skills or AGENTS_HOME/skills, then rerun the command.",
        )

    ok, reason = _handle_prodctl(policy, server_transcript_text, command, lambda: transcript_context().current_user_text)
    if not ok:
        return _block(reason)
    ok, reason = _handle_ssh(policy, transcript_text, server_transcript_text, command)
    if not ok:
        return _block(reason)
    ok, reason = _handle_git(policy, payload, merge_guard, transcript_text, command)
    if not ok:
        return _block(reason)
    return _allow()


if __name__ == "__main__":
    raise SystemExit(main())
