# ADR-0005：治理权威、规格一致性与自修改规则

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"approved","owner":"architect","requirement_refs":["REQ-1.6.2","GOV-001","DOC-001","VERSION-001"]} -->

- 状态：Accepted
- 日期：2026-08-31
- 决策者：项目所有者

## 1. 上下文

仓库曾同时在 `AGENTS.md`、项目总文档和历史执行入口中维护读取顺序，并在配置、Workflow、Agent、Skill、ID 与测试文档中复制实现事实。重复的权威定义已经产生阶段入口、角色、文档 metadata 和测试要求漂移。

## 2. 决策

1. `PROJECT_MASTER.md` 第 3 节是实现契约读取顺序的唯一事实源；其他入口只提供引用和导航。
2. 历史入口材料不拥有治理职能，也不能覆盖活跃规格。
3. Workflow 可以从注册表声明的入口阶段开始；进入后必须按状态机顺序推进并满足适用 Gate。
4. Skill、Agent、ID、阶段和测试追溯必须由自动检查与仓库实际资产保持一致。
5. 修改指令优先级、Gate、执行/安全策略、角色权限、生命周期、ID 或审计留存等治理语义，必须具备 maintenance authority、独立 Review 和 accepted ADR。非语义性的 metadata、链接、错别字和排版修复不强制新增 ADR。

## 3. 后果

- `AGENTS.md` 不再维护第二份读取顺序或验证矩阵。
- 活跃文档 metadata 和测试追溯成为文档治理检查的一部分。
- 规格与实现漂移会在测试阶段 fail closed，而不是依赖人工发现。
- 历史文档、旧分支和审计记录继续保留，但不能作为当前执行规则。

## 4. 被否决的选项

1. **保留多份同等权威顺序**：无法定义冲突时的确定性选择。
2. **以实现代码完全替代规格**：会丢失用户可 Review、可 diff 的治理契约。
3. **所有文档修正都强制 ADR**：会让无语义变化的维护产生不必要审批成本。

## 5. 验证

- 文档检查验证唯一读取顺序、metadata、链接和追溯映射。
- 资产一致性测试比较规范 Skill/Agent 清单与仓库目录。
- 生命周期测试比较 Workflow 入口表与运行时注册表。
