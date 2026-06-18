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
    "requirements_interpreter": "requirements",
    "requirements-interpreter": "requirements",
    "implementer": "implementation",
    "worker": "implementation",
    "reviewer": "review",
    "critic": "review",
    "test_designer": "test_design",
    "test-designer": "test_design",
    "test-writer": "test_implementation",
    "test_writer": "test_implementation",
    "test-runner": "test_execution",
    "test_runner": "test_execution",
    "conflict-resolver": "conflict_resolution",
    "conflict_resolver": "conflict_resolution",
    "deployer": "deployment",
    "researcher": "research",
    "explorer": "research",
}

ROLE_SUFFIX_CAPABILITIES = (
    ("requirements_interpreter", "requirements"),
    ("implementer", "implementation"),
    ("reviewer", "review"),
    ("test_designer", "test_design"),
    ("test-writer", "test_implementation"),
    ("test_writer", "test_implementation"),
    ("test-runner", "test_execution"),
    ("test_runner", "test_execution"),
    ("conflict_resolver", "conflict_resolution"),
    ("deployer", "deployment"),
    ("researcher", "research"),
    ("explorer", "research"),
)

WORKFLOW_STATE_FIELDS = (
    "state_version",
    "task_id",
    "turn_id",
    "created_at",
    "updated_at",
    "current_mode",
    "task_class",
    "selected_skill",
    "stage",
    "required_roles",
    "spawned_roles",
    "waited_roles",
    "closed_roles",
    "role_assignments",
    "role_evidence",
    "verification_commands",
    "reviewer_result",
    "exception_reason",
    "retained_subagents_reason",
    "files_changed",
    "final_status",
    "mode_reason",
    "lane",
    "phase",
    "elapsed_step",
    "asked_user_count",
    "subagent_count",
    "intent",
    "action",
    "target_env",
    "target_ref",
    "writes_shared_branch",
    "risk_flags",
    "authorization",
    "rollback_plan",
    "sampled_recheck_reason",
)

WORKFLOW_MODES = {"tiny_direct", "split_workflow"}
WORKFLOW_STATE_VERSION = 1
WORKFLOW_STAGES = {"planning", "spawning", "integrating", "verifying", "reviewing", "done"}
WORKFLOW_FINAL_STATUSES = {"pass", "fail", "blocked", "needs_context", "not_run"}

DEFAULT_WORKFLOW_POLICY = {
    "heavy_task_requires_state": False,
    "tiny_direct_requires_reason": True,
    "split_workflow_requires_selected_skill": True,
    "split_workflow_requires_required_roles": True,
    "require_state_version": False,
    "require_task_identity": False,
    "require_workflow_stage": False,
    "require_role_evidence": False,
    "require_final_status": False,
}

DEFAULT_SKILL_REQUIREMENTS = {
    "feature": {
        "required_roles": ["implementer", "test_designer", "test_runner", "reviewer"],
        "conditional_roles": ["test_writer", "researcher", "requirements_interpreter", "critic"],
        "forbidden_roles": [],
        "one_of_roles": [],
        "requires_verification_commands": True,
        "requires_reviewer_result": True,
    },
    "bugfix": {
        "required_roles": ["implementer", "test_designer", "test_runner", "reviewer"],
        "conditional_roles": ["test_writer", "researcher", "requirements_interpreter", "critic"],
        "forbidden_roles": [],
        "one_of_roles": [],
        "requires_verification_commands": True,
        "requires_reviewer_result": True,
    },
    "merge": {
        "required_roles": ["conflict_resolver", "test_runner"],
        "conditional_roles": ["reviewer"],
        "forbidden_roles": ["implementer"],
        "one_of_roles": [],
        "requires_verification_commands": True,
        "requires_reviewer_result": False,
    },
    "review": {
        "required_roles": ["reviewer"],
        "conditional_roles": ["researcher", "critic"],
        "forbidden_roles": ["implementer", "test_writer", "deployer"],
        "one_of_roles": [],
        "requires_verification_commands": False,
        "requires_reviewer_result": True,
    },
    "predeploy": {
        "required_roles": ["deployer"],
        "conditional_roles": ["reviewer"],
        "forbidden_roles": [],
        "one_of_roles": [],
        "requires_verification_commands": True,
        "requires_reviewer_result": False,
        "requires_current_turn_authorization": True,
        "fast_lanes": ["fast_pre", "fast_hotfix"],
        "fast_lanes_require_roles": False,
    },
}


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


def workflow_policy(payload: dict) -> dict[str, object]:
    profile = load_workflow_profile(payload)
    policy = dict(DEFAULT_WORKFLOW_POLICY)
    configured = profile.get("workflow_policy", {}) if isinstance(profile, dict) else {}
    if isinstance(configured, dict):
        for key, default in DEFAULT_WORKFLOW_POLICY.items():
            if key in configured:
                policy[key] = bool(configured[key]) if isinstance(default, bool) else configured[key]
    return policy


def role_capability_map(payload: dict) -> dict[str, str]:
    mapping = dict(DEFAULT_ROLE_CAPABILITIES)
    profile = load_workflow_profile(payload)
    role_capabilities = profile.get("role_capabilities", {}) if isinstance(profile, dict) else {}
    if isinstance(role_capabilities, dict):
        for role, capability in role_capabilities.items():
            role_key = normalize_role_token(role)
            canonical_role = canonical_role_token(role)
            capability_key = normalize_capability(capability)
            if role_key and capability_key:
                mapping[role_key] = capability_key
            if canonical_role and capability_key:
                mapping[canonical_role] = capability_key
    return mapping


def normalize_role_token(value: object) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    return re.sub(r"[^a-z0-9_-]+", "", token.replace(" ", "_"))


def canonical_role_token(value: object) -> str:
    return normalize_role_token(value).replace("-", "_")


def normalize_capability(value: object) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    token = re.sub(r"[^a-z0-9_]+", "", token)
    return token


def infer_capability(role_token: object, payload: dict | None = None) -> str | None:
    normalized = normalize_role_token(role_token)
    if not normalized:
        return None
    canonical = canonical_role_token(role_token)

    mapping = role_capability_map(payload or {})
    if normalized in mapping:
        return mapping[normalized]
    if canonical in mapping:
        return mapping[canonical]

    for suffix, capability in ROLE_SUFFIX_CAPABILITIES:
        if normalized.endswith(suffix) or canonical.endswith(suffix.replace("-", "_")):
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


def _extract_text(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        texts: list[str] = []
        for child in value.values():
            texts.extend(_extract_text(child))
        return texts
    if isinstance(value, list):
        texts = []
        for child in value:
            texts.extend(_extract_text(child))
        return texts
    return []


def _current_turn_text_fragments(payload: dict) -> list[str]:
    fragments: list[str] = []
    transcript_path = payload.get("transcript_path")
    if isinstance(transcript_path, str):
        for item in _iter_current_turn_items(transcript_path):
            item_payload = item.get("payload", {}) if isinstance(item, dict) else {}
            if item_payload.get("type") == "message" and item_payload.get("role") == "assistant":
                fragments.extend(_extract_text(item_payload.get("content")))
    last_message = payload.get("last_assistant_message")
    if isinstance(last_message, str) and last_message.strip():
        fragments.append(last_message)
    return fragments


def _parse_scalar_or_list(raw_value: object) -> object:
    if not isinstance(raw_value, str):
        return raw_value
    value = raw_value.strip().strip("`")
    if not value:
        return ""
    try:
        return json.loads(value)
    except Exception:
        pass
    if "," in value:
        return [part.strip().strip("\"'") for part in value.split(",") if part.strip()]
    return value.strip("\"'")


def _coerce_list(value: object) -> list[str]:
    parsed = _parse_scalar_or_list(value)
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(parsed, tuple):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(parsed, str):
        if not parsed.strip():
            return []
        return [parsed.strip()]
    if parsed is None:
        return []
    return [str(parsed).strip()] if str(parsed).strip() else []


def _merge_workflow_state(target: dict[str, object], source: dict[str, object]) -> None:
    for field, value in source.items():
        if field not in WORKFLOW_STATE_FIELDS:
            continue
        if field in {"required_roles", "spawned_roles", "waited_roles", "closed_roles", "verification_commands", "files_changed", "risk_flags"}:
            target[field] = _coerce_list(value)
        elif field in {"role_assignments", "role_evidence"}:
            parsed = _parse_scalar_or_list(value)
            target[field] = parsed if isinstance(parsed, dict) else {}
        elif field == "state_version":
            try:
                target[field] = int(value)  # type: ignore[arg-type]
            except Exception:
                target[field] = value
        elif field in {"current_mode", "selected_skill"}:
            target[field] = normalize_capability(value)
        elif field in {"task_class", "stage", "final_status", "lane", "phase", "action", "target_env"}:
            target[field] = normalize_capability(value)
        elif field in {"asked_user_count", "subagent_count"}:
            try:
                target[field] = int(value)  # type: ignore[arg-type]
            except Exception:
                target[field] = value
        elif field == "writes_shared_branch":
            parsed = _parse_scalar_or_list(value)
            if isinstance(parsed, bool):
                target[field] = parsed
            else:
                target[field] = str(parsed).strip().lower() in {"1", "true", "yes", "y", "shared", "共享"}
        else:
            target[field] = value


def _extract_json_after_marker(text: str, marker: str) -> list[dict[str, object]]:
    objects: list[dict[str, object]] = []
    for match in re.finditer(marker, text, flags=re.IGNORECASE):
        start = text.find("{", match.end())
        if start < 0:
            continue
        depth = 0
        in_string = False
        escape = False
        end = -1
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        if end < 0:
            continue
        try:
            parsed = json.loads(text[start:end])
        except Exception:
            continue
        if isinstance(parsed, dict):
            nested = parsed.get("workflow_state")
            objects.append(nested if isinstance(nested, dict) else parsed)
    return objects


def workflow_state_from_text(text: str) -> dict[str, object]:
    state: dict[str, object] = {}
    if not text:
        return state

    for obj in _extract_json_after_marker(text, r"workflow_state\s*[:=]"):
        _merge_workflow_state(state, obj)
    for obj in _extract_json_after_marker(text, r'"workflow_state"\s*:'):
        _merge_workflow_state(state, obj)

    line_re = re.compile(r"(?im)^\s*workflow_state\.(?P<field>[a-zA-Z0-9_]+)\s*[:=]\s*(?P<value>.+?)\s*$")
    for match in line_re.finditer(text):
        field = match.group("field").strip()
        value = match.group("value").strip()
        _merge_workflow_state(state, {field: value})
    return state


def current_turn_workflow_state(payload: dict) -> dict[str, object]:
    state: dict[str, object] = {}
    for fragment in _current_turn_text_fragments(payload):
        _merge_workflow_state(state, workflow_state_from_text(fragment))
    return state


def normalize_selected_skill(value: object) -> str:
    token = normalize_capability(value)
    token = token.removesuffix("_workflow")
    if token in {"pre_deploy", "pre_verify", "pre_verification", "deploy"}:
        return "predeploy"
    if token in {"merge_safety", "conflict", "conflict_resolution"}:
        return "merge"
    if token in {"code_review", "review_only"}:
        return "review"
    return token


def skill_requirements(selected_skill: object, payload: dict | None = None) -> dict[str, object]:
    skill = normalize_selected_skill(selected_skill) or "feature"
    requirements = dict(DEFAULT_SKILL_REQUIREMENTS.get(skill, DEFAULT_SKILL_REQUIREMENTS["feature"]))
    requirements["required_roles"] = list(requirements.get("required_roles", []))
    requirements["one_of_roles"] = [list(group) for group in requirements.get("one_of_roles", [])]

    profile = load_workflow_profile(payload or {})
    configured_skills = profile.get("workflow_skills", {}) if isinstance(profile, dict) else {}
    configured = configured_skills.get(skill, {}) if isinstance(configured_skills, dict) else {}
    if isinstance(configured, dict):
        for list_key in ("required_roles", "conditional_roles", "forbidden_roles"):
            if list_key in configured:
                requirements[list_key] = _coerce_list(configured[list_key])
        if "one_of_roles" in configured and isinstance(configured["one_of_roles"], list):
            requirements["one_of_roles"] = [
                [canonical_role_token(role) for role in group]
                for group in configured["one_of_roles"]
                if isinstance(group, list)
            ]
        for key in (
            "requires_verification_commands",
            "requires_reviewer_result",
            "requires_current_turn_authorization",
            "fast_lanes_require_roles",
        ):
            if key in configured:
                requirements[key] = bool(configured[key])
        if "fast_lanes" in configured:
            requirements["fast_lanes"] = _coerce_list(configured["fast_lanes"])

    requirements["required_roles"] = [canonical_role_token(role) for role in requirements["required_roles"]]
    requirements["conditional_roles"] = [canonical_role_token(role) for role in requirements.get("conditional_roles", [])]
    requirements["forbidden_roles"] = [canonical_role_token(role) for role in requirements.get("forbidden_roles", [])]
    requirements["one_of_roles"] = [
        [canonical_role_token(role) for role in group]
        for group in requirements["one_of_roles"]
    ]
    requirements["fast_lanes"] = [normalize_capability(lane) for lane in requirements.get("fast_lanes", [])]
    return requirements


def normalized_workflow_roles(state: dict[str, object], field: str) -> list[str]:
    return [canonical_role_token(role) for role in _coerce_list(state.get(field)) if canonical_role_token(role)]


def role_satisfies(required_role: object, actual_role: object) -> bool:
    return canonical_role_token(required_role) == canonical_role_token(actual_role)


def _missing_required_roles(required_roles: list[str], actual_roles: list[str]) -> list[str]:
    return [role for role in required_roles if not any(role_satisfies(role, actual) for actual in actual_roles)]


def _missing_one_of_roles(one_of_groups: list[list[str]], actual_roles: list[str]) -> list[list[str]]:
    missing: list[list[str]] = []
    for group in one_of_groups:
        if group and not any(any(role_satisfies(option, actual) for actual in actual_roles) for option in group):
            missing.append(group)
    return missing


def _truthy_text(value: object) -> bool:
    text = str(value or "").strip().lower()
    return bool(text and text not in {"false", "no", "none", "null", "pending", "unknown", "todo", "tbd", "missing"})


def _looks_like_exact_ref(value: object) -> bool:
    text = str(value or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", text):
        return True
    if re.fullmatch(r"(?:refs/tags/)?v?\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9._-]+)?", text):
        return True
    if text.startswith("sha:") and _truthy_text(text.split(":", 1)[1]):
        return True
    return False


def workflow_state_status(state: dict[str, object], payload: dict | None = None) -> dict[str, object]:
    mode = normalize_capability(state.get("current_mode"))
    selected_skill = normalize_selected_skill(state.get("selected_skill"))
    stage = normalize_capability(state.get("stage"))
    final_status = normalize_capability(state.get("final_status"))
    policy = workflow_policy(payload or {})
    status: dict[str, object] = {
        "mode": mode,
        "selected_skill": selected_skill,
        "stage": stage,
        "final_status": final_status,
        "errors": [],
        "missing_by_phase": {},
        "missing_one_of_by_phase": {},
        "missing_role_evidence": [],
        "forbidden_roles_present": [],
        "unclosed_roles": [],
    }
    errors: list[str] = status["errors"]  # type: ignore[assignment]
    if mode and mode not in WORKFLOW_MODES:
        errors.append("invalid_current_mode")
    state_version = state.get("state_version")
    if state_version not in (None, "", WORKFLOW_STATE_VERSION):
        errors.append("unsupported_state_version")
    if policy.get("require_state_version") and state_version != WORKFLOW_STATE_VERSION:
        errors.append("missing_state_version")
    if policy.get("require_task_identity") and not str(state.get("task_id", "")).strip():
        errors.append("missing_task_id")
    if stage and stage not in WORKFLOW_STAGES:
        errors.append("invalid_stage")
    if policy.get("require_workflow_stage") and mode == "split_workflow" and not stage:
        errors.append("missing_stage")
    if final_status and final_status not in WORKFLOW_FINAL_STATUSES:
        errors.append("invalid_final_status")
    if policy.get("require_final_status") and mode == "split_workflow" and stage == "done" and not final_status:
        errors.append("missing_final_status")
    if mode != "split_workflow":
        return status

    requirements = skill_requirements(selected_skill, payload)
    lane = normalize_capability(state.get("lane"))
    fast_lane = bool(
        selected_skill == "predeploy"
        and lane
        and lane in set(requirements.get("fast_lanes", []))
        and not requirements.get("fast_lanes_require_roles", False)
    )
    explicit_required = normalized_workflow_roles(state, "required_roles")
    required_roles = explicit_required or list(requirements.get("required_roles", []))
    if fast_lane and not explicit_required:
        required_roles = []
    one_of_roles = list(requirements.get("one_of_roles", []))
    forbidden_roles = list(requirements.get("forbidden_roles", []))
    if explicit_required:
        one_of_roles = [
            group for group in one_of_roles
            if not any(role in explicit_required for role in group)
        ]

    all_ledger_roles = set()
    for phase in ("spawned_roles", "waited_roles", "closed_roles"):
        actual_roles = normalized_workflow_roles(state, phase)
        all_ledger_roles.update(actual_roles)
        missing = _missing_required_roles(required_roles, actual_roles)
        if missing:
            status["missing_by_phase"][phase] = missing  # type: ignore[index]
        missing_groups = _missing_one_of_roles(one_of_roles, actual_roles)
        if missing_groups:
            status["missing_one_of_by_phase"][phase] = missing_groups  # type: ignore[index]

    forbidden_present = sorted(role for role in forbidden_roles if any(role_satisfies(role, actual) for actual in all_ledger_roles))
    if forbidden_present:
        status["forbidden_roles_present"] = forbidden_present
        errors.append("forbidden_roles_present")

    spawned = set(normalized_workflow_roles(state, "spawned_roles"))
    closed = set(normalized_workflow_roles(state, "closed_roles"))
    unclosed = sorted(spawned - closed)
    if unclosed:
        status["unclosed_roles"] = unclosed
        if not str(state.get("retained_subagents_reason", "")).strip():
            errors.append("spawned_roles_not_closed")

    if requirements.get("requires_verification_commands") and not _coerce_list(state.get("verification_commands")):
        errors.append("missing_verification_commands")
    if requirements.get("requires_reviewer_result") and not str(state.get("reviewer_result", "")).strip():
        errors.append("missing_reviewer_result")
    if selected_skill == "predeploy" and requirements.get("requires_current_turn_authorization"):
        if not _truthy_text(state.get("authorization")):
            errors.append("missing_current_turn_authorization")
    writes_shared_branch = bool(state.get("writes_shared_branch"))
    if selected_skill == "predeploy" and (writes_shared_branch or lane in {"fast_hotfix", "shared_lane", "controlled_ops"}):
        if not _truthy_text(state.get("rollback_plan")):
            errors.append("missing_rollback_plan")
    if selected_skill == "predeploy" and lane == "fast_hotfix":
        if not _looks_like_exact_ref(state.get("target_ref")):
            errors.append("fast_hotfix_missing_exact_ref")

    role_evidence = state.get("role_evidence")
    if isinstance(role_evidence, dict):
        evidence_roles = {canonical_role_token(role) for role in role_evidence}
    else:
        evidence_roles = set()
    evidence_required_roles = [
        role for role in required_roles
        if role in {"reviewer", "test_designer", "test_runner", "test_writer", "conflict_resolver", "deployer"}
    ]
    missing_evidence = [role for role in evidence_required_roles if role not in evidence_roles]
    if missing_evidence:
        status["missing_role_evidence"] = missing_evidence
        if policy.get("require_role_evidence"):
            errors.append("missing_role_evidence")

    if status["missing_by_phase"]:  # type: ignore[index]
        errors.append("missing_required_roles")
    if status["missing_one_of_by_phase"]:  # type: ignore[index]
        errors.append("missing_required_role_alternative")
    return status


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
