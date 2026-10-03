"""User approval belongs to the Agent; nonprod hooks enforce technical bounds."""
import tempfile
import unittest
from pathlib import Path

from tests.test_critical_action_gate import _payload, _run_gate, _write_transcript_records, _message


class NonprodBoundaryTests(unittest.TestCase):
    def evaluate(self, command, approval="允许，继续推进吧"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = _write_transcript_records(root, [
                _message("assistant", "只在预发应用目录内创建临时代理文件并测试，不修改已有服务。"),
                _message("user", approval),
            ])
            return _run_gate(_payload(command, cwd=root, transcript_path=str(transcript)))

    def test_real_incident_shape(self):
        command = "ssh app-pre-1 'cd /srv/example-app && git rev-parse HEAD && supervisorctl status example-app && ss -lnt \"( sport = :18443 or sport = :13128 )\" && test ! -L .runtime && mkdir -p .runtime/probe/logs && chmod 0755 .runtime/probe .runtime/probe/logs && docker image inspect ubuntu/squid:latest'"
        self.assertEqual(self.evaluate(command)["decision"], "allow")

    def test_routine_directory_preparation(self):
        for operation in ("mkdir -p .runtime/probe", "touch .runtime/probe/ready", "chmod 0644 .runtime/probe/ready"):
            with self.subTest(operation=operation):
                self.assertEqual(self.evaluate("ssh app-pre-1 'cd /srv/example-app && " + operation + "'")["decision"], "allow")

    def test_runtime_file_transport_is_allowed_only_on_registered_nonprod_hosts(self):
        cases = (
            ("scp local-squid.conf app-pre-1:/srv/example-app/.runtime/proxy/squid.conf", "allow"),
            ("rsync -a local-squid.conf app-test-1:/srv/example-app/.runtime/proxy/squid.conf", "allow"),
            ("scp local-squid.conf app-pre-1:/srv/example-app/config.env", "block"),
            ("scp local-squid.conf app-prod-1:/srv/example-app/.runtime/proxy/squid.conf", "block"),
            ("scp local-squid.conf unknown-pre:/srv/example-app/.runtime/proxy/squid.conf", "block"),
        )
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(self.evaluate(command)["decision"], expected)

    def test_runtime_pipe_write_requires_a_bounded_tee_target(self):
        allowed = "ssh app-pre-1 'cd /srv/example-app && base64 -d | sudo tee .runtime/proxy/squid.conf >/dev/null'"
        blocked = "ssh app-pre-1 'cd /srv/example-app && printf config > .runtime/proxy/squid.conf'"
        self.assertEqual(self.evaluate(allowed)["decision"], "allow")
        self.assertEqual(self.evaluate(blocked)["decision"], "block")

    def test_task_runtime_cleanup_allows_explicit_files_and_empty_directories(self):
        command = (
            "ssh app-pre-1 'cd /srv/example-app && "
            "rm .runtime/proxy-run-20260928/nginx.conf .runtime/proxy-run-20260928/logs/access.log && "
            "rmdir .runtime/proxy-run-20260928/logs .runtime/proxy-run-20260928'"
        )
        self.assertEqual(self.evaluate(command)["decision"], "allow")

    def test_task_runtime_cleanup_rejects_recursive_or_non_task_paths(self):
        cases = (
            "rm -rf .runtime/proxy-run-20260928",
            "rm .runtime/proxy-run-20260928",
            "rm .runtime/proxy-run-20260928/*",
            "rm config.env",
            "rmdir .runtime",
        )
        for operation in cases:
            with self.subTest(operation=operation):
                self.assertEqual(
                    self.evaluate("ssh app-pre-1 'cd /srv/example-app && " + operation + "'")["decision"],
                    "block",
                )

    def test_local_stdin_and_stdout_redirection_are_not_misparsed_as_remote_targets(self):
        command = (
            "ssh app-pre-1 'sudo tee /srv/example-app/.runtime/proxy/squid.conf' "
            "< scripts/ops/squid.conf > /dev/null"
        )
        self.assertEqual(self.evaluate(command)["decision"], "allow")

    def test_dangerous_or_outside_scope_remains_blocked(self):
        for operation in ("mkdir -p /etc/probe", "chmod 0777 b", "chmod -R 0755 .", "chmod 4755 b", "chown root b", "rm -rf .", "ln -s /etc b", "cp a b", "mv a b", "printf test > b", "git reset --hard"):
            with self.subTest(operation=operation):
                self.assertEqual(self.evaluate("ssh app-pre-1 'cd /srv/example-app && " + operation + "'")["decision"], "block")

    def test_production_does_not_inherit_nonprod_relaxation(self):
        self.assertEqual(self.evaluate("ssh app-prod-1 'cd /srv/example-app && mkdir -p .runtime/probe'")["decision"], "block")

    def test_prodctl_unknown_service_is_technical_rejection(self):
        result = self.evaluate("prodctl restart --host app-pre-1 --service unrelated")
        self.assertEqual(result["decision"], "block")
        self.assertNotIn("current-turn", result["reason"])

    def test_explicit_stop_is_not_ignored(self):
        self.assertEqual(self.evaluate("ssh app-pre-1 'cd /srv/example-app && mkdir -p .runtime/probe'", "不要执行，先停下")["decision"], "block")
