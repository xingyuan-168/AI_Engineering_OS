# API 与命令规范

V1 以 CLI 为主，未来可由 FastAPI 暴露同一服务接口。

## CLI

| 命令 | 输入 | 输出 |
| --- | --- | --- |
| `codex-os init` | 项目路径、名称 | 文档骨架、项目清单 |
| `codex-os run <workflow>` | 目标、项目 ID | Workflow ID、初始状态 |
| `codex-os status` | Workflow ID（可选） | 状态、阻塞、待审批、产物 |
| `codex-os resume <id>` | Workflow ID、审批结果 | 下一状态或阻塞原因 |
| `codex-os check-docs` | 项目路径 | 完整性和影响检查报告 |
| `codex-os research <capability>` | 能力板块 | 开源调研任务和记录 |
| `codex-os verify` | Workflow ID | 测试、Review、安全结果 |
| `codex-os release --candidate` | Workflow ID | 发布候选物和证据清单 |
| `codex-os approve <workflow-id> --gate G2` | Workflow ID、门禁 | 审批记录和下一状态 |
| `codex-os reject <workflow-id> --gate G2 --reason <原因>` | Workflow ID、门禁、理由 | 退回记录和阻塞原因 |
| `codex-os doctor` | 当前环境 | Python、Git、容器、SQLite、权限和安全检查 |

## 任务事件

```yaml
task_id: string
workflow_id: string
from: string
to: string
type: artifact-request | result | approval | failure
payload: object
artifacts: [string]
approval_required: boolean
created_at: RFC3339
```

## 错误类别

- `CONFIG_INVALID`：项目、Workflow、Skill 或 Agent 配置无效。
- `DOCS_INCOMPLETE`：必需文档缺失或状态未达标。
- `APPROVAL_REQUIRED`：需要人工确认。
- `PATH_DENIED`：操作超出允许路径。
- `COMMAND_DENIED`：命令未通过策略。
- `DEPENDENCY_UNVERIFIED`：开源依赖版本、License 或安全信息未核验。
- `RECOVERY_UNAVAILABLE`：没有可用检查点或产物。

## 核心类型

`ProjectConfig`、`WorkflowDefinition`、`WorkflowState`、`Task`、`TaskEvent`、`Approval`、`ExecutionRequest`、`ExecutionResult`、`Artifact`、`MemoryRecord` 是运行时公共类型。字段的来源和持久化映射见 [CONFIG_SPEC.md](CONFIG_SPEC.md)、[WORKFLOW_SPEC.md](WORKFLOW_SPEC.md) 和 [MIGRATION_SPEC.md](MIGRATION_SPEC.md)。

## 通用响应

```yaml
ok: true | false
request_id: RUN-001
workflow_id: WF-001
status: completed | blocked | failed | needs_approval
data: {}
error:
  code: null
  message: null
  details: {}
```

重复请求使用 `Idempotency-Key`（CLI 自动生成 `workflow_id + state_version + operation`）；同一 key 必须返回同一业务结果，不得重复创建任务或产物。

## 退出码

统一退出码见 [RUNTIME_SPEC.md](RUNTIME_SPEC.md) 第 8 节；CLI 输出必须同时包含机器可读 JSON 和面向用户的摘要。
