# Workspace 遗留收口与定期盘点

这套机制解决的是“改动属于谁、是否还值得保留”，不是强迫每个任务在每
次停止时都提交。

## 一次性收口

1. `workspace_lifecycle_reconcile.py inventory` 只读扫描 Git 已登记的
   worktree，并把脏状态分成三类：

   - `owner_recall`：ledger 有经过验证的 `task_id/thread_id`。由 Codex
     Desktop 向原任务发送一次收口消息；原任务提交、handoff 或明确放弃。
   - `quarantine_review`：没有可信 owner。保留改动和历史候选，不让新任务
     静默接管；必要时显式执行 `--quarantine`。
   - `expiry_candidate`：次级 worktree、没有可信 owner、且按证据达到过期
     阈值。默认阈值为 30 天。

2. 对 `expiry_candidate` 先运行 `expire`（默认 dry-run）。确认路径、分
   支、HEAD、变更路径和快照后，才给明确的 `--worktree` 加 `--apply`。
   主 checkout 永远被拒绝，删除的是 Git worktree 目录，不是主仓库里的单
   个文件。删除前的 diff、未跟踪文件清单和 SHA 存放在 recovery 目录。

3. 过期 worktree 删除后，分支引用仍保留；未提交内容只能从 recovery 快照
   恢复。若路径、时间证据或 owner 不清楚，保持 `quarantine_review`，不要
   通过猜测把它归给某个任务。

## 从现在开始的闭环

- SessionStart/UserPromptSubmit 在干净 worktree 上记录 owner。
- Stop 时若 owner 明确但 worktree 脏，shadow hook 写入
  `pending_closure` 和队列，不发送消息、不修改 Git。
- Desktop/人工 dispatcher 读取队列，调用原 task 的
  `send_message_to_thread`；消息只包含路径和收口要求，不带源码正文。
- 原任务提交后到达 `ready`/`archived`，队列项标记 `resolved`。
- 新任务看到 `pending_closure` 或 `quarantined` 时只告警，不覆盖原改动。

本仓库不自动注册 launchd/cron，也不在 hook 中调用 Desktop API。建议每天
或每次开始工作前运行一次 `inventory`；以后若要自动提醒，可把该命令接到
用户自己的本地调度器，保留同样的 dry-run 和路径边界。

## 为什么不把所有遗留都发给“最像”的任务

session 日志里经常会出现子 Agent 读取过某个路径、主任务创建过多个
worktree 或后续任务检查过旧文件。只有 ledger 的明确绑定，或可复核的原
任务回执，才足以发送 owner recall；其余只记录为候选，避免把别人的改动
误交给错误任务。
