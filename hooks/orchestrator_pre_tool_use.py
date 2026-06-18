#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

from orchestrator_observability import (
    emit_observability_event,
    normalize_tool_family,
    normalize_tool_name,
    text_length,
)
from workflow_profile import (
    WORKFLOW_MODES,
    current_turn_capabilities,
    current_turn_workflow_state,
    workflow_options,
    workflow_policy,
)


DELEGATION_MARKERS = [
    "spawn_agent",
    "delegate",
    "delegation decision",
    "parallel agent",
    "subagent",
    "orchestrator workflow",
    "\u5206\u5de5",
    "\u59d4\u6258",
]


def all_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(all_text(v) for v in value.values())
    if isinstance(value, list):
        return "\n".join(all_text(v) for v in value)
    return "" if value is None else str(value)


def has_delegation_signal(transcript_path: str | None) -> bool:
    if not transcript_path:
        return False
    try:
        text = Path(transcript_path).read_text(encoding="utf-8", errors="ignore")[-120000:].lower()
    except Exception:
        return False
    return any(marker in text for marker in DELEGATION_MARKERS)


def required_capabilities(tool_name: str, tool_family: str, text: str) -> set[str]:
    if tool_family == "shell_like" and is_broad_scan(text):
        return {"research"}
    if tool_name in {"apply_patch", "edit", "write"}:
        return {"implementation"}
    return set()


def block(reason: str) -> int:
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def is_broad_scan(command: str) -> bool:
    c = command.lower()
    broad_patterns = [
        r"\brg\s+--files\b",
        r"\bget-childitem\b.*(?:^|\s)-recurse\b",
        r"\bselect-string\b.*(?:^|\s)-recurse\b",
        r"\bdir\b.*\s/s\b",
        r"\btree\b.*\s/f\b",
        r"\bls\b.*\s-r\b",
        r"\bfind\s+\.(?:\s|$)",
        r"\bgit\s+grep\b",
    ]
    return any(re.search(pattern, c, re.DOTALL) for pattern in broad_patterns)


def patch_file_count(patch: str) -> int:
    return len(re.findall(r"^\*\*\* (?:Add|Update|Delete) File:", patch, flags=re.MULTILINE))


def is_large_or_multi_file_edit(tool_name: str, text: str) -> tuple[bool, dict[str, object]]:
    if tool_name not in {"apply_patch", "edit", "write"}:
        return False, {}
    files = patch_file_count(text)
    large_patch = len(text) > 12000 or text.count("\n") > 220
    return files > 1 or large_patch, {"files": files, "large_patch": large_patch}


def is_workflow_state_update(tool_name: str, text: str) -> bool:
    if tool_name not in {"apply_patch", "edit", "write"}:
        return False
    lowered = text.lower()
    if "workflow_state" not in lowered:
        return False
    source_file_markers = (
        "/app/",
        "/src/",
        "/lib/",
        "/services/",
        "/routes/",
        "/controllers/",
        "/models/",
        "/packages/",
    )
    if any(marker in lowered for marker in source_file_markers):
        return False
    return True


def is_dry_run_or_audit(text: str) -> bool:
    lowered = text.lower()
    return "--dry-run" in lowered or "dry run" in lowered or "dry-run" in lowered or "audit" in lowered


def is_write_or_execution_risk(tool_name: str, tool_family: str, text: str) -> bool:
    if tool_name in {"apply_patch", "edit", "write"}:
        return not is_workflow_state_update(tool_name, text)
    lowered = text.lower()
    risky_shell_patterns = [
        r"\bpytest\b",
        r"\bnpm\s+test\b",
        r"\bpnpm\s+test\b",
        r"\bdeploy\b",
        r"\brestart\b",
        r"\bkubectl\b",
        r"\bterraform\s+apply\b",
        r"\bgit\s+(merge|rebase|cherry-pick|pull|push)\b",
    ]
    if tool_family == "shell_like" and any(re.search(pattern, lowered) for pattern in risky_shell_patterns):
        return not is_dry_run_or_audit(lowered)
    return False


def is_heavy_tool_use(tool_name: str, tool_family: str, text: str) -> tuple[bool, str, dict[str, object]]:
    broad_scan = tool_family == "shell_like" and is_broad_scan(text)
    if broad_scan:
        return True, "broad_scan", {}
    large_edit, edit_extra = is_large_or_multi_file_edit(tool_name, text)
    if large_edit:
        return True, "large_or_multifile_edit", edit_extra
    return False, "", {}


def main() -> int:
    payload = json.load(sys.stdin)
    tool_name = normalize_tool_name(payload.get("tool_name"))
    tool_family = normalize_tool_family(tool_name)
    tool_input = payload.get("tool_input", {})
    text = all_text(tool_input)
    input_length = text_length(tool_input)
    options = workflow_options(payload)
    pretool_policy = options.get("pretool_policy", "soft")
    policy = workflow_policy(payload)
    workflow_state = current_turn_workflow_state(payload)
    delegation_signal = has_delegation_signal(payload.get("transcript_path"))
    capability_signal = current_turn_capabilities(payload)
    needed_capabilities = required_capabilities(tool_name, tool_family, text)
    missing_capabilities = needed_capabilities - capability_signal
    broad_scan = tool_family == "shell_like" and is_broad_scan(text)
    heavy_tool_use, heavy_reason, heavy_extra = is_heavy_tool_use(tool_name, tool_family, text)
    workflow_mode = str(workflow_state.get("current_mode", "") or "").strip()

    if heavy_tool_use and policy.get("heavy_task_requires_state"):
        base_extra = {
            "workflow_state": workflow_state,
            "workflow_policy": policy,
            "heavy_reason": heavy_reason,
            "needed_capabilities": sorted(needed_capabilities),
            "current_turn_capabilities": sorted(capability_signal),
            "pretool_policy": pretool_policy,
            **heavy_extra,
        }
        if not workflow_mode:
            if is_workflow_state_update(tool_name, text) or (tool_family == "shell_like" and is_dry_run_or_audit(text)):
                emit_observability_event(
                    payload,
                    event_name="pre_tool_use",
                    status="allow",
                    reason="workflow_state_initialization_or_audit",
                    tool_family=tool_family,
                    normalized_tool_name=tool_name,
                    input_length=input_length,
                    tool_name=tool_name,
                    text_source=text,
                    extra=base_extra,
                )
                return 0
            emit_observability_event(
                payload,
                event_name="pre_tool_use",
                status="blocked",
                reason="heavy_task_missing_workflow_state",
                tool_family=tool_family,
                normalized_tool_name=tool_name,
                input_length=input_length,
                tool_name=tool_name,
                text_source=text,
                extra=base_extra,
            )
            return block(
                "Heavy work needs a machine-readable workflow_state first. Declare "
                "workflow_state.current_mode=tiny_direct or split_workflow, selected_skill, "
                "and required roles before running this tool."
            )
        if workflow_mode not in WORKFLOW_MODES:
            emit_observability_event(
                payload,
                event_name="pre_tool_use",
                status="blocked",
                reason="invalid_workflow_mode",
                tool_family=tool_family,
                normalized_tool_name=tool_name,
                input_length=input_length,
                tool_name=tool_name,
                text_source=text,
                extra=base_extra,
            )
            return block("workflow_state.current_mode must be tiny_direct or split_workflow.")
        if workflow_mode == "tiny_direct" and policy.get("tiny_direct_requires_reason"):
            if not str(workflow_state.get("mode_reason", "") or "").strip():
                emit_observability_event(
                    payload,
                    event_name="pre_tool_use",
                    status="blocked",
                    reason="tiny_direct_missing_reason",
                    tool_family=tool_family,
                    normalized_tool_name=tool_name,
                    input_length=input_length,
                    tool_name=tool_name,
                    text_source=text,
                    extra=base_extra,
                )
                return block("tiny_direct needs workflow_state.mode_reason explaining why delegation is not used.")
        if workflow_mode == "split_workflow":
            if policy.get("split_workflow_requires_selected_skill") and not workflow_state.get("selected_skill"):
                emit_observability_event(
                    payload,
                    event_name="pre_tool_use",
                    status="blocked",
                    reason="split_workflow_missing_selected_skill",
                    tool_family=tool_family,
                    normalized_tool_name=tool_name,
                    input_length=input_length,
                    tool_name=tool_name,
                    text_source=text,
                    extra=base_extra,
                )
                return block("split_workflow needs workflow_state.selected_skill before heavy tool use.")
            if policy.get("split_workflow_requires_required_roles") and not workflow_state.get("required_roles"):
                emit_observability_event(
                    payload,
                    event_name="pre_tool_use",
                    status="blocked",
                    reason="split_workflow_missing_required_roles",
                    tool_family=tool_family,
                    normalized_tool_name=tool_name,
                    input_length=input_length,
                    tool_name=tool_name,
                    text_source=text,
                    extra=base_extra,
                )
                return block("split_workflow needs workflow_state.required_roles before heavy tool use.")

    if broad_scan and delegation_signal:
        emit_observability_event(
            payload,
            event_name="pre_tool_use",
            status="allow",
            reason="broad_scan_with_workflow_signal",
            text_source=text,
            tool_name=tool_name,
            tool_family=tool_family,
            normalized_tool_name=tool_name,
            input_length=input_length,
            extra={
                "needed_capabilities": sorted(needed_capabilities),
                "current_turn_capabilities": sorted(capability_signal),
                "pretool_policy": pretool_policy,
            },
        )
        return 0

    if broad_scan:
        if not missing_capabilities:
            emit_observability_event(
                payload,
                event_name="pre_tool_use",
                status="allow",
                reason="broad_scan_with_research_capability",
                text_source=text,
                tool_name=tool_name,
                tool_family=tool_family,
                normalized_tool_name=tool_name,
                input_length=input_length,
                extra={
                    "needed_capabilities": sorted(needed_capabilities),
                    "current_turn_capabilities": sorted(capability_signal),
                    "pretool_policy": pretool_policy,
                },
            )
            return 0
        if pretool_policy == "hard":
            block(
                "Broad repo scanning is noisy work. First make an orchestrator delegation decision "
                "and prefer a researcher subagent for repo mapping. If this is a tiny direct task, "
                "state the exception and retry with a narrower command."
            )
            status = "blocked"
        else:
            status = "warn"
        emit_observability_event(
            payload,
            event_name="pre_tool_use",
            status=status,
            reason="broad_scan_without_research_capability",
            tool_family=tool_family,
            normalized_tool_name=tool_name,
            input_length=input_length,
            tool_name=tool_name,
            text_source=text,
            extra={
                "needed_capabilities": sorted(needed_capabilities),
                "current_turn_capabilities": sorted(capability_signal),
                "pretool_policy": pretool_policy,
            },
        )
        if status == "blocked":
            return 0
        return 0

    if delegation_signal or not missing_capabilities:
        emit_observability_event(
            payload,
            event_name="pre_tool_use",
            status="allow",
            reason="workflow_signal_detected" if delegation_signal else "capability_signal_detected",
            text_source=text,
            tool_name=tool_name,
            tool_family=tool_family,
            normalized_tool_name=tool_name,
            input_length=input_length,
            extra={
                "needed_capabilities": sorted(needed_capabilities),
                "current_turn_capabilities": sorted(capability_signal),
                "pretool_policy": pretool_policy,
            },
        )
        return 0

    if tool_name in {"apply_patch", "edit", "write"}:
        files = patch_file_count(text)
        large_patch = len(text) > 12000 or text.count("\n") > 220
        if files > 1 or large_patch:
            if pretool_policy == "hard":
                block(
                    "This looks like a multi-file or large code edit without a prior workflow signal. "
                    "Split ownership first, or set this project to a softer pretool policy if the main "
                    "thread intentionally handles bounded implementation."
                )
                status = "blocked"
            else:
                status = "warn"
            emit_observability_event(
                payload,
                event_name="pre_tool_use",
                status=status,
                reason="large_patch_without_implementation_capability",
                tool_family=tool_family,
                normalized_tool_name=tool_name,
                input_length=input_length,
                tool_name=tool_name,
                text_source=text,
                extra={
                    "files": files,
                    "large_patch": large_patch,
                    "needed_capabilities": sorted(needed_capabilities),
                    "current_turn_capabilities": sorted(capability_signal),
                    "pretool_policy": pretool_policy,
                },
            )
            if status == "blocked":
                return 0
            return 0

    emit_observability_event(
        payload,
        event_name="pre_tool_use",
        status="allow",
        reason="soft_allow_without_capability_signal",
        tool_name=tool_name,
        tool_family=tool_family,
        normalized_tool_name=tool_name,
        input_length=input_length,
        text_source=text,
        extra={
            "needed_capabilities": sorted(needed_capabilities),
            "current_turn_capabilities": sorted(capability_signal),
            "pretool_policy": pretool_policy,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
