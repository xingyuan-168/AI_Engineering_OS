# 可观测性规格

版本：V2.0-derived-observability
状态：可执行实现规格基线

## 1. 目标

任何 Workflow、任务、审批、容器执行、Plugin Hook、Worktree、Agent Handoff、Routing Decision、Memory 和发布候选物都必须能够通过关联 ID 重建过程；日志服务不可用时，核心事件仍写入本地 SQLite。

## 2. 统一事件字段

```json
{
  "event_id": "EVT-001",
  "event_type": "execution.completed",
  "timestamp": "2026-08-20T12:00:00Z",
  "project_id": "PROJECT-001",
  "workflow_id": "WF-001",
  "task_id": "TASK-001",
  "run_id": "RUN-001",
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

- 事件和审批默认保留 180 天，发布和安全事件至少保留 365 天。
- 日志按项目分目录，单文件达到 20 MB 时滚动。
- 支持导出 JSONL 审计包，导出前执行 Secret 扫描。
- 删除索引不得删除 Git 事实来源；删除操作本身写入审计事件。

## 8. 完成定义

可观测性只有在统一事件字段、日志级别、指标、诊断命令、保留周期、脱敏规则和导出格式均可验证时才算完成。
