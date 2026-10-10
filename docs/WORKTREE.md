# Worktree 协议

AIOS 不创建、调度或管理 Agent；Codex 主会话负责规划与拆分，必要时调用原生子 Agent。AIOS 只负责把并行写隔离在登记的 disposable worktree 里。

## 四能力（core/worktree.py）

`prepare(task) / check(task) / finish(task) / cleanup(task)`（外加 list）。MCP `worktree_manage` 与 CLI `codex-os worktree ...` 暴露同一实现。

- Worktree 位于 `.worktrees/<slug>`，分支 `codex/wt-<slug>`，目标分支登记为 `target_branch`（默认 main）。
- `prepare` 登记进 SQLite worktrees 表；只有登记的真实 worktree（SQLite 记录 + cwd realpath 包含 + `git worktree list` 佐证）被 Hook 视为 disposable，伪造的 `.worktrees/` 目录失败封闭。
- disposable 区内仅具备局部 Git 操作放行条件；文件清理仍检查实际目标，不能凭 cwd 删除外部资产、跟踪文件或整个 checkout。
- `finish` 要求 worktree 干净（全部已提交），否则拒绝——子 Agent 工作绝不丢失；finish 只把状态置为 `ready`（ready ≠ merged）。
- `cleanup` 验证路径、登记与 Git 清单、干净状态以及 `merge-base --is-ancestor <tip> <target>`；失败拒绝。证明后依次执行非强制 `git worktree remove` 和 `git branch -d`，全部成功才注销登记。中途失败保留记录/分支以便恢复，不升级为 -D 或 force。创建后登记失败，只回收本次新建且仍干净、分支 tip 未变化的对象；否则保留并报告孤儿状态。

## 纪律

- 并行写路径明显冲突的任务不得同时改；子 Agent 不写主工作区与其他 Worktree。
- 子 Agent 不写 `docs/memory/`：提交 candidate，主会话 Finish 时合并。
- 子 Agent 完成后回报变更、测试与风险；主会话 review 后合并，合并完成立即 cleanup。
- 不保留孤儿 Worktree；无 coordination/DAG/lease 机器。

## Handoff

轻量 handoff 规则：任务中断时在分支留下未完成说明（提交信息或 Worktree 内 NOTE），主会话可凭 worktree list + 分支状态恢复；不建独立的 handoff 文档体系。
