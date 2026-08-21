# Memory 规范

版本：V2.0-derived-memory
状态：可执行实现规格基线
范围：项目内长期知识、决策追踪、失败经验和可审计检索。

## 1. 目标与原则

Memory 保存经过验证、可复用且有来源的项目知识。Memory 不是聊天记录仓库，也不是第二事实来源；事实仍保存在 Markdown/Git，SQLite/FTS5 只保存索引、元数据和事件关联。

原则：当前项目优先、来源优先、低置信度不作事实、旧决策不静默覆盖、跨项目复用显式授权。

## 2. Memory 分类

| 类型 | 保存内容 | 典型来源 |
| --- | --- | --- |
| `decision` | 架构决策、技术选型、设计原因和后果 | ADR、G2 审批 |
| `project` | 项目结构、模块关系、当前状态和约束 | 架构文档、项目清单 |
| `bug` | 复现、根因、修复、回归测试和影响 | Bug 任务、测试报告 |
| `experience` | 最佳实践、失败案例和可复用经验 | Review、发布复盘 |

## 3. 记录 Schema

```yaml
memory_id: MEM-001
project_id: PROJECT-001
type: decision | project | bug | experience
title: string
content: string
source_refs: [docs/ADR/ADR-0001.md]
source_hashes: [sha256:...]
confidence: 0.0-1.0
tags: [architecture, workflow]
scope: project | organization | public
status: pending | active | needs_review | superseded | revoked | expired | deleted
created_at: RFC3339
updated_at: RFC3339
expires_at: RFC3339 | null
```

`source_refs`、`source_hashes`、`project_id`、`status` 为必填；没有来源的记录不得进入 `active` 状态。逻辑字段 `content` 在 SQLite 中以脱敏内容引用 `content_ref` 保存，逻辑字段 `source_hashes` 以 `source_hash` 或其 JSON 列表保存，`tags` 以结构化标签列保存。`pending` 表示候选记录尚未完成校验，`needs_review` 表示来源 hash、冲突或适用范围发生变化，二者都不能作为事实直接消费。

## 4. 写入触发

以下事件必须触发 Memory 候选记录：

- ADR 创建、接受、替代或撤销。
- 技术选型和 G2 架构决策确认。
- Bug 关闭且回归测试通过。
- Release 完成或回滚复盘。
- 架构、数据模型、安全策略或 Workflow 发生重大变更。
- Review 确认的失败方案和可复用经验。

候选记录先进入 `pending` 事件，再由 Memory Manager 校验来源、脱敏和重复性，最后写入 `active`。

## 5. 禁止写入

- 原始长聊天记录和无结论的中间推理。
- 未验证推测、临时假设或没有来源的建议。
- Token、密码、私钥、Cookie、个人敏感数据和完整凭据。
- 与项目无关的外部内容。
- 被撤销决策的复制文本；应保留原记录并建立 supersedes 关系。

## 6. 检索规则

检索必须支持 `project_id`、`type`、`status`、`tags`、时间范围和来源过滤。排序优先级为：当前项目 > 直接来源 > active 状态 > 置信度 > 最近更新时间 > FTS 相关性。

低于 `0.7` 的记录只能作为参考，必须在结果中显示低置信度；跨项目记录必须显示原项目、适用范围和复用理由。

## 7. 更新、冲突和失效

- 新决策不得更新覆盖旧决策；建立 `supersedes` 关系并保留旧记录。
- 冲突记录必须关联 ADR 或人工确认事件。
- 过期使用 `expires_at`，撤销使用 `revoked`，删除使用 `deleted`；三者都保留审计事件。
- 事实文档变更导致 `source_hash` 不匹配时，记录进入 `needs_review`，不得继续作为高置信度事实；复核完成后只能通过新版本记录恢复 `active`。

## 8. 隐私与跨项目隔离

默认 `scope=project`。只有用户明确批准且完成脱敏的经验才能提升为 `organization` 或 `public`。检索必须先按项目范围过滤，禁止跨项目自动泄露内容。

## 9. 完成定义

Memory 规范只有在分类、Schema、写入、禁止写入、检索、更新、失效、删除、来源、脱敏和跨项目隔离均有对应测试时才算完成。
