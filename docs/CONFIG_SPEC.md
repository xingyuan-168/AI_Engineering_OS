# 配置规格

版本：V2.0-derived-config
状态：可执行实现规格基线
格式：YAML；所有配置文件 UTF-8 编码，禁止未知字段静默忽略。

## 1. 配置层级

加载顺序由低到高：

1. 内置安全默认值。
2. 全局 Codex Plugin 默认配置。
3. 项目根目录 `.codex/` 配置。
4. 项目 `project.yaml` 和 `.codex-os/` 配置。
5. CLI 显式参数。

安全字段（容器、网络、宿主路径、凭据策略、生产发布）不允许被低权限配置或单次任务参数放宽。

## 2. `project.yaml`

```yaml
schema_version: "1.0"
project_id: PROJECT-001
name: example-project
root: "C:/work/example-project"
project_type: backend | frontend | fullstack | desktop | generic
risk_level: low | medium | high | critical
source_of_truth: docs/
active_workflow: new-project
approval_policy: critical-gates-human
default_agent_profile: standard
```

### 字段规则

| 字段 | 类型 | 必填 | 规则 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 只接受受支持的主版本 |
| `project_id` | string | 是 | `PROJECT-` 前缀，项目内唯一 |
| `root` | path | 是 | 必须是已存在的项目根目录 |
| `risk_level` | enum | 是 | 影响审批和执行策略 |
| `source_of_truth` | path | 是 | 必须位于项目根目录内 |
| `active_workflow` | string | 否 | 必须引用已注册 Workflow |

## 3. `workflow.yaml`

```yaml
schema_version: "1.0"
name: new-project
version: "1.0.0"
states: [intake, requirements, research, design, implementation, verify, release, memory]
initial_state: intake
max_retries: 2
checkpoint: after_transition
gates: [G0, G1, G2, G3, G4]
```

Workflow 的详细状态和门禁规则见 [WORKFLOW_SPEC.md](WORKFLOW_SPEC.md)。

## 4. `skill.yaml` / `SKILL.md` 元数据

```yaml
schema_version: "1.0"
name: requirement-analysis
version: "1.0.0"
description: 分析业务目标并生成需求产物
inputs: [business_goal, project_context]
outputs: [PRODUCT_REQUIREMENTS.md, USER_STORY.md]
tools: [read_docs, write_artifacts, codex_host]
allowed_paths: [docs/]
approval: none | critical | always
```

正文契约见 [SKILL_SPEC.md](SKILL_SPEC.md)。

## 5. `agent.yaml`

```yaml
schema_version: "1.0"
name: architect
version: "1.0.0"
role: Architect
skills: [architecture-design, api-design, database-design]
read_paths: [docs/, .codex-os/]
write_paths: [docs/ARCHITECTURE.md, docs/API_SPEC.md, docs/DATABASE.md, docs/ADR/]
branch_prefix: agent/architect/
requires_review: true
```

详细角色和任务契约见 [AGENT_SPEC.md](AGENT_SPEC.md)。

## 6. `execution-policy.yaml`

```yaml
schema_version: "1.0"
sandbox: docker | podman
network: disabled
allowed_mounts: [worktree, artifacts, cache]
allowed_commands: [git, python, pytest, ruff, pyright]
blocked_commands: [format, del, rd, shutdown, reboot]
max_duration_seconds: 1800
approval_for: [network, migration, delete, credential, release]
```

详细策略见 [EXECUTION_POLICY.md](EXECUTION_POLICY.md)。

## 7. Memory 配置 `memory.yaml`

```yaml
schema_version: "1.0"
enabled: true
storage: sqlite
fts5: true
default_scope: project
min_active_confidence: 0.7
retention_days: 0
allowed_types: [decision, project, bug, experience]
redaction_profile: strict
cross_project_reuse: approval_required
```

`storage` 只能使用已登记的 SQLite 存储；`default_scope` 必须为 `project` 或更严格范围。配置不得关闭来源 hash、脱敏、审计或项目隔离；`retention_days: 0` 表示按 `expires_at` 和人工失效规则管理，而不是立即删除历史。

## 8. Plugin 配置 `plugin.yaml`

```yaml
schema_version: "1.0"
host: codex
plugin_api: "1.0"
runtime_endpoint: "local-cli"
enabled: true
hooks:
  enabled: true
  timeout_seconds: 10
  max_retries: 2
permissions:
  read_paths: [docs/, .codex/]
  write_paths: []
  commands: []
  network: disabled
future_adapters: [harness-adapter]
```

Plugin 权限只能由全局策略收紧；项目配置不得增加可写路径、命令、网络或凭据权限。`host` V1 只能为 `codex`；`harness-adapter` 仅登记兼容接口，不得使 DeepSeek 进入 V1 验收路径。

## 9. Worktree 配置 `worktree-policy.yaml`

```yaml
schema_version: "1.0"
root: .worktrees
layout: "<workflow-id>/<agent-name>/<task-id>"
branch_pattern: "agent/<agent-name>/<task-id>"
require_isolation: true
preserve_failed: true
cleanup_requires_clean: true
orphan_cleanup: human_approval
```

`root` 必须位于项目根目录内；`layout`、`branch_pattern` 和 `require_isolation` 不可由低权限配置放宽。失败现场、Branch、Commit 和审计记录默认保留。

## 10. 路由配置 `routing-policy.yaml`

```yaml
schema_version: "1.0"
score_range: [0, 10]
tie_policy: needs_approval
boundary_policy: needs_approval
insufficient_input: blocked
high_risk_override: forbidden
profiles: [frontend-project, backend-project, large-project]
audit: required
```

路由配置只能改变评分阈值的明确策略，不能降低风险级别、跳过人工确认或删除路由审计。字段 `tie_policy`、`boundary_policy` 和 `insufficient_input` 不得设置为自动猜测。

## 11. 交接配置 `handoff-policy.yaml`

```yaml
schema_version: "1.0"
required_artifact_fields: [path, kind, hash, source_commit, status]
require_commit: true
require_tests: true
reject_on_open_questions: true
reject_on_hash_mismatch: true
conflict_policy: blocked
review_required_by_default: true
```

交接配置不能关闭 hash/Commit 校验、测试证据、路径校验或冲突阻塞。角色级配置只能增加必需产物，不能减少全局必需字段。

## 12. 新增配置的最小字段契约

| 配置 | 必填字段 | 字段类型约束 | 允许的覆盖范围 |
| --- | --- | --- | --- |
| `memory.yaml` | `schema_version`、`enabled`、`storage`、`default_scope`、`redaction_profile` | string、boolean、enum、enum、enum | 只能收紧范围、置信度和保留策略 |
| `plugin.yaml` | `schema_version`、`host`、`plugin_api`、`enabled`、`permissions` | string、enum、string、boolean、object | 只能减少 Hook、路径、命令和网络权限 |
| `worktree-policy.yaml` | `schema_version`、`root`、`layout`、`branch_pattern`、`require_isolation` | string、path、string、string、boolean | 只能收紧路径和清理策略 |
| `routing-policy.yaml` | `schema_version`、`score_range`、`tie_policy`、`boundary_policy`、`insufficient_input`、`audit` | string、整数数组、enum、enum、enum、enum | 只能增加人工确认和阻塞 |
| `handoff-policy.yaml` | `schema_version`、`required_artifact_fields`、`require_commit`、`require_tests`、`conflict_policy` | string、string 数组、boolean、boolean、enum | 只能增加必需证据和校验 |

所有新增配置必须拒绝未知字段、记录版本和最终配置 hash；项目级和 CLI 覆盖不能改变 `host=codex`、默认沙箱、Memory 脱敏、Worktree 隔离、Handoff 校验或高风险审批。

## 13. 校验与兼容

- 启动、初始化、恢复和升级前均校验全部配置。
- 缺失必填字段、枚举非法、路径越界、版本不兼容时返回 `CONFIG_INVALID`。
- 未知字段默认报错；只有显式声明 `extensions` 的命名空间允许扩展字段。
- Schema 主版本不兼容时禁止启动；次版本可在兼容范围内读取。
- 配置 hash 写入 Workflow 检查点，恢复时必须匹配或经过迁移。

## 14. 完成定义

配置规格只有在每种配置都有 Schema 版本、字段类型、必填规则、覆盖规则、安全限制、错误处理和示例时才算完成。
