#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "hooks" / "critical_action_gate.py"


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot compute percentile of empty sample")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _summary_ms(values: list[float]) -> dict[str, float]:
    return {
        "p50": round(statistics.median(values) * 1000, 3),
        "p95": round(_percentile(values, 0.95) * 1000, 3),
        "max": round(max(values) * 1000, 3),
    }


def _run_once(command: list[str], *, stdin: str, env: dict[str, str]) -> tuple[float, subprocess.CompletedProcess[str]]:
    start = time.perf_counter()
    proc = subprocess.run(
        command,
        input=stdin,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    elapsed = time.perf_counter() - start
    return elapsed, proc


def _assert_silent_allow(proc: subprocess.CompletedProcess[str]) -> None:
    if proc.returncode != 0 or proc.stdout != "" or proc.stderr != "":
        raise AssertionError(
            "critical_action_gate fast path must silently allow; "
            f"returncode={proc.returncode}, stdout={proc.stdout!r}, stderr={proc.stderr!r}"
        )


def run_benchmark(*, iterations: int, warmup: int) -> dict[str, Any]:
    if iterations < 1:
        raise ValueError("iterations must be >= 1")
    if warmup < 0:
        raise ValueError("warmup must be >= 0")
    if not GATE.is_file():
        raise FileNotFoundError(f"missing critical action gate: {GATE}")

    with tempfile.TemporaryDirectory(prefix="critical-gate-bench.") as temp_dir:
        root = Path(temp_dir)
        codex_home = root / "codex-home"
        codex_home.mkdir()
        cwd = root / "repo"
        cwd.mkdir()
        payload = json.dumps(
            {
                "cwd": str(cwd),
                "tool_name": "Bash",
                "tool_input": {"cmd": "python -V"},
            }
        )
        env = {
            **dict(os.environ),
            "CODEX_HOME": str(codex_home),
        }
        gate_command = [sys.executable, str(GATE)]
        baseline_command = [sys.executable, "-c", ""]

        for _ in range(warmup):
            _, gate_proc = _run_once(gate_command, stdin=payload, env=env)
            _assert_silent_allow(gate_proc)
            _, baseline_proc = _run_once(baseline_command, stdin=payload, env=env)
            if baseline_proc.returncode != 0:
                raise AssertionError(f"baseline Python exited {baseline_proc.returncode}: {baseline_proc.stderr!r}")

        gate_samples: list[float] = []
        baseline_samples: list[float] = []
        for _ in range(iterations):
            elapsed, gate_proc = _run_once(gate_command, stdin=payload, env=env)
            _assert_silent_allow(gate_proc)
            gate_samples.append(elapsed)

            elapsed, baseline_proc = _run_once(baseline_command, stdin=payload, env=env)
            if baseline_proc.returncode != 0:
                raise AssertionError(f"baseline Python exited {baseline_proc.returncode}: {baseline_proc.stderr!r}")
            baseline_samples.append(elapsed)

    gate_summary = _summary_ms(gate_samples)
    baseline_summary = _summary_ms(baseline_samples)
    return {
        "python": sys.executable,
        "gate": str(GATE),
        "iterations": iterations,
        "warmup": warmup,
        "payload": {"tool_name": "Bash", "cmd": "python -V"},
        "unit": "ms",
        "fast_path": gate_summary,
        "baseline": baseline_summary,
        "overhead": {
            "p50": round(gate_summary["p50"] - baseline_summary["p50"], 3),
            "p95": round(gate_summary["p95"] - baseline_summary["p95"], 3),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark critical_action_gate.py ordinary Bash fast-path latency.")
    parser.add_argument("--iterations", type=int, default=60, help="measured iterations; default: 60")
    parser.add_argument("--warmup", type=int, default=10, help="warmup iterations before measuring; default: 10")
    args = parser.parse_args()

    result = run_benchmark(iterations=args.iterations, warmup=args.warmup)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
