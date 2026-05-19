#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None


DEFAULT_ROLE_CAPABILITIES = {
    "implementer": "implementation",
    "worker": "implementation",
    "reviewer": "review",
    "critic": "review",
    "test_designer": "test_design",
    "test-writer": "test_design",
    "test_writer": "test_design",
    "test-runner": "test_execution",
    "test_runner": "test_execution",
    "researcher": "research",
    "explorer": "research",
}

ROLE_SUFFIX_CAPABILITIES = (
    ("implementer", "implementation"),
    ("reviewer", "review"),
    ("test-writer", "test_design"),
    ("test_writer", "test_design"),
    ("test-runner", "test_execution"),
    ("test_runner", "test_execution"),
    ("researcher", "research"),
    ("explorer", "research"),
)


def _candidate_roots(payload: dict) -> list[Path]:
    roots: list[Path] = []
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        roots.append(Path(cwd).expanduser())

    transcript_path = payload.get("transcript_path")
    if isinstance(transcript_path, str) and transcript_path.strip():
        try:
            roots.append(Path(transcript_path).expanduser().resolve().parent)
        except Exception:
            pass

    env_cwd = os.environ.get("PWD")
    if env_cwd:
        roots.append(Path(env_cwd).expanduser())
    return roots


def _profile_path(payload: dict) -> Path | None:
    for root in _candidate_roots(payload):
        current = root
        for candidate in (current, *current.parents):
            path = candidate / ".codex" / "review-profile.toml"
            if path.exists():
                return path
    return None


def load_workflow_profile(payload: dict) -> dict:
    path = _profile_path(payload)
    if not path or tomllib is None:
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except Exception:
        return {}
    data["_profile_path"] = str(path)
    return data


def workflow_options(payload: dict) -> dict:
    profile = load_workflow_profile(payload)
    options = profile.get("workflow_profile", {}) if isinstance(profile, dict) else {}
    if not isinstance(options, dict):
        options = {}
    return {
        "pretool_policy": str(options.get("pretool_policy", "soft")).strip().lower() or "soft",
        "hide_internal_workflow_text": bool(options.get("hide_internal_workflow_text", True)),
    }


def role_capability_map(payload: dict) -> dict[str, str]:
    mapping = dict(DEFAULT_ROLE_CAPABILITIES)
    profile = load_workflow_profile(payload)
    role_capabilities = profile.get("role_capabilities", {}) if isinstance(profile, dict) else {}
    if isinstance(role_capabilities, dict):
        for role, capability in role_capabilities.items():
            role_key = normalize_role_token(role)
            capability_key = normalize_capability(capability)
            if role_key and capability_key:
                mapping[role_key] = capability_key
    return mapping


def normalize_role_token(value: object) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    return re.sub(r"[^a-z0-9_-]+", "", token.replace(" ", "_"))


def normalize_capability(value: object) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    token = re.sub(r"[^a-z0-9_]+", "", token)
    return token


def infer_capability(role_token: object, payload: dict | None = None) -> str | None:
    normalized = normalize_role_token(role_token)
    if not normalized:
        return None

    mapping = role_capability_map(payload or {})
    if normalized in mapping:
        return mapping[normalized]

    for suffix, capability in ROLE_SUFFIX_CAPABILITIES:
        if normalized.endswith(suffix):
            return capability
    return None


def _iter_current_turn_items(transcript_path: str | None) -> list[dict]:
    if not transcript_path:
        return []
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []

    last_user_index = -1
    parsed: list[dict | None] = []
    for index, line in enumerate(lines):
        try:
            item = json.loads(line)
        except Exception:
            parsed.append(None)
            continue
        parsed.append(item)
        payload = item.get("payload", {}) if isinstance(item, dict) else {}
        if payload.get("type") == "message" and payload.get("role") == "user":
            content_text = str(payload.get("content", ""))
            if "<hook_prompt" not in content_text:
                last_user_index = index

    if last_user_index < 0:
        return [item for item in parsed if isinstance(item, dict)]
    return [item for item in parsed[last_user_index + 1 :] if isinstance(item, dict)]


def current_turn_text(transcript_path: str | None) -> str:
    items = _iter_current_turn_items(transcript_path)
    if not items:
        return ""
    return "\n".join(json.dumps(item, ensure_ascii=False) for item in items)[-120000:]


def capabilities_from_text(text: str, payload: dict | None = None) -> set[str]:
    capabilities: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text or ""):
        capability = infer_capability(token, payload)
        if capability:
            capabilities.add(capability)
    return capabilities


def current_turn_capabilities(payload: dict) -> set[str]:
    transcript_path = payload.get("transcript_path")
    text = current_turn_text(transcript_path if isinstance(transcript_path, str) else None)
    capabilities = capabilities_from_text(text, payload)
    last_message = payload.get("last_assistant_message")
    if isinstance(last_message, str) and last_message.strip():
        capabilities |= capabilities_from_text(last_message, payload)
    return capabilities
