# ADR-0006：分阶段 Workflow 生命周期与安全取消

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"accepted","owner":"architect","requirement_refs":["REQ-1.6.2","GOV-001","GATE-001"]} -->

## 状态

Accepted，2026-08-31。

## 上下文

原 `workflow_start` 在一个调用中创建 Workflow、首个 Task 并分配 Worktree，调用方无法先审查 Routing Decision，也没有显式、安全且可恢复的取消契约。直接把运行态改成 `cancelled` 还可能掩盖正在执行或结果未知的 Host Operation。

## 决定

API 1.2 增加 `workflow_create`、`workflow_begin` 与 `workflow_cancel`。创建事务只持久化 `created` Workflow 和 Routing Decision；开始事务绑定期望状态版本与幂等键，原子创建首个 Task；取消事务停止新调度、取消逻辑 Task，并保留 `running/reconcile_required` Host Operation 的对账责任。全部外部操作终止后才进入 `cancelled`。成功发布的 Release 不允许用取消替代回滚与审计。

取消请求保存在现有 checkpoint 与追加事件中，因此 SQLite 继续使用 `0007`。`workflow_start` 在 0.2.x 作为带弃用 warning 的 create+begin 兼容入口保留。

## 后果

- 调用方可在分配执行资源前审查 Routing 与目标文档版本。
- 所有写入支持稳定幂等重放和乐观并发冲突检测。
- 取消不会删除 Git、Release 或审计历史，也不会把未知外部结果伪装成已终止。
- 0.3.0 可在 ADR 复核后移除兼容 `workflow_start`。
