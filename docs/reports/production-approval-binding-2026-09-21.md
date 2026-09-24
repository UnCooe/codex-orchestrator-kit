# 生产授权绑定：实际复现与边界

## 本轮范围

原发布任务：`01a0c31b-ed68-7061-8bbe-9348b266fb21`。
不执行业务生产迁移、发布或重启；仅修本地 gate，并在既有独立实验服务验证。
沿用 dirty 工作树，无 commit/push/reset；此前改动保留。

## 已确认原因

1. 上轮修复的是 `restart-service --dry-run` 误当写入。本轮不重复该修复。
2. 原任务 09:50 UTC 计划写“cn-sz1 重启第一个实例”，没有 canonical service；fleet 明确 server_instance=1 对应 aiserver。不能把所有缺失服务名都当作默认服务。
3. 独立原生 CLI 实验已展示确切主机与服务，用户仅回复 `ok，继续吧`，仍被拒绝。门禁额外要求计划出现环境词，而已登记主机本身就确定环境。

## 最终保留的修复

- 计划没有显式环境词时，只有唯一已登记主机命中才从主机 policy 取得环境。错误主机、多主机歧义或明确冲突环境不推断。
- 读取紧邻计划时不跨越另一条普通用户消息，避免复活已过期计划。
- 不修改 policy、config、auth、wrapper；不新增审批步骤。
- 首实例别名解析候选在独立复核中发现 P1，已从源代码撤回，未同步 live。不能宣称原来的省略服务名计划已经自动支持。
- Agent 应先从 fleet 解析具体服务，展示准确计划；用户仍只需自然语言确认，不需要复制命令。固定版本已在 master 时使用正常 master 发布与 expected-head 校验，不为了固定版本进入 detached 例外。

## 校验

`uv run --with pytest pytest -q tests/test_production_plan_binding.py tests/test_critical_action_gate.py tests/test_critical_gate_pre_integration.py`

142 passed，228 subtests passed；git diff --check 通过。最终15行增量独立复核无未解决 P0/P1/P2。
live 与 source 完全一致。变更前副本：`/private/tmp/prod-auth-binding.pC1iYz/`。

## 实际会话实验

会话 `01a0c38b-1a3a-7192-b7b7-1219543231bb`，CLI 原生用户输入，无模型配置修改。
目标仅测试机 `/opt/codex-workflow-lab` 中 `lab-prod-sim` / `codex-workflow-lab-prod-sim`。
第一次短确认被 gate 阻断、零写入，保留失败。修复后用同一句短确认继续。
修复后真实重启退出0，PID 从3189810变为3023391，后续受控 status/ps 确认 RUNNING、目标进程存在；Git SHA保持4063c6212c76e2a55b62f1638690aa6dcceff2b4。
本次不是dry-run，调用了测试机专用wrapper；只重启独立实验服务一次，没有业务生产写入。修复后未要求复制授权或补服务名。
首次失败和再次确认均保留，不能说首次全流程一次通过。CLI续接同一实验会话成功，不等于所有Desktop旧任务与所有生产动作已覆盖。

## 结论限制

本轮不是整个14主机30实例发布链的验收。迁移、工具安装、滚动条件和生产变更仍由原发布任务负责，转发消息不替代用户生产授权。

## 后续实际 deploy 阻断修复

原发布已完成生产两表迁移，实际deploy仍拒绝。本次新增回归使用10:48:48完整计划和10:58:28原文“可以 开始吧”，而非简化字符串。

发现并修复：消息文本块误拼入input_text元数据；短确认未识别；“工具修复已通过测试和复审”误判test环境；明确master路径未绑定ref。仅处理该确定兼容缺口，不宣称通用语义授权。条件或否定验证语句不能按“已通过”处理。

150 tests / 238 subtests passed，独立复核通过，diff检查及source/live一致性检查通过；备份 `/private/tmp/release-gate-hotfix.nW4OGt/`。原冻结授权离线覆盖44项（14部署+30重启）全部通过，非真实生产执行。

注意原任务随后用户已询问“那接下来怎么办”，该问题本身不是新的批准。本次未篡改消息或把任务间转发升级为用户授权；已让原任务准备准确剩余计划。真实独立实验deploy验证运行中。

### 最终回执

- 原用户随后在发布任务原生输入 `ok 开始吧`，该形式已纳入同一短确认回归。最终150 tests / 240 subtests通过。
- 实验会话 `01a0c3b7-3778-7cd2-883c-b9057d4305a7`：真实非dry-run deploy退出0，master保持4063c621，实验服务PID3023391→3267885，返回RUNNING。部署到同一SHA，不冒充版本升级验证。
- 原生产任务实际deploy也已成功：cn-sz1的master从d507fdf到8000e71，aiserver PID2681440→2932770，服务RUNNING。由原任务使用真实用户授权执行，本诊断任务未直接操作业务生产。
- 已通知原任务部署内部已重启首实例，不要重复重启，按既定5分钟观察后再推进。整批发布未完成，不能用首实例成功代替全量验收。
- 修复为有限文本兼容与解析正确性修补，不是已完成通用语义授权架构。
