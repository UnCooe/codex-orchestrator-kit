# 预发默认流程：有效门禁排查与验证

## 最终结论

已修复本次实际命中的多层冲突并同步 live。本轮使用三个全新 native CLI 任务发现遗漏，最后在第三个任务中直接续跑成功：公共 pre 已推送，服务器恢复 pre 分支、快进同步、重启及测试通过，无需复制命令或再次授权。不是仅靠单元测试宣布成功。

范围仅独立实验项目；尚不能宣称所有项目、任意命令或生产发布均已验证。流程效率也未全面达标，前置重复读规范与参数纠错仍有成本。

## 用户边界

“上预发验证”默认包含源分支修改、干净工作树集成公共 pre、同步、必要构建、重启与验证。不要求复制动作或 SHA。非生产 wrapper 可选；生产仍保留显式批准及受控 wrapper。破坏性操作、真实环境占用和质量阻断不是自动放行项。

## 已确认的遗漏

1. 当前 live config 只加载一份 critical_action_gate，旧 orchestrator hooks 已注释；没有发现重复旧 hook 加载。aiserver 项目和全局 pre skill 一致，默认公共 pre 已存在。
2. `tts asr问题排查` 真实失败命令含 `git reset --hard origin/pre`，没有命中非生产常规 Git 分类，掉进“current-turn semantic authorization”回退。并非只因旧 session 没刷新。
3. 独立的 `/Users/a123/.codex/git-guard/bin/git_guard.py` 仍要求 pre/test 推送带授权环境变量。它不是 Codex PreToolUse hook；前几轮只改后者不能解决这一层。
4. live aiserver_session_context 是指向 aiserver 本地 ignored `.codex/hooks` 文件的符号链接，仍输出 pre/server 当轮授权提醒。
5. 实验仓库 AGENTS 仍强制非生产 wrapper；实验 host policy 缺少对应目录。已对齐当前非生产边界，生产模拟不变。
6. live prod-ops skill 中“write requires...”及停止条件未显式限于生产；已加限定，避免非生产重复套用。

## Git guard 处理

- 仅对配置中的 pre/test 目标取消环境变量口令；改为检查已知提交的快进关系，不接受未知远端历史或回退。
- master/main promotion、其他自定义 integration 分支、禁止删除、分支命名和受保护分支提交规则保留。
- 同一已验证提交以分支、tracking ref 或精确 SHA 表达不应改变快进边界；测试覆盖。
- 候选先验证再应用；独立复核完成。12 项测试通过，原实现出现 5 个失败断言。live 与候选 hash 均为 `3c813dc2e5cc6c0e11d24da5f7918bac79d194dc8fce61c739ccd7d2e214013f`。

## Hook 修复与检查

- 普通预发快进同步以及 branch/status/rev-parse 检查不再掉入二次授权回退。
- `reset --hard` 仍按可能丢弃内容的技术风险拒绝，明确提示非破坏性同步方向，而非要求复制授权。独立复核发现的 embedded reset（包括 `git -C .`）已补测试；危险检查在常规放行之前。
- critical gate 122 项、personal Git guard 12 项、merge-safety 41 项、install hook contract 21 项通过；`git diff --check` 通过。
- 已同步 live hook，源码与 live SHA-256 同为 `9d44ded696ca4f66d2a8f347d187d86851ebc2d9e13f777e1c74c8307ea1a7ed`。

## 真实实验 1（失败，保留）

- Native CLI 新任务：`01a0c1c6-9989-7861-a383-1761fdd15e00`，2026-09-21 10:23 北京时间启动。不覆盖模型配置，不追加机器授权，不中途提示命令。
- 请求：独立实验项目 VERSION 改为 `workflow-lab:release-003`，测试后上预发验证；禁止修改 hook/wrapper/fleet/权限和业务项目、test/master/生产模拟环境。
- 初始远端：pre `9f6c87203357eee64061b22669d14d45bb73147e`，test `b948b9f2a749dd61a0bc7a052f68cb84cc75762e`，master `4063c6212c76e2a55b62f1638690aa6dcceff2b4`。
- 初始实验服务 PID：pre `3637633`，test `2779435`，prod-sim `3189810`。预发原 checkout 为 detached，实验需恢复默认公共 pre 分支。
- 结果：10:30 结束，turn duration 394286ms（约 6 分 34 秒），未部署、未重启、远端 pre 未变；无二次用户消息。
- 发现两个真实缺陷：compound `push; ssh ...` 被整体当成 push 参数；单 ref `HEAD:refs/heads/pre` 仍触发授权拒绝，需核对 feature-source 与 integration-result 绑定。
- 任务自行修正了 `target-ref pre` 不存在以及 `--p1-now 0` 被当作 blocker 文本的问题；这属于额外流程成本，不能宣称效率验收通过。
- 源提交 `8ec8819`，本地集成提交 `ec36d73` 保留，临时工作树清理。后续修复不覆盖本次失败记录。

## 根据真实失败追加的窄修复

- Compound 命令分别解析，每条 Git 操作独立检查；后续只读命令不再变成 push refspec，后续 protected push 仍会阻断。
- pre/test 的源分支与合并结果用真实 check-post 产物绑定：当前 HEAD、待推 source 与已验收 SHA 必须一致，lineage 必须证明源分支已纳入结果。不限定 Agent 使用 HEAD、分支名或 SHA 的写法。
- 七项新增集成测试直接运行 merge_guard 的 init/check-pre/record-evidence/check-post；不是手工造一份理想 state。覆盖等价 ref、过期结果、错误 source、缺 lineage、master 不走快捷路径，以及结果标签不限定 HEAD。
- 最终相关测试 142 项通过（critical gate 123 + integration 7 + personal Git guard 12），独立 reviewer 确认可同步。新 live/source hash：`ef445045b29100983661e27c78dd1906adf9949318fcd991433c640305b228b6`。

## 真实实验 2（失败，保留）

- 新 native CLI 任务 `01a0c1d7-cf51-7231-abba-1c925f754b8c`，10:42 启动。
- 只要求“继续 release-003，复用已有修改，测试后上预发验证”，相同隔离边界，不提供实现命令或机器口令。
- Codex hook 已通过；随后独立 personal Git guard 拒绝临时分支 `integration/pre-release-003` 的命名。仍未推送、部署或重启。任务自行清理临时工作树，原提交保留。
- 本轮 task duration 357166ms（约 5 分 57 秒）。
- 追加修正：普通临时分支名前缀不再阻断配置中的 pre/test 目标；仍检查快进、禁止保护分支删除、禁止无受控 promotion 的 master/main 推送，保留受保护/无效来源检查。普通 feature 目标的命名规则不变。新增测试先复现红色，候选 13 项通过。

## 真实实验 3 与续跑

- 第三次新任务 `01a0c1e0-3472-7563-89f0-f5bf6fb848c5`，10:51 启动，已实际推送公共 pre 到 `ec36d7351d8f1011cdab4dc29c4327aac2a99424`，没有二次批准或 bypass。
- 服务器同步仍被拒：只读 `git merge-base` 被当作非例行写操作；另 host 的 `test` 分类不等于所承载项目的 `pre` 分支名。
- 已修分类并补先红后绿回归：只读 ancestry probe 正常通过，配置范围内的非生产 FF 同步允许公共 pre/test；master 仍不走此路径。精确失败命令离线重放通过，相关 144 tests OK，独立复核通过。
- 第三次初始 turn 394791ms（约 6 分 35 秒），只有 pre 推送成功，服务器仍旧版本。
- 11:00 通过 native CLI resume 继续同一任务，输入仅“继续上预发验证，范围不变”等边界说明，未要求复制 SHA 或命令。续跑 98151ms（约 98 秒）成功；这是已有集成完成后的续跑耗时，不是完整开发部署耗时。
- 最终独立只读核对：服务器 branch `pre`，HEAD/origin/pre/远端 pre 均为 `ec36d7351d8f1011cdab4dc29c4327aac2a99424`；工作区干净，VERSION `workflow-lab:release-003`；专用服务 RUNNING，PID `3637633 → 517918`，连续日志心跳为 release-003，服务器版本单测 1 项通过。
- test/master 远端 SHA 与初始一致；test PID `2779435`、prod-sim PID `3189810` 均未变；原有本地工作树保留，本次临时工作树已收尾。
- live config/auth 前后 SHA-256 一致，分别为 `72964b0de3669e59f159f07b5a1555af5bde34388a9dab6d5f6e224fa717ac24` / `7ec5975eca55fc89bb373b283300eb54051dc983e12e74c504896833b9128c3c`。
- 最终 live hook SHA-256 `2a8fdac048ee71ebcebc18b11d65b166abfa982efbf0896309e1024b5ad887e4`；personal Git guard `a7c58bad9e1cfefa6ff5855303ca0e8931d9981a01013187cde819896f4e5863`。

## 备份和限制

- 修改前文件位于 `/private/tmp/nonprod-boundary-repair.hnRhNH`。
- 未修改业务代码或业务服务器。真实验证只允许独立 `/opt/codex-workflow-lab` 及专用服务。
- SSH parser 不是通用 shell 沙箱；不能将未知复杂命令假装为已证明安全，也不以追加同义词解决。
- 不以最后一次成功抹去前面三次暴露的失败；当前证明这条自然请求与续跑路径可以完成，不证明所有变体都已覆盖。
