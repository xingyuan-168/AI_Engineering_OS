# 文档索引

AI Engineering OS 是 Codex 的工程治理层。本目录只保存当前正确的实现事实；Git 历史是唯一归档。

## 阅读顺序

1. 仓库根目录 `AGENTS.md`（十条宪法）。
2. 按任务加载下表文档，不再有长链必读。

## 文档地图

| 文档 | 内容 | 何时读 |
| --- | --- | --- |
| REQUIREMENTS.md | 治理层需求基线（八条目标） | 设计取舍时 |
| SCOPE.md | 范围内 / 范围外 | 判断需求是否进 Core |
| ARCHITECTURE.md | 分层架构与边界 | 改动结构时 |
| GOVERNANCE_RULES.md | 三 Gate、路径策略、Hook 语义、规则优先级 | 任何治理行为 |
| MEMORY.md | Memory 双层存储与单写者契约 | 记录/检索经验时 |
| WORKTREE.md | disposable worktree 协议 | 并行任务时 |
| FRONTEND_GATE.md | 前端批准流程与豁免 | 前端任务时 |
| OPEN_SOURCE_RESEARCH.md | 开源调研记录（本仓库自身） | 引入新依赖时 |
| TEST_PLAN.md | 默认验证与测试映射 | 跑验证前 |
| API_SPEC.md | MCP 7 工具 + CLI 命令契约 | 调用接口时 |
| DATABASE.md | SQLite 四表与 memory_index | 动 schema 时 |
| CHANGELOG.md | 变更记录 | 发布/合并前 |
| ADR/ | 已接受/已否决的架构决策 | 重大决策前后 |
| design/ | 前端设计基线（README + UI_SPEC） | 前端任务时 |

## 派生缓存

`.codex-os/context/PROJECT_CONTEXT.md` 由 `codex-os` 重新生成，可随时重建，永不作为事实源、永不手工编辑。
