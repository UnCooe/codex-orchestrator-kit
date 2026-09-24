# 预发／测试执行路径恢复

## 边界

- 用户授权仍是必要前提，由 Agent 从当前需求和上下文理解；普通 pre/test 不再由 hook 重复校验授权词。
- pre/test wrapper 可选，默认集成到项目公共 pre/test 分支。工作树、测试、血缘、远端漂移和自己产生的临时内容由 Agent 处理。
- hook 保留配置的主机、目录和服务范围，强推、删除及非例行破坏性操作不走快速路径。生产路径不放宽。

## 已应用

- kit 和 live critical_action_gate.py 同步：配置内非生产例行 SSH、证据完整的 pre/test push 不再索要逐步口令；修复 Supervisor group:service 解析。
- 更新全局指令、predeploy-workflow 和 live aiserver-prod-ops 的非生产 wrapper 规则。
- 只读确认后补全 test 的 /opt/project/midoo_pre 与 midoo_pre:midoo_pre_api_app，以及 aiserver 的 pre/test 范围；独立实验目录与服务单独列明。
- 备份：/private/tmp/nonprod-restore.8ad5Pp。

## 验证

- 清除继承的 CODEX_SESSION_ID 后，critical gate 115 项通过；merge-safety 41 项通过；git diff --check 通过。
- 发现测试进程若继承真实会话 ID，会读入真实授权上下文，导致无授权负例受污染。本轮使用 env -u CODEX_SESSION_ID 隔离，不把受污染运行算通过。
- 原生 CLI 实验会话：01a0be66-4396-77b3-94c6-616deac620f4。首次只读查询的 2>&1 被误判文件写入，已保留失败；仅修复标准描述符复制识别，真实文件重定向仍受限。最终 critical gate 116 项通过，独立复核无 P0/P1。
- 同一会话继续成功：2026-09-20 10:44:23 UTC，通过 SSH 在实验 pre 目录执行一次 Supervisor 重启；PID 3157805 → 3637633，RUNNING，启动日志 release-002，未要求重复授权。未修改源码或部署新版本；证明实际 SSH 重启路径可用，不冒充完整业务发布验收。
- 已给业务任务 01a0b3d2-74dc-7b93-af92-4a8529cc331a 发送只读规则刷新通知，要求停止改全局 hook、不要为普通预发新增 wrapper。没有将转发消息作为服务器写入授权。

## 限制

- scp/rsync 上传仍拒绝，未新增复杂传输解析；正常 Git 拉取部署不依赖上传。
- SSH 命令解析不是完整 shell 安全沙箱，Go build 等未覆盖形态不能宣称已验证。
- 本轮不替业务任务执行发布或重启，不宣称其业务版本已经上线。
- 独立审查原先提出“缺少二次正向授权”与用户最新要求冲突，已明确职责：Agent 判断授权，hook 守技术危险边界，不把同意词解析重新塞回来。

## 业务预发实际执行（用户批准继续后）

- 原任务最初使用未带目录的 git push，再次遇到 runtime workdir 丢失导致的验证状态查找错误。主任务只读对照确认：相同 state，plain git 被拒绝、显式 git -C 到实际集成 worktree 通过；没有修改授权词或再改 hook。
- 保留正常 company Git guard，通过其集成授权入口推送公共 test 到 `13488aa1456754af75a3b01493fd08effd1368cd`，未使用 bypass。
- 业务服务器 `aiserver-test:/opt/project/midoo_pre` 在 test 分支通过 fetch + SHA 断言 + merge --ff-only 同步到该提交，四个原有未跟踪文件保留。
- 原任务执行 `./service_ctl.sh restart api app`，构建成功并仅重启 `midoo_pre:midoo_pre_api_app` 一次，PID `3828106`，运行该目录新构建的 api-midoo；7001 /ping 为 HTTP 200 pong。
- 已清除完成的 merge-safety intent、移除本任务干净的临时集成 worktree；源工作区其他改动未动，集成分支引用保留。
- 完成数真实业务验收尚缺登录态和具体 lesson_id。既有测试和部署健康检查不能代替真实用户课时场景，不宣布端到端业务验收通过。
- 未操作 master、生产、其他业务服务或继续修改全局运维工具。

## 目录与入口收尾

- 修改并同步 merge-safety 与 predeploy 文案：推送使用工具返回的显式 `git -C <checked_root>`；先确认项目的正常 Git 入口，不在错误项目运行 aiserver 专用规划器。未新增 hook 或审批步骤。备份 `/private/tmp/workflow-closeout.hxojz6`。
- 更新 live aiserver-prod-ops 说明：多个目标可以共用 SSH 别名，分别配置目录、服务、policy；映射能力不代表 Go 构建能力，普通非生产部署不必先扩展 wrapper。
- 使用现有 fleet/client 代码离线构造两个共用 SSH 的目标，验证生成命令保留各自目录、服务与 policy；未安装示例 policy，未连接服务器。
- 任务 `01a0b45f-dac2-7ac0-884e-39901209405d` 已完成只读更正：保留 aiserver_test 映射，不将其替换为 midoo_pre，不重构 target 模型，也不重复部署业务服务。
- 本轮 merge-safety 41 项测试通过，source/live 两份 skill 内容一致，`git diff --check` 通过。
- 新原生 CLI 实验 `01a0be9f-861b-72d2-9dbf-ed06bb6fe614` 的首次顺畅执行验收失败：虽然推送已要求显式目录，但前置 plain `git merge` 仍被拦截两次。保留原始失败；补充所有集成命令同样使用 `git -C <checked_root>` 的文案，不修改 hook。
- 后续诊断性继续已成功快进临时工作树到 `9f6c872`；不能将带人工诊断的恢复计作新会话首次通过。审查还曾把实验开始前的 master 历史变更误当成本轮越界，必须按本轮增量判断。
- 最终推送仍失败：显式 `git -C` 已通过外层检查，但专用 Git guard 拒绝 `integration/test-release-002` 分支命名及缺少正常集成授权标记。新会话没有落实“首次执行前选对项目正常入口”的文案。因此本次真实实验不通过，不声称三项已全部验收；未继续扩展 hook 或绕过保护。
- 诊断消息通过 Desktop 转发后另起了同任务执行回合；原生 CLI 进程已显式停止，避免两个执行者并行修改同一工作树。本次只作失败诊断证据，不能当作纯新会话性能样本。
- 收尾后主任务独立执行 `ls-remote`：test 仍为 `b948b9f`、pre 为 `9f6c872`、master 为 `4063c62`，均与开始一致；新建的临时工作树已移除，其余四个原有工作树保留。实验任务已结束，无部署或重启。

## 按边界引导，不穷举执行路径

- 用户再次明确：允许 Agent 在授权范围内选择路径，不应将工具覆盖范围当作操作许可范围。本次仅收缩两份 skill 引导，并同步同名 live skill；未改 hook、Git guard、wrapper、模型或配置，未操作服务器。
- merge-safety 不再要求逐字使用生成的推送命令：保留明确目录、已验证提交、目标 ref、证据及禁止强推等不变量；项目正常入口由项目规则决定，不编造授权标记、不忽略失败安全检查。
- predeploy 不再强制每个项目调用 planner；普通 pre/test 可使用已有脚本或标准命令，生产仍走受控 wrapper。没有新增命令生成器或场景枚举。
- 当前变更前快照：`/private/tmp/workflow-boundary-guidance.F1DSO1`。本轮逐项检查精确 diff、source/live 一致性和 `git diff --check` 均通过；skill-creator 的 quick_validate 因当前 Python 缺少 PyYAML 未运行成功。
- 本轮未启动新的模型任务或远端实验；这是引导修订，不是对上一轮真实失败的通过证明。
