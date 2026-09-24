# 授权流程验证：未完成验收

## 本轮结论

- 本地 critical-action hook 与 live 文件哈希一致。
- 发现 live 缺少 critical-action-policies 主机映射；此前测试通过时使用的是 fixture policy，不能代表 live 安装完整。
- 已增加 live test/pre 映射，生产未放宽。当前非生产快捷路径依赖受控 prodctl 的 token 和已登记主机/服务；这不是自然语言理解准确率的证明。
- master promotion helper 与本地 Git guard 已改动，但真实隔离 master 推送闭环仍未完成。dry-run 不算推送验收。

## 真实服务器操作与恢复

本轮错误地选择了现有业务 test 服务，而非用户要求的独立测试项目。后续不得沿用这个方式验证。

目标：test / /opt/project/aiserver_test / aiserver_test。

1. 原提交 f32952fe256e96b8e15a3dbd215f805714fba98a，服务 RUNNING。
2. 通过受控 prodctl 切到 d507fdf5bd827b0fc5f387dd8bc8231702830e53，成功。
3. 受控重启后 BACKOFF，随后 FATAL。根因未确定；应用日志中未检索到 ERROR 或 Traceback，不代表没有启动错误。
4. 通过受控 rollback 恢复 f32952fe256e96b8e15a3dbd215f805714fba98a，再重启成功。
5. 最后观察 RUNNING，PID 1586918，uptime 3:43。工作区为 detached HEAD，未恢复原 master 分支附着状态。

pre 只读核验：a0bed2ae362f227fc2c6fb7a2c857e198dcd7e5e，RUNNING。未部署或重启。生产未执行写操作。

另发现日志查询含竖线的参数到远端后发生 shell 拆分；该命令失败，没有执行写操作。参数传递问题另列待修，不扩大本轮变更。

## 测试范围

- 仓库 unittest：236 通过。
- live merge-safety unittest：40 通过。
- Python 编译、git diff --check 通过。
- 测试输出含 codex-cli 0.144.0 与 0.144.1 不一致提示；不能据此宣称运行时版本验收通过。
- 新任务 01a0bd15-c29d-79c1-b253-41076df5acb7：按指定场景运行 mock，报告 test/pre 不要求复制、生产停下确认。这是脚本模拟，不是真实多轮用户交互或真实服务端验收。
- master 新任务曾因 provider 故障结束，不计作通过或行为失败。

## 下一步边界

先明确独立测试项目的仓库、目录、服务、端口及受控安装入口。若现有 wrapper 无法创建隔离项目，先提交最小能力扩展方案，不以现有业务服务代替。

在隔离项目中由主任务逐轮发送自然语言指令，让新任务自行执行；以实际远端引用、服务版本、重启回执和拒绝事件验收。不能把预写好“应该通过”的 mock 当端到端成功。

在 master、pre/test、生产模拟保护和负例均完成前，状态保持未验收。
