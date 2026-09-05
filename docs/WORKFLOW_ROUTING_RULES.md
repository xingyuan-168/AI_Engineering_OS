# Workflow 路由规则

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"review-ready","owner":"product-manager","requirement_refs":["REQ-1.6.2","ROUTING-001"]} -->

版本：V2.0-derived-routing
状态：可执行实现规格基线
策略：可解释评分 + 人工覆盖。

## 1. 评分维度

总分 0-10：

| 维度 | 分值 | 判断依据 |
| --- | --- | --- |
| 功能范围 | 0-2 | 独立功能、多个业务域、跨模块 |
| 系统边界/外部集成 | 0-2 | 单模块、多个服务、外部系统/协议 |
| 数据/安全/合规风险 | 0-2 | 无敏感数据、内部数据、敏感/合规数据 |
| UI/前端工作量 | 0-1 | 无 UI 或单页面、完整交互界面 |
| 多 Agent/并行协作 | 0-1 | 单角色、两角色、三角色以上 |
| 部署/恢复/迁移 | 0-1 | 无迁移、可回滚迁移、复杂发布/恢复 |
| 不确定性/开源调研 | 0-1 | 目标明确、需要研究或方案比较 |

评分规则必须对相同输入产生相同结果，并记录每个维度的理由。

## 2. 基础路由

- 明确是 Bug 且无架构、数据库或安全重大影响：`bug-fix`。
- 明确是发布动作：`release`。
- 没有既有项目基线或从零建立项目：`new-project`。
- 已有项目且总分 0-6：`feature-development`。
- 总分 7-10、风险为 high/critical，或需要三个以上 Agent：`large-project` 候选，并强制人工确认。
- 涉及 UI：增加 `frontend-project` profile。
- 明确后端/数据库工作：增加 `backend-project` 或数据库 Agent profile。

## 3. 路由输出

```yaml
workflow: feature-development
profiles: [backend-project]
score: 5
risk_level: medium
reasons:
  - "涉及 API 和数据库，但不涉及外部系统"
approval_required: false
human_override: null
```

路由决策必须写入 `routing_decisions` 和事件日志，并作为 `intake` 的输出交给 Workflow Engine。

## 4. 覆盖规则

- 用户显式指定 Workflow 时保留用户选择，但执行兼容性检查。
- 用户选择与风险策略冲突时进入 `needs_approval`，不能直接降低安全等级。
- 分数处于边界、信息不足或多个 Workflow 同分时请求人工确认。
- 人工覆盖必须记录原路由、覆盖理由、批准人和时间。

## 5. 失败与复盘

无法计算评分时进入 `blocked`，列出缺失输入。历史路由可按输入 hash、分数、结果和人工覆盖检索，用于改进规则；V1 不使用黑盒模型直接决定 Workflow。

### 5.1 校准责任与变更纪律

- Product Manager 是路由校准负责人；每季度或每完成 20 个 Workflow（先到者）复核一次。
- 复核至少统计人工覆盖率、误入 `large-project` 的比例、因路由导致的 blocked/reopen、返工率和交付 lead time，并保留使用的数据范围与结论。
- V1 不根据历史记录自动更新权重或阈值。任何评分维度、权重或 `7` 分边界变更都必须通过 ADR，更新 Profile/Effective Policy hash、测试向量和兼容说明后才能生效。
- 样本不足或指标相互矛盾时维持当前规则，不以个人印象调整线上策略。

## 6. 完成定义

路由规范只有在评分可重复、基础路由、profile 组合、人工覆盖、冲突处理、事件记录和失败恢复均有测试时才算完成。
