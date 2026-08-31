# 可观测性规格

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"review-ready","owner":"architect","requirement_refs":["REQ-1.6.2","GOV-001"]} -->

版本：V2.0-derived-observability
状态：可执行实现规格基线

## 1. 目标

任何 Workflow、任务、审批、容器执行、Plugin Hook、Worktree、Agent Handoff、Routing Decision、Memory 和发布候选物都必须能够通过关联 ID 重建过程；日志服务不可用时，核心事件仍写入本地 SQLite。

## 2. 统一事件字段

```json
{
  "event_id": "EVENT-20260831090002000000-C3D4E5F6",
  "event_type": "execution.completed",
  "timestamp": "2026-08-20T12:00:00Z",
  "project_id": "PROJECT-001",
  "workflow_id": "RUN-20260831090000000000-A1B2C3D4",
  "task_id": "TASK-20260831090001000000-B2C3D4E5",
  "run_id": "RUN-20260831090000000000-A1B2C3D4",
  "actor": "agent:architect",
  "status": "completed",
  "duration_ms": 1200,
  "artifact_refs": [],
  "approval_ref": null,
  "error_code": null,
  "payload": {}
}
```

`payload` 不得包含 Secret、完整凭据、未脱敏用户输入或不必要的个人数据。

## 3. 事件类型

除状态、审批、执行和发布事件外，必须记录以下治理事件：

| 事件类型 | 关键字段 |
| --- | --- |
| `routing.decided` | 输入 hash、评分、维度理由、Workflow、Profile、人工覆盖 |
| `worktree.created` / `worktree.cleaned` | Worktree、Branch、Task、路径、清理审批 |
| `handoff.created` / `handoff.accepted` / `handoff.blocked` | Handoff、产物 hash、Commit、测试、阻塞原因 |
| `memory.candidate` / `memory.activated` / `memory.invalidated` | Memory、来源 hash、类型、置信度、状态原因 |
| `plugin.hook.failed` | Plugin API、Hook、Request、错误码、重试次数 |

所有治理事件必须包含 `project_id`，适用时包含 `workflow_id` 和 `task_id`，并与对应 SQLite 表和产物索引关联。

## 4. 日志级别

- `ERROR`：失败、拒绝、数据损坏和安全事件。
- `WARN`：重试、降级、配置风险和即将超限。
- `INFO`：状态转换、任务、审批、执行和发布事件。
- `DEBUG`：仅本地诊断，默认不记录完整提示词或源码。

## 5. 指标

| 指标 | 计算方式 | 用途 |
| --- | --- | --- |
| Workflow 成功率 | completed / started | 判断流程稳定性 |
| 任务失败率 | failed tasks / total tasks | 定位 Skill/Agent 问题 |
| 重试率 | retried tasks / total tasks | 判断执行可靠性 |
| 恢复成功率 | resumed successfully / resume attempts | 验证检查点质量 |
| 审批等待时长 | approval decision - request | 识别门禁瓶颈 |
| 沙箱失败率 | sandbox failures / executions | 监控 Docker/Podman |
| 文档完整率 | compliant projects / checked projects | 监控治理质量 |
| 发布阻塞率 | blocked releases / release attempts | 识别质量和安全缺口 |

## 6. CLI 诊断

`codex-os status` 必须显示 Workflow 状态、当前任务、阻塞原因、待审批、最近事件、检查点和产物。`codex-os doctor` 必须检查 Python/uv、Git、Docker/Podman、SQLite、配置 Schema、目录权限、磁盘空间和安全工具。

## 7. 保留和导出

- 事件和审批在活动存储中至少保留 180 天，发布和安全事件至少保留 365 天；这两个数字是最短热保留期，不是自动删除期限。
- 被 Workflow、Gate、ADR、Memory、Release 或安全事件引用的记录不得到期删除。超过热保留期后只能进入带 hash、索引和恢复说明的审计归档。
- 0.2.x 不提供自动 purge；未来删除必须使用独立、获批且可审计的维护 Workflow。
- 日志按项目分目录，单文件达到 20 MB 时滚动。
- 支持导出 JSONL 审计包，导出前执行 Secret 扫描。
- 删除索引不得删除 Git 事实来源或被引用的审计链；删除操作本身写入审计事件。

## 8. 完成定义

可观测性只有在统一事件字段、日志级别、指标、诊断命令、保留周期、脱敏规则和导出格式均可验证时才算完成。
