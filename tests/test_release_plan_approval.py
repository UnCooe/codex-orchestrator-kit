"""Offline gate regression for the frozen 2026-09-21 rollout approval."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "release_gate_test_helpers", Path(__file__).with_name("test_critical_action_gate.py")
)
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)

# Exact displayed plan at 10:48:48.920Z; no credentials or live endpoints.
PLAN = """已核对：master 仍是 `8000e71`，没有版本漂移；cn-sz1 仍为 `d507fdf`。工具修复已通过测试和复审，但尚未提交、安装到服务器。本轮没有生产写入。

当前发布计划：

1. 提交工具修复，通过受控入口安装 wrapper，保留原策略及回滚备份。
2. 给生产 `midoo.ai_performance`、`midoo.asr_performance` 补齐 `terminal_stage`，验证字段。
3. **cn-sz1** 按正常 `master` 路径同步，校验目标必须为 `8000e71`；仅重启 **`aiserver`**，观察 5 分钟。
4. 验证通过后，依次重启 cn-sz1 的 **`aiserver-2`、`aiserver-3`**。
5. 按 **cn-sz2 → cn-sz3 → cn-sz4 → cn-sz5 → cn-sz6 → cn-sz7 → cn-sz8** 推进，每台依次重启 **`aiserver`、`aiserver-2`、`aiserver-3`**。
6. 再按 **sz → cn1 → cn2 → sg → jp → us**，逐台同步并重启 **`aiserver`**。

每步检查健康和异常，失败即暂停；最终核对 30 个实例及收紧参数生效。门禁修复已在独立实验验证，真实生产发布仍待执行验证。

确认后我按这份计划推进，你自然语言确认即可。"""
APPROVAL = "可以 开始吧\n"
DEPLOY = "./prodctl deploy --host cn-sz1 --ref master --expected-head 8000e710000000000000000000000000000000000"


def message(role: str, text: str) -> dict:
    return {"type": "response_item", "payload": {
        "type": "message", "role": role,
        "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
    }}


class ReleasePlanApprovalTests(unittest.TestCase):
    def check(self, command=DEPLOY, *, plan=PLAN, approval=APPROVAL):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = json.loads(_helpers.POLICY.read_text(encoding="utf-8"))
            hosts = policy["server_policy"]["hosts"]
            for host in [f"cn-sz{i}" for i in range(1, 9)] + ["sz", "cn1", "cn2", "sg", "jp", "us"]:
                hosts[host] = {"env": "prod", "services": ["aiserver", "aiserver-2", "aiserver-3"]}
            path = root / "policy.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            records = [message("user", "请核对并给出发布计划"), message("assistant", plan)]
            if approval is not None:
                records.append(message("user", approval))
            transcript = _helpers._write_transcript_records(root, records)
            return _helpers._run_gate(_helpers._payload(
                command, cwd=root, policy_path=str(path), transcript_path=str(transcript)
            ))

    def test_exact_frozen_plan_accepts_current_short_approval_for_master_deploy(self):
        for approval in (APPROVAL, "ok 开始吧"):
            with self.subTest(approval=approval):
                result = self.check(approval=approval)
                self.assertEqual(result["decision"], "allow", result)

    def test_exact_frozen_plan_accepts_all_three_cn_sz1_service_restarts(self):
        for service in ("aiserver", "aiserver-2", "aiserver-3"):
            with self.subTest(service=service):
                result = self.check(f"./prodctl restart-service --host cn-sz1 --service {service}")
                self.assertEqual(result["decision"], "allow", result)

    def test_current_denial_overrides_plan(self):
        for approval in ("不要开始", "可以，但别重启", "先别执行"):
            with self.subTest(approval=approval):
                result = self.check("./prodctl restart-service --host cn-sz1 --service aiserver", approval=approval)
                self.assertEqual(result["decision"], "block", result)

    def test_no_current_approval_blocks(self):
        result = self.check(approval=None)
        self.assertEqual(result["decision"], "block", result)

    def test_unknown_host_blocks(self):
        result = self.check(DEPLOY.replace("cn-sz1", "cn-sz99"))
        self.assertEqual(result["decision"], "block", result)

    def test_wrong_ref_blocks(self):
        for ref in ("other", "8000e71"):
            with self.subTest(ref=ref):
                result = self.check(DEPLOY.replace("--ref master", f"--ref {ref}"))
                self.assertEqual(result["decision"], "block", result)

    def test_explicit_conflicting_environment_is_not_ignored_as_test_evidence(self):
        result = self.check(plan=PLAN + "\n本次 cn-sz1 部署的目标环境为测试环境。")
        self.assertEqual(result["decision"], "block", result)

    def test_excluded_environment_mentions_do_not_break_selected_plan_binding(self):
        result = self.check(plan=PLAN + "\n本次只操作 cn-sz1，不会操作 test/pre、业务服务或保护设施。")
        self.assertEqual(result["decision"], "allow", result)

    def test_quoted_approval_explanation_is_not_current_authorization(self):
        for approval in ('“可以 开始吧”这句话是什么意思？', '用户之前说过“可以 开始吧”，这里只是在解释历史记录。'):
            with self.subTest(approval=approval):
                result = self.check(approval=approval)
                self.assertEqual(result["decision"], "block", result)


if __name__ == "__main__":
    unittest.main()
