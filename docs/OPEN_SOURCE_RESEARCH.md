# Open Source Research

## Requirement

requirement_id: REQ-GC-1.0
summary: governance-core 轻量化重构（ADR-0016）：把过度工程化的治理运行时收敛为无状态三 Gate + 轻量 Memory/Worktree，删除自有执行平台。
scope:
  - gates
  - memory
  - worktree
updated_at: 2026-09-11

## Candidates

### LangGraph / CrewAI / MetaGPT / OpenHands
- URL: https://github.com/langchain-ai/langgraph 等
- License: MIT 系
- 解决什么：多 Agent 编排、状态图运行时。
- 可直接复用：否——本层只做治理，不做 Agent 运行时。
- 可二开：否——引入即违反架构边界（ADR-0016 §2.12）。
- 值得学习：状态机的显式建模思想；本项目刻意反其道用无状态评估器。
- 风险：依赖膨胀、把 AIOS 重新拉回 Agent 平台。

### detect-secrets
- URL: https://github.com/Yelp/detect-secrets
- License: Apache-2.0
- 解决什么：Secret 检测。
- 可直接复用：是——作为轻依赖，只扫本次修改/staged diff。
- 可二开：否。值得学习：插件式规则。风险：低。
- 备注：AGENTS.md 的仓库 Secret Scan 封装。

### SQLite FTS5（标准库/编译选项）
- 解决什么：Memory 全文检索。
- 决策相关：放弃——单项目记忆量小，LIKE 检索 + reindex 足够，避免 FTS 维护与构建门槛。

## Decision

decision: build
reason: 治理层必须确定、可审计且不引入第二运行时；全部核心能力（三 Gate、审批、SQLite、JSONL Memory、worktree 生命周期）自研约三千余行即可覆盖，任何 Agent 框架依赖都会重建已删除的平台层。detect-secrets 以 use 方式作为轻依赖直接复用。
