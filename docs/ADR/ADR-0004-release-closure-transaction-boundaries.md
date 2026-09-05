# ADR-0004：0.2.0 发布收口、事务边界与证据来源

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"approved","owner":"architect","requirement_refs":["REQ-1.6.2","VERSION-001","GATE-001","RELEASE-001","EXEC-001","MEMORY-001","ROUTING-001"]} -->

- 状态：Accepted
- 日期：2026-08-26
- 决策者：项目所有者
- 取代：ADR-0003 中关于 0.2.0 接口/数据库版本和 slim 执行镜像的冻结值；其余边界继续有效

## 1. 上下文

`workflow/RUN-20260824014314648609-97E87866/integration@ab879c0` 已形成可运行候选，但尚未形成正式发布。现有实现仍存在版本硬编码、跨 SQLite 与 Git/GitHub 副作用的半完成状态、以活动 Worktree 内容替代 Commit 内容的证据读取、发布重放不完整、路由资源缺失和 OCI 镜像引用冲突。

这些问题不能通过修改提示词或在发布清单中人工确认来解决。它们需要一个可迁移、可恢复且可测试的 Runtime 契约，并需要在 G3/G4 前以持久化状态证明每个外部副作用的授权和结果。

## 2. 决策

### 2.1 版本矩阵

0.2.0 的唯一冻结矩阵为：

| 维度 | 冻结值 |
| --- | --- |
| 需求基线 | `REQ-1.6.2` |
| 软件、CLI、Plugin 发行版 | `0.2.0` |
| Plugin API | `1.2` |
| 配置 Schema | `1.2` |
| 文档 Schema | `1.2` |
| Profile Schema | `1.2` |
| SQLite Schema | `0007` |
| Git tag | `v0.2.0`，仅在独立 G4 授权后创建 |

Runtime 通过一个不可变的 `RuntimeVersions` 对象提供全部版本值。Release、G4、Evidence、DocumentManager、CLI、MCP 和 Plugin 资产不得各自硬编码版本。

Plugin API、配置、文档和 Profile 的 1.0/1.1 兼容入口保留至 0.2.x 结束，并返回弃用 warning；最早只可在 0.3.0 通过新 ADR 移除。旧自由文本证据不得满足 1.2 Gate。

### 2.2 持久化 Host Operation

`integration_prepare`、`integration_merge`、`release_prepare` 和 `release_publish` 是持久化 Host Operation。审批、期望状态版本、证据包 hash、幂等键、请求 hash 和待执行操作必须在一个 SQLite 事务内先写入，再执行 Git、OCI 或 GitHub 副作用。

Operation 状态为 `pending | running | succeeded | failed | reconcile_required`，并记录租约、尝试次数、结果摘要和脱敏调用审计。进程在副作用后崩溃或远端结果未知时，恢复器必须先查询 Git ancestry、remote ref、tag、draft Release 和资产 hash，再决定补执行或完成对账。

本地 merge 成功但 push 失败时保留 merge Commit 和待推送状态。已经 accepted 的 Handoff 保持 accepted；merge/run 可进入 blocked，但不得将审核事实改写为 rejected。G4 必须按“持久化授权 -> 验证已合并 PR -> annotated tag -> draft Release -> final manifest -> 资产上传与复核 -> 发布 -> Workflow 完成”执行。

### 2.3 Commit-bound Evidence

文档、Review、报告和发布制品必须从 `git show <commit>:<path>`、Git object 或受管只读审计区读取并重新计算 hash。活动 Worktree 的未提交文件不能替代 Commit 内容。Commit 校验同时支持 40 位 SHA-1 与 64 位 SHA-256 object format。

Gate 审批除期望状态版本外还必须提交当前 `evidence_bundle_hash`。证据、Commit 或状态漂移分别产生 `EVIDENCE_STALE`、`REVIEW_STALE` 或 `STATE_VERSION_CONFLICT`。

### 2.4 官方 Bookworm 执行镜像

正式验证和构建使用当前代码冻结的完整官方 Python Bookworm 引用：

```text
python:3.12.14-bookworm@sha256:852282e520cc1754221fb2e061ab35b13b596e8112a731d60e2a8b471c973b7a
```

该值是发布输入而不是安全结论。G3 必须额外保存 registry index digest、实际宿主平台 digest、镜像 SBOM 和 Trivy 报告。存在未获批准的 high/critical finding、缓存过期或无法证明实际拉取 digest 时保持 blocked。

经网络审批的 `verification prepare` 生成与 `uv.lock`、平台和有效期绑定的只读 wheelhouse、pip-audit snapshot 与 Trivy DB snapshot。正式 G3/G4 只离线消费这些已批准缓存；构建不得从网络临时补依赖。

### 2.5 项目根与来源 Worktree

公共调用从受管任务 Worktree 发起时，必须明确拒绝并返回 coordinator root 指引，或要求调用方显式提交已登记的 coordinator root。禁止根据配置中的绝对路径静默切换到另一检出目录。Gate 始终使用数据库登记的 source Worktree 和 source Commit。

## 3. 被否决的选项

1. **沿用内存中的待执行动作**：进程崩溃后无法区分未执行和结果未知，不能提供 exactly-once 等价行为。
2. **以当前 Worktree 文件为证据**：未提交内容可改变审核结果，破坏 Commit 可追溯性。
3. **发布阶段先执行 GitHub 再写 SQLite**：失败时丢失授权与幂等上下文，无法安全重放。
4. **继续使用 1.1/0006 标识实现新增契约**：调用方无法判断并发、证据和迁移语义是否可用。
5. **为对齐文档做大规模目录重构**：不增加发布正确性，却显著扩大回归面；本轮只修正文档模块映射。

## 4. 后果

- 新增 SQLite `0007`，不得修改 `0001`～`0006`。
- 写接口增加期望版本、幂等键和受信 `InvocationContext`；旧显示字段不能提升权限。
- `status`、`step` 的只读计算路径不得迁移、分配任务或推进状态。
- 候选 Manifest 同时记录 integration source Commit 与提交候选文件的 candidate Commit；final manifest 在 G4 对账阶段产生。
- 旧活动 Workflow 保留原审计记录。证据不足时进入 `MIGRATION_REVALIDATION_REQUIRED`，不得伪造新 Task Group 或新 Gate 证据。
- Profile Schema 1.2 是任务模板、影响路径模式、增量 Gate 证据和 Reviewer 的声明式事实源；Runtime 将核心规则与所选 Profile 编译为带 hash 的 `EffectiveGovernancePolicy`。缺失、未知或试图放宽核心规则的策略必须 fail closed。
- CQ-OS `400a930` 是治理机制研究基线；仅将明确声明 MIT 的 `@cq/governance` 0.1.0 包中的单调 Baseline + Project 合并、默认拒绝、保护路径匹配及测试向量移植为独立 Python 实现并保留 attribution。其余源码只作单一调度、角色边界、路由审计和契约自检参考，不引入 Node.js/DSH/Cordis 或 `.cq/` 第二事实源。
- 0.2.0 不包含 Web 控制台、DeepSeek Harness、公共插件市场、远程多租户或生产部署。

## 5. 验证

本决策由 [RELEASE_CLOSURE_MATRIX.md](../RELEASE_CLOSURE_MATRIX.md) 逐项绑定实现、迁移、故障注入、契约、安全和真实 OCI 测试。任何 Host Operation、Commit-bound Evidence、官方 Bookworm 镜像或版本矩阵未实现时，G3/G4 必须保持 blocked。
