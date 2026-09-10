#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import sys
from pathlib import Path


MANAGED_SKILLS = (
    "orchestrator",
    "orchestrator-observability",
    "feature-workflow",
    "review-workflow",
    "merge-workflow",
    "predeploy-workflow",
    "merge-safety",
)


def ignored(path: Path) -> bool:
    return any(part == "__pycache__" or fnmatch.fnmatch(part, "*.bak.*") for part in path.parts) or fnmatch.fnmatch(path.name, "*.pyc")


def ignored_agent(path: Path) -> bool:
    return fnmatch.fnmatch(path.name, "*.bak.*")


def regular_files(root: Path, skill: str) -> dict[str, str]:
    skill_root = root / skill
    if not skill_root.is_dir():
        return {}
    files: dict[str, str] = {}
    for path in sorted(skill_root.rglob("*")):
        rel = path.relative_to(root)
        if ignored(rel):
            continue
        if path.is_file():
            files[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def symlink_failures(root: Path, label: str) -> list[str]:
    failures: list[str] = []
    if root.is_symlink():
        failures.append(f"{label} skills root is a symlink: {root}")
        return failures

    for skill in MANAGED_SKILLS:
        skill_root = root / skill
        if skill_root.is_symlink():
            failures.append(f"{label} managed skill directory is a symlink: {skill_root}")
            continue
        if not skill_root.exists():
            continue
        for path in sorted(skill_root.rglob("*")):
            if path.is_symlink():
                rel = path.relative_to(root)
                failures.append(f"{label} managed skill file is a symlink: {rel.as_posix()}")
    return failures


def compare(source: Path, target: Path) -> list[str]:
    failures: list[str] = []
    failures.extend(symlink_failures(target, "target"))
    if failures:
        return failures
    for skill in MANAGED_SKILLS:
        source_dir = source / skill
        target_dir = target / skill
        if not source_dir.is_dir():
            failures.append(f"missing source managed skill directory: {skill}")
            continue
        if not target_dir.is_dir():
            failures.append(f"missing target managed skill directory: {skill}")
            continue

        source_files = regular_files(source, skill)
        target_files = regular_files(target, skill)
        missing = sorted(set(source_files) - set(target_files))
        extra = sorted(set(target_files) - set(source_files))
        for rel in missing:
            failures.append(f"missing target file: {rel}")
        for rel in extra:
            failures.append(f"extra target file: {rel}")
        for rel in sorted(set(source_files) & set(target_files)):
            if source_files[rel] != target_files[rel]:
                failures.append(
                    f"SHA-256 mismatch: {rel} source={source_files[rel]} target={target_files[rel]}"
                )
    return failures


def managed_agent_files(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    if not root.is_dir():
        return files
    for path in sorted(root.glob("*.toml")):
        rel = path.name
        if ignored_agent(path):
            continue
        if path.is_symlink():
            continue
        if path.is_file():
            files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def compare_agents(source: Path, target: Path) -> list[str]:
    failures: list[str] = []
    if target.is_symlink():
        failures.append(f"target agents root is a symlink: {target}")
        return failures
    if not source.is_dir():
        failures.append(f"missing source agents directory: {source}")
        return failures
    if not target.is_dir():
        failures.append(f"missing target agents directory: {target}")
        return failures

    source_files = managed_agent_files(source)
    for rel, source_hash in source_files.items():
        target_path = target / rel
        if target_path.is_symlink():
            failures.append(f"target managed agent file is a symlink: {rel}")
            continue
        if not target_path.is_file():
            failures.append(f"missing target agent: {rel}")
            continue
        target_hash = hashlib.sha256(target_path.read_bytes()).hexdigest()
        if source_hash != target_hash:
            failures.append(f"SHA-256 mismatch: {rel} source={source_hash} target={target_hash}")
    return failures


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Verify installed managed Codex skills.")
    parser.add_argument("--source", required=True, type=Path, help="Source skills root.")
    parser.add_argument("--target", required=True, type=Path, help="Installed skills root.")
    parser.add_argument("--source-agents", type=Path, help="Source managed agents root.")
    parser.add_argument("--target-agents", type=Path, help="Installed managed agents root.")
    args = parser.parse_args(argv)

    failures = compare(args.source.resolve(), args.target.absolute())
    if bool(args.source_agents) != bool(args.target_agents):
        failures.append("--source-agents and --target-agents must be provided together")
    elif args.source_agents and args.target_agents:
        failures.extend(compare_agents(args.source_agents.resolve(), args.target_agents.absolute()))
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1

    if args.source_agents and args.target_agents:
        agent_count = len(managed_agent_files(args.source_agents.resolve()))
        print(f"verified {len(MANAGED_SKILLS)} managed skill directories and {agent_count} managed agents")
    else:
        print(f"verified {len(MANAGED_SKILLS)} managed skill directories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
