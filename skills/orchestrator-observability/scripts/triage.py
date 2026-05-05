#!/usr/bin/env python3
"""Standalone proposal triage for orchestrator observability reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


ABS_PATH_RE = re.compile(
    r"([a-zA-Z]:[\\/])|(\\\\[^\\/\s]+[\\/][^\\/\s]+)|(/Users/)|(/home/)|(/tmp/)|(/workspace/)|(/var/)"
)
CURRENT_HIGH_ALERT = Path("alerts") / "high-risk-current.json"
HIGH_ALERT_ARCHIVE = Path("alerts")
PROPOSAL_ROOT = Path("proposals")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify orchestrator-observability reports and stage triage state."
    )
    parser.add_argument("--codex-home", required=True)
    parser.add_argument("--since-days", type=int, required=True)
    parser.add_argument("--report", default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _read_json(path: Path) -> Dict:
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def _to_int(value: object) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _safe_ratio(n: int, d: int) -> float:
    if d <= 0:
        return 0.0
    return float(n) / float(d)


def _parse_report_timestamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _within_window(path: Path, since_days: int) -> bool:
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            payload = json.load(stream)
        generated_at = _parse_report_timestamp(payload.get("generated_at"))
        if generated_at is None:
            generated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except Exception:
        generated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    return generated_at >= cutoff


def _choose_report(reports_root: Path, report_arg: Optional[str], since_days: int) -> Path:
    if report_arg:
        p = Path(report_arg).expanduser()
        if not p.is_absolute():
            p = reports_root / p
        if not p.exists():
            raise FileNotFoundError(f"report file not found: {p}")
        return p

    candidates = sorted(reports_root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    recent = [p for p in candidates if _within_window(p, since_days)]
    if recent:
        return recent[0]
    if not candidates:
        raise FileNotFoundError(f"no report found under {reports_root}")
    return candidates[0]


def _collect_path_values(value: object, out: List[str]) -> None:
    if isinstance(value, str):
        if ABS_PATH_RE.search(value):
            out.append(value)
    elif isinstance(value, list):
        for item in value:
            _collect_path_values(item, out)
    elif isinstance(value, dict):
        for k, item in value.items():
            _collect_path_values(item, out)


def _scan_for_privacy_leak(report: Dict) -> Tuple[bool, int]:
    hits: List[str] = []
    _collect_path_values(report, hits)
    return (len(hits) > 0, len(hits))


def _contains_automation_failure_marker(report: Dict) -> bool:
    if "automation_failure_marker" in report:
        return bool(report.get("automation_failure_marker"))
    for source in (report.get("totals", {}), report.get("noise", {}), report.get("metrics", {})):
        if isinstance(source, dict) and source.get("automation_failure_marker") is not None:
            return bool(source.get("automation_failure_marker"))
    return False


def _safe_low_risk_patch(patch_path: Path) -> bool:
    changed = _changed_files_from_patch(patch_path)
    if not changed:
        return False

    allowed_ext = {".md", ".markdown", ".txt"}
    allowed_dir_prefix = (
        "docs/",
        "templates/",
        "template/",
    )
    disallowed_tokens = (
        ".codex/",
        ".agents/",
        "automations/",
        "/scripts/",
        "scripts/",
        ".py",
        "hooks/",
        "hook",
        "install.",
        "uninstall.",
        "tests/",
    )

    for path in changed:
        lowered = path.lower().replace("\\", "/")
        if any(token in lowered for token in disallowed_tokens):
            return False
        if lowered.startswith(allowed_dir_prefix):
            continue
        if lowered in {"readme.md", "skill.md", "agents.md"}:
            continue
        if lowered.endswith(".md"):
            continue
        if any(lowered.endswith(ext) for ext in allowed_ext):
            continue
        return False
    return True


def _find_concrete_patch(report_path: Path) -> Optional[Path]:
    candidates = []
    try:
        payload = _read_json(report_path)
    except Exception:
        payload = {}
    for key in ("patch_path", "patch", "proposal_patch", "patch.diff", "patch_diff"):
        value = payload.get(key)
        if isinstance(value, str):
            candidates.append(Path(value))
    candidates.append(report_path.with_suffix(".patch.diff"))
    candidates.append(report_path.with_name(f"{report_path.stem}.patch"))
    candidates.append(report_path.parent / "patch.diff")
    for candidate in candidates:
        candidate = candidate.expanduser()
        if not candidate.is_absolute():
            candidate = report_path.parent / candidate
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _actionable_suggestions(report: Dict) -> List[str]:
    suggestions = report.get("suggestions", []) or []
    return [
        str(item).strip()
        for item in suggestions
        if isinstance(item, str) and item.strip() and not item.strip().lower().startswith("no critical")
    ]


def classify(report: Dict) -> Tuple[str, List[str], Dict[str, object]]:
    totals = report.get("totals", {}) if isinstance(report.get("totals", {}), dict) else {}
    subagents = report.get("subagents", {}) if isinstance(report.get("subagents", {}), dict) else {}
    acceptance = report.get("acceptance", {}) if isinstance(report.get("acceptance", {}), dict) else {}
    orchestrator_events = _to_int(totals.get("orchestrator_events", 0))
    blocked_events = _to_int(totals.get("blocked_orchestrator_events", 0))
    blocked_ratio = _safe_ratio(blocked_events, orchestrator_events)
    lifecycle_delta = _to_int(subagents.get("lifecycle_delta_spawn_minus_close", 0))
    missing_verification = _to_int(acceptance.get("work_done_missing_verification", 0))
    missing_subagent_status = _to_int(acceptance.get("work_done_missing_subagent_status", 0))

    privacy_leak, privacy_hits = _scan_for_privacy_leak(report)
    automation_failure = _contains_automation_failure_marker(report)
    high_block_ratio = orchestrator_events >= 4 and blocked_ratio >= 0.3
    repeat_acceptance_blocks = (missing_verification + missing_subagent_status) >= 3 or lifecycle_delta >= 3
    high_close_delta = lifecycle_delta >= 4

    flags = {
        "orchestrator_events": orchestrator_events,
        "blocked_orchestrator_events": blocked_events,
        "blocked_ratio": round(blocked_ratio, 4),
        "lifecycle_delta_spawn_minus_close": lifecycle_delta,
        "work_done_missing_verification": missing_verification,
        "work_done_missing_subagent_status": missing_subagent_status,
        "privacy_hit_count": privacy_hits,
        "automation_failure_marker": automation_failure,
        "high_blocked_ratio": high_block_ratio,
        "repeated_acceptance_or_lifecycle_blocks": repeat_acceptance_blocks,
        "high_close_delta": high_close_delta,
        "actionable_suggestion_count": len(_actionable_suggestions(report)),
    }

    if privacy_leak or automation_failure or high_block_ratio or repeat_acceptance_blocks or high_close_delta:
        return "high", ["high_risk"], flags

    actionable = _actionable_suggestions(report)
    if actionable:
        return "candidate_low", ["actionable_suggestions_present"], flags

    return "none", [], flags


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _evidence_hash(reasons: List[str], flags: Dict[str, object], report: Dict) -> str:
    payload = {
        "reasons": reasons,
        "flags": flags,
        "suggestions": _actionable_suggestions(report)[:5],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _changed_files_from_patch(patch_path: Path) -> List[str]:
    try:
        text = patch_path.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception:
        return []
    changed: List[str] = []
    for line in text.splitlines():
        if line.startswith("diff --git a/"):
            parts = line.split()
            if len(parts) >= 3:
                path = parts[2][2:] if parts[2].startswith("a/") else parts[2]
                changed.append(path)
        elif line.startswith("*** Update File: ") or line.startswith("*** Add File: ") or line.startswith("*** Delete File: "):
            changed.append(line.split(": ", 1)[1].strip())
    return sorted(set(changed))


def _high_risk_title(flags: Dict[str, object]) -> str:
    if _to_int(flags.get("privacy_hit_count", 0)) > 0:
        return "报告可能泄漏本机路径或敏感路径信息"
    if flags.get("automation_failure_marker"):
        return "观测自动化可能失败"
    if flags.get("high_blocked_ratio"):
        return "hook 阻断比例偏高，可能存在误伤"
    if flags.get("repeated_acceptance_or_lifecycle_blocks"):
        return "Stop gate 或 subagent 生命周期异常反复出现"
    if flags.get("high_close_delta"):
        return "subagent 关闭数量落后，可能占用槽位"
    return "Orchestrator 观测发现高风险问题"


def _safe_evidence_lines(flags: Dict[str, object]) -> List[str]:
    lines = []
    if flags.get("high_blocked_ratio"):
        lines.append(
            f"hook 阻断比例 {flags.get('blocked_ratio')} ({flags.get('blocked_orchestrator_events')}/{flags.get('orchestrator_events')})"
        )
    if flags.get("repeated_acceptance_or_lifecycle_blocks"):
        lines.append(
            "验收/生命周期异常 "
            f"verification={flags.get('work_done_missing_verification')}, "
            f"subagents={flags.get('work_done_missing_subagent_status')}, "
            f"delta={flags.get('lifecycle_delta_spawn_minus_close')}"
        )
    if _to_int(flags.get("privacy_hit_count", 0)) > 0:
        lines.append(f"隐私路径命中 {flags.get('privacy_hit_count')} 次")
    if flags.get("automation_failure_marker"):
        lines.append("自动化失败标记出现")
    if not lines:
        lines.append("存在需要人工处理的高风险信号")
    return lines[:3]


def _write_high_alert(triage_root: Path, report_path: Path, reasons: List[str], flags: Dict[str, object], evidence_hash: str, dry_run: bool) -> Path:
    alert_path = triage_root / CURRENT_HIGH_ALERT
    archive_path = triage_root / HIGH_ALERT_ARCHIVE / f"{evidence_hash}.json"
    preserved: Dict[str, object] = {}
    try:
        existing = json.loads(alert_path.read_text(encoding="utf-8"))
        if existing.get("evidence_hash") == evidence_hash:
            for key in ("last_reminded_at", "reminder_count"):
                if key in existing:
                    preserved[key] = existing[key]
    except Exception:
        pass

    alert_state = {
        "id": f"high-{evidence_hash[:12]}",
        "status": "pending",
        "severity": "high",
        "title": _high_risk_title(flags),
        "summary": "Orchestrator 观测发现高风险信号，需要确认处理方向。",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "evidence_hash": evidence_hash,
        "report_name": report_path.name,
        "reasons": reasons,
        "evidence": _safe_evidence_lines(flags),
        "flags": flags,
        **preserved,
    }
    if not dry_run:
        _write_json(alert_path, alert_state)
        _write_json(archive_path, alert_state)
    return alert_path


def _write_proposal(
    triage_root: Path,
    report_path: Path,
    patch_path: Path,
    reasons: List[str],
    flags: Dict[str, object],
    evidence_hash: str,
    dry_run: bool,
) -> Path:
    proposal_id = f"{report_path.stem}-{evidence_hash[:8]}"
    proposal_path = triage_root / PROPOSAL_ROOT / proposal_id
    changed_files = _changed_files_from_patch(patch_path)
    evidence = _safe_evidence_lines(flags)
    if flags.get("actionable_suggestion_count"):
        evidence.append(f"可执行建议 {flags.get('actionable_suggestion_count')} 条")
    evidence = evidence[:3]

    manifest = {
        "id": proposal_id,
        "status": "pending",
        "severity": "low",
        "risk_level": "low",
        "created_at": _now_iso(),
        "evidence_hash": evidence_hash,
        "report_name": report_path.name,
        "target_files": changed_files,
        "evidence": evidence,
        "reasons": reasons,
        "reply_options": ["应用", "延后", "忽略", "展开依据"],
    }
    proposal_md = "\n".join(
        [
            "# Orchestrator 低风险变更确认",
            "",
            "推荐操作：应用本期低风险 patch。",
            "",
            "为什么：",
            *[f"- {line}" for line in evidence],
            "",
            "将改变：",
            *[f"- {path}" for path in (changed_files or ["文档、模板或提示词指导"] )],
            "",
            "风险：低。只允许文档、模板或非阻断指导类 patch。",
            "",
            "回滚：使用安装器备份或丢弃该 proposal。",
            "",
            "你只需要回复：应用 / 延后 / 忽略 / 展开依据",
        ]
    )
    validation_md = "\n".join(
        [
            "# Validation",
            "",
            "- Run `python -m py_compile` for observability scripts if Python files are touched by the application path.",
            "- Run `powershell -NoProfile -ExecutionPolicy Bypass -File .\\tests\\smoke-test.ps1`.",
            "- Run `bash ./tests/smoke-test.sh` when Bash is available.",
            "- Reinstall with `./install.ps1` and verify orchestrator hooks are not duplicated.",
        ]
    )
    rollback_md = "\n".join(
        [
            "# Rollback",
            "",
            "- Do not apply `patch.diff` if the evidence no longer matches the current report.",
            "- If applied through the kit installer, restore from the timestamped `.bak.*` files or rerun uninstall/install after reverting source changes.",
        ]
    )

    if not dry_run:
        proposal_path.mkdir(parents=True, exist_ok=True)
        shutil.copy2(patch_path, proposal_path / "patch.diff")
        (proposal_path / "proposal.md").write_text(proposal_md + "\n", encoding="utf-8")
        (proposal_path / "validation.md").write_text(validation_md + "\n", encoding="utf-8")
        (proposal_path / "rollback.md").write_text(rollback_md + "\n", encoding="utf-8")
        _write_json(proposal_path / "manifest.json", manifest)
    return proposal_path


def main() -> None:
    args = parse_args()
    codex_home = Path(args.codex_home).expanduser()
    reports_root = codex_home / "orchestrator-observability" / "reports"
    triage_root = codex_home / "orchestrator-observability" / "triage"
    report_path = _choose_report(reports_root, args.report, args.since_days)
    report = _read_json(report_path)

    severity, reasons, flags = classify(report)
    evidence_hash = _evidence_hash(reasons, flags, report)
    ignored_path = triage_root / "ignored" / f"{evidence_hash}.json"
    if ignored_path.exists():
        severity = "none"
        reasons = ["ignored_evidence_hash"]

    if severity == "candidate_low":
        patch_path = _find_concrete_patch(report_path)
        if patch_path and patch_path.exists():
            if _safe_low_risk_patch(patch_path):
                severity = "low"
            else:
                severity = "none"
                reasons = ["non_low_risk_or_missing_patch"]
        else:
            severity = "none"
            reasons = ["no_concrete_patch_found"]

    if severity == "high":
        alert_path = triage_root / CURRENT_HIGH_ALERT
        triage_path = triage_root / "state.json"
        proposal_path = None
        _write_high_alert(triage_root, report_path, reasons, flags, evidence_hash, args.dry_run)
    elif severity == "low":
        patch_path = _find_concrete_patch(report_path)
        proposal_path = triage_root / PROPOSAL_ROOT / f"{report_path.stem}-{evidence_hash[:8]}"
        alert_path = None
        triage_path = proposal_path / "manifest.json"
        if patch_path is not None:
            _write_proposal(triage_root, report_path, patch_path, reasons, flags, evidence_hash, args.dry_run)
    else:
        alert_path = None
        proposal_path = None
        triage_path = triage_root / "archive" / f"{report_path.stem}-{evidence_hash[:8]}.json"

    if not args.dry_run:
        triage_state = {
            "severity": severity,
            "report_name": report_path.name,
            "since_days": args.since_days,
            "created_at": _now_iso(),
            "evidence_hash": evidence_hash,
            "flags": flags,
            "reasons": reasons,
            "state": "pending" if severity in {"high", "low"} else "archived",
        }
        if severity != "low":
            _write_json(triage_path, triage_state)

    result = {
        "severity": severity,
        "triage_path": str(triage_path),
        "summary": {
            "reasons": reasons,
            "flags": flags,
            "report_name": report_path.name,
            "evidence_hash": evidence_hash,
            "dry_run": args.dry_run,
        },
    }
    if proposal_path:
        result["proposal_path"] = str(proposal_path)
    if alert_path:
        result["alert_path"] = str(alert_path)

    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
