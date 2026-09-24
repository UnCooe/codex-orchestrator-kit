"""Offline regression for a real short approval; no prodctl is executed."""
from __future__ import annotations

import json
import importlib.util
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("critical_gate_test_helpers", Path(__file__).with_name("test_critical_action_gate.py"))
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
_payload = _helpers._payload
_run_gate = _helpers._run_gate
_write_transcript_records = _helpers._write_transcript_records


# Sanitized response_item structure from the 2026-09-21 09:50-09:52 UTC
# conversation. Tool events and assistant commentary after approval must not
# replace the assistant plan that the user actually saw and approved.
PLAN = """接下来先完成工具修复的最终复审，再执行已批准的发布：
1. 安装受控工具更新，补齐生产两张表的 `terminal_stage`。
2. cn-sz1 同步 `8000e71`，重启第一个实例，观察 5 分钟，核对健康、真实请求和异常日志。
3. 通过后逐实例推进其余服务器，最终核对全部 30 个实例，以及 `30s / 5%` 配置生效情况。

目前唯一执行阻碍是自动审批未识别前文授权。请明确回复一句：**“允许生产改表、同步 8000e71，并按上述顺序滚动重启全部 14 台服务器的 30 个实例。”**

这是自动审批拒绝了重启预演，提示“当前轮授权未匹配环境和动作”；不是服务故障，也不是需要你重新讨论方案。"""


def message(role: str, text: str) -> dict:
    return {"type": "response_item", "payload": {
        "type": "message", "role": role,
        "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
    }}


class ProductionPlanBindingTests(unittest.TestCase):
    def check(self, *, plan=PLAN, approval="ok，继续吧", host="cn-sz1", service="aiserver",
              aliases=None, stale=False, command=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host_policy = {"env": "prod", "services": ["aiserver", "aiserver-2", "aiserver-3"]}
            if aliases is not None:
                host_policy["service_aliases"] = aliases
            policy = {"server_policy": {"hosts": {"cn-sz1": host_policy, "cn-sz2": host_policy},
                        "prodctl": {"required_for_envs": ["prod"]}}}
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            records = [message("user", "那接下来呢？"), message("assistant", plan)]
            if stale:
                records.append(message("user", "先停止发布，只核对日志"))
            records.extend([message("user", approval), {
                "type": "response_item", "payload": {"type": "function_call", "name": "exec", "arguments": "{}"}},
                message("assistant", "继续。我先完成复审。")])
            transcript = _write_transcript_records(root, records)
            return _run_gate(_payload(command or f"./prodctl restart-service --host {host} --service {service}",
                             cwd=root, policy_path=str(policy_path), transcript_path=str(transcript)))

    def test_real_plan_without_declared_mapping_fails_closed(self):
        self.assertEqual(self.check()["decision"], "block")

    def test_fleet_resolved_service_in_displayed_plan_accepts_short_approval(self):
        plan = PLAN.replace("第一个实例", "aiserver")
        self.assertEqual(self.check(plan=plan)["decision"], "allow")

    def test_exact_registered_host_determines_environment(self):
        plan = "通过 prodctl 仅重启 cn-sz1 上的 aiserver 一次，随后验证。"
        self.assertEqual(self.check(plan=plan)["decision"], "allow")
        for other in ("cn-sz2", "unknown"):
            self.assertEqual(self.check(plan=plan, host=other)["decision"], "block")
        self.assertEqual(self.check(plan="预发 " + plan)["decision"], "block")
        self.assertEqual(self.check(plan=plan + " 同时检查 cn-sz2。")["decision"], "block")

    def test_wrong_service_or_host_stays_blocked(self):
        for scope in ({"service": "aiserver-2"}, {"service": "aiserver-3"}, {"host": "cn-sz2"}):
            with self.subTest(scope=scope):
                self.assertEqual(self.check(plan=PLAN.replace("第一个实例", "aiserver"), **scope)["decision"], "block")

    def test_colliding_unknown_or_malformed_aliases_fail_closed(self):
        for aliases in ({"aiserver": ["第一个实例"], "aiserver-2": ["第一个实例"]},
                        {"unknown": ["第一个实例"]}, {"aiserver": "第一个实例"}):
            with self.subTest(aliases=aliases):
                self.assertEqual(self.check(aliases=aliases)["decision"], "block")

    def test_negated_alias_or_approval_does_not_bind(self):
        for scope in ({"plan": PLAN.replace("重启第一个实例", "不要重启第一个实例")},
                      {"approval": "不要继续"},
                      {"plan": "生产 cn-sz1 重启。\ncn-sz2 重启第一个实例。"},
                      {"plan": "生产 cn-sz1 检查，cn-sz2 重启第一个实例。"},
                      {"plan": "生产 cn-sz1 检查第一个实例。\ncn-sz1 重启。"}):
            with self.subTest(scope=scope):
                self.assertEqual(self.check(aliases={"aiserver": ["第一个实例"]}, **scope)["decision"], "block")

    def test_intervening_user_cancels_stale_plan(self):
        self.assertEqual(self.check(plan=PLAN.replace("第一个实例", "aiserver"), stale=True)["decision"], "block")

    def test_service_alias_and_action_must_share_an_affirmative_clause(self):
        for plan in ("生产 cn-sz1 检查第一个实例，重启第二个实例。",
                     "生产 cn-sz1 重启第二个实例，第一个实例不动。",
                     "生产 cn-sz1 重启第一个实例之外的实例。",
                     "生产 cn-sz1 重启除了第一个实例的其他实例。",
                     "生产 cn-sz1 不允许重启第一个实例。",
                     "生产 cn-sz1，重启第一个实例不要执行。",
                     "生产 cn-sz1，重启第一个实例，但不要重启第一个实例。"):
            with self.subTest(plan=plan):
                self.assertEqual(self.check(plan=plan, aliases={"aiserver": ["第一个实例"]})["decision"], "block")

    def test_explicit_production_plan_ref_cannot_change(self):
        plan = "生产 cn-sz1 使用 prodctl deploy --host cn-sz1 --service aiserver --ref master。"
        for ref, expected in (("master", "allow"), ("other", "block"), ("8000e71", "block")):
            with self.subTest(ref=ref):
                self.assertEqual(self.check(plan=plan, command=f"./prodctl deploy --host cn-sz1 --service aiserver --ref {ref}")["decision"], expected)

    def test_batch_rollout_authorization_covers_registered_hosts_and_services(self):
        plan = "授权生产按 cn-sz1 → cn-sz2，全部部署并重启到 master。"
        for host, service in (("cn-sz1", "aiserver"), ("cn-sz1", "aiserver-2"), ("cn-sz2", "aiserver-3")):
            with self.subTest(host=host, service=service):
                result = self.check(
                    plan=plan,
                    approval=plan,
                    host=host,
                    service=service,
                    command=f"./prodctl restart-service --host {host} --service {service}",
                )
                self.assertEqual(result["decision"], "allow", result)

    def test_batch_rollout_does_not_authorize_unlisted_host_or_service(self):
        plan = "授权生产按 cn-sz1 → cn-sz2，全部部署并重启到 master。"
        for host, service in (("cn-sz3", "aiserver"), ("cn-sz1", "unknown-service")):
            with self.subTest(host=host, service=service):
                result = self.check(
                    plan=plan,
                    approval=plan,
                    host=host,
                    service=service,
                    command=f"./prodctl restart-service --host {host} --service {service}",
                )
                self.assertEqual(result["decision"], "block", result)


if __name__ == "__main__":
    unittest.main()
