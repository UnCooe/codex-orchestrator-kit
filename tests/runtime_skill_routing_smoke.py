#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.144.1"
EXPECTED_SKILLS = {
    "orchestrator",
    "orchestrator-observability",
    "feature-workflow",
    "review-workflow",
    "merge-workflow",
    "predeploy-workflow",
    "merge-safety",
}
HIDDEN_LEAVES = {
    "feature-workflow",
    "review-workflow",
    "merge-workflow",
    "predeploy-workflow",
    "merge-safety",
}
PROMPT_MARKER = "FEATURE_WORKFLOW_RUNTIME_BODY_MARKER"
FEATURE_BODY_MARKER = "workflow_state.selected_skill=feature"
IMPLICIT_MANAGED_SKILLS = {"orchestrator", "orchestrator-observability"}


def _codex_version() -> str | None:
    exe = shutil.which("codex")
    if not exe:
        return None
    proc = subprocess.run([exe, "--version"], text=True, capture_output=True)
    if proc.returncode != 0:
        return None
    for token in proc.stdout.strip().split():
        if token[0:1].isdigit():
            return token
    return proc.stdout.strip() or None


def _copy_catalog(catalog_home: Path) -> None:
    skills_root = catalog_home / "skills"
    skills_root.mkdir(parents=True)
    for name in EXPECTED_SKILLS:
        shutil.copytree(ROOT / "skills" / name, skills_root / name)


def _assert_catalog(catalog_home: Path) -> None:
    actual = {path.name for path in (catalog_home / "skills").iterdir() if path.is_dir()}
    if actual != EXPECTED_SKILLS:
        raise AssertionError(f"runtime skill catalog mismatch: {sorted(actual)}")


class _CaptureResponsesHandler(BaseHTTPRequestHandler):
    request_bodies: list[bytes] = []
    request_event = threading.Event()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        self.__class__.request_bodies.append(self.rfile.read(length))
        self.__class__.request_event.set()
        self.send_response(500)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":{"message":"intentional mock failure"}}')

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _write_mock_config(target_home: Path, base_url: str) -> None:
    (target_home / "config.toml").write_text(
        f"""
model = "mock-responses"
model_provider = "local-mock"
approval_policy = "never"
disable_response_storage = true
web_search = "disabled"

[features]
hooks = false

[model_providers.local-mock]
name = "local-mock"
base_url = "{base_url}"
env_key = "CODEX_ORCHESTRATOR_MOCK_API_KEY"
wire_api = "responses"
request_max_retries = 0
stream_max_retries = 0
""".lstrip(),
        encoding="utf-8",
    )


def _runtime_env(codex_home: Path, agents_home: Path) -> dict[str, str]:
    env = os.environ.copy()
    isolated_home = codex_home.parent
    env["HOME"] = str(isolated_home)
    env["CODEX_HOME"] = str(codex_home)
    env["AGENTS_HOME"] = str(agents_home)
    env["CODEX_DISABLE_PLUGINS"] = "1"
    env["CODEX_DISABLE_FEATURED_PLUGINS"] = "1"
    return env


def _render_prompt_input(codex_home: Path, agents_home: Path) -> str:
    exe = shutil.which("codex")
    if not exe:
        raise AssertionError("codex executable disappeared after version gate")
    proc = subprocess.run(
        [exe, "debug", "prompt-input", "router-first catalog smoke"],
        cwd=str(codex_home),
        env=_runtime_env(codex_home, agents_home),
        text=True,
        capture_output=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise AssertionError(f"codex debug prompt-input failed: {proc.stderr.strip()}")
    return proc.stdout


def _run_mocked_feature_workflow(codex_home: Path, agents_home: Path, base_url: str) -> None:
    exe = shutil.which("codex")
    if not exe:
        raise AssertionError("codex executable disappeared after version gate")
    _write_mock_config(codex_home, base_url)
    env = _runtime_env(codex_home, agents_home)
    env["CODEX_ORCHESTRATOR_MOCK_API_KEY"] = "mock-key"
    prompt = f"Use $feature-workflow. Include {PROMPT_MARKER} in the answer."
    proc = subprocess.run(
        [
            exe,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            prompt,
        ],
        cwd=str(codex_home),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if not _CaptureResponsesHandler.request_event.wait(timeout=2):
        raise AssertionError(f"mock Responses endpoint did not receive a request: {proc.stderr.strip()}")


def _iter_strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)


def _assert_implicit_catalog(payload_text: str) -> None:
    payload = json.loads(payload_text)
    catalog_sections = [
        value
        for value in _iter_strings(payload)
        if "<skills_instructions>" in value and "</skills_instructions>" in value
    ]
    if not catalog_sections:
        raise AssertionError("Codex input did not include <skills_instructions>")
    catalog_names: set[str] = set()
    for section in catalog_sections:
        catalog_names.update(re.findall(r"(?m)^- ([a-z0-9][a-z0-9:-]*):", section))
    managed_catalog = catalog_names & EXPECTED_SKILLS
    if managed_catalog != IMPLICIT_MANAGED_SKILLS:
        raise AssertionError(f"unexpected managed implicit catalog: {sorted(managed_catalog)}")


def _assert_feature_workflow_request(body: str) -> None:
    payload = json.loads(body)
    model_text = "\n".join(_iter_strings(payload))
    if PROMPT_MARKER not in model_text:
        raise AssertionError("mock Responses request did not include prompt body marker")
    if not re.search(r"<skill>\s*<name>feature-workflow</name>", model_text):
        raise AssertionError("mock Responses request did not include explicit feature-workflow skill")
    if "# Feature Workflow" not in model_text or FEATURE_BODY_MARKER not in model_text:
        raise AssertionError("mock Responses request did not include the complete feature-workflow body")
    present_hidden = {
        name for name in HIDDEN_LEAVES if re.search(rf"<skill>\s*<name>{re.escape(name)}</name>", model_text)
    }
    if present_hidden != {"feature-workflow"}:
        raise AssertionError(f"unexpected hidden leaf skills in initial request: {sorted(present_hidden)}")
    skill_names = set(re.findall(r"<skill>\s*<name>([^<]+)</name>", model_text))
    unexpected_hidden = skill_names & (HIDDEN_LEAVES - {"feature-workflow"})
    if unexpected_hidden:
        raise AssertionError(f"unexpected hidden <skill> blocks: {sorted(unexpected_hidden)}")

    _assert_implicit_catalog(body)


def _exercise_catalog_mode(temp_root: Path, catalog_mode: str) -> None:
    runtime_root = temp_root / catalog_mode
    codex_home = (runtime_root / ".codex").resolve()
    agents_home = (runtime_root / ".agents").resolve()
    codex_home.mkdir(parents=True)
    agents_home.mkdir(parents=True)
    catalog_home = codex_home if catalog_mode == "codex" else agents_home
    _copy_catalog(catalog_home)
    _assert_catalog(catalog_home)
    _assert_implicit_catalog(_render_prompt_input(codex_home, agents_home))

    _CaptureResponsesHandler.request_bodies = []
    _CaptureResponsesHandler.request_event = threading.Event()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureResponsesHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        _run_mocked_feature_workflow(codex_home, agents_home, f"http://{host}:{port}/v1")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    if not _CaptureResponsesHandler.request_bodies:
        raise AssertionError(f"mock Responses endpoint did not capture a request in {catalog_mode} mode")
    _assert_feature_workflow_request(_CaptureResponsesHandler.request_bodies[0].decode("utf-8", errors="replace"))


def main() -> int:
    version = _codex_version()
    if version != EXPECTED_VERSION:
        print(f"FAIL: codex-cli {version or 'not found'} is not {EXPECTED_VERSION}")
        return 2

    with tempfile.TemporaryDirectory(prefix="codex-skill-routing-") as temp_dir:
        for catalog_mode in ("codex", "agents"):
            _exercise_catalog_mode(Path(temp_dir), catalog_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
