# 需求基线

蒸馏自已删除的 PRODUCT_REQUIREMENTS / USER_STORY / BUSINESS_RULES（Git 历史可考古）。AI Engineering OS 只解决以下八条，其余默认不进 Core：

1. **GitHub 前置**：正式开发前必须有可达的 GitHub remote；没有时只允许分析、调研、规划与文档工作。
2. **开源调研优先**：新需求/新模块/重大功能/新技术栈/新集成必须先查成熟开源方案，并在 `docs/OPEN_SOURCE_RESEARCH.md` 记录 use / fork / extract / build 决策；小修与既有方案内改动豁免。
3. **禁止复制式版本管理**：不创建 src_v2/、backup/、copy/ 等副本；仓库保持干净，历史由 Git 保存。
4. **文档维护**：只维护受本次变更影响的文档，且必须与变更同任务完成。
5. **子 Agent + Worktree**：复杂并行任务用 Codex 原生子 Agent；并行写用 `.worktrees/` 隔离并及时清理。
6. **前端人工确认**：实质前端工作先出可审核的交互原型 + UI 方案，用户批准后才编码。
7. **任务结束清垃圾**：一次性脚本、缓存、调试文件删除且不进 Git。
8. **项目长期记忆**：只沉淀架构决策、Bug 根因、失败经验与可复用模式（decision/bug/lesson/pattern + 可选 project-summary），单写者规则，拒绝聊天与过程日志。

验收：规格 §40 A-L（ADR-0016 收录）+ Phase 6 Dogfood 四案例。
