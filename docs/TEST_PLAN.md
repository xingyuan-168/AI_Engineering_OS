# AI Engineering OS 0.2.0 测试计划

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"approved","owner":"qa","requirement_refs":["REQ-1.6.2","GATE-001","RELEASE-001","EXEC-001","MEMORY-001","ROUTING-001","FRONTEND-001"]} -->

测试以 `REQ-1.6.2`、Plugin API/配置/文档/Profile 1.2、SQLite 0007 和 [ADR-0004](ADR/ADR-0004-release-closure-transaction-boundaries.md) 为基线。任何跳过的必需真实检查都视为未通过，不以 mock 或自由文本替代。

## 1. 测试层级

- 单元：版本对象、严格模型、状态转换、Gate Policy、路由评分、路径和命令策略。
- 集成：应用服务、SQLite、迁移/恢复、Git/Worktree、Host Operation、Release staging、CLI/MCP 契约。
- 端到端：1.2 自举 Workflow 从 Routing、并行任务和 Handoff 到 G3、Release、Memory 与恢复。
- 真实环境：Podman OCI、干净克隆安装、全新虚拟环境、GitHub PR/tag/Release 对账（仅有独立 G4 授权时执行写操作）。
- 安全：Secret、依赖、Bandit、镜像/Trivy、路径、身份、命令、缓存和发布权限。

## 2. 迁移与数据库

- fresh install `0001 -> 0007`、真实 `0006 -> 0007`、重复运行与迁移 checksum 冲突。
- WAL checkpoint、备份 checksum、备份可打开、失败库保留、临时库恢复校验与原子替换。
- `integrity_check=ok`、`foreign_key_check` 空、FTS rebuild/query、索引和触发器。
- 旧 Workflow 保留审计并进入 `MIGRATION_REVALIDATION_REQUIRED`；不得伪造新 Task Group、Review 或 Evidence。
- Handoff accepted 后 merge/push 失败仍 accepted；Memory/Task/Workflow/Operation 版本单调。
- 配置/文档/Profile 1.0/1.1 兼容读取及弃用 warning；旧文本证据不能满足 Gate。
- 回滚恢复失败、磁盘/替换失败和迁移锁并发均返回可恢复、可审计错误。

## 3. Workflow、并发与故障注入

- G2 创建 integration Worktree、G3 创建 release Worktree 的事务前/后进程中断。
- 至少三个 Agent 并行，最多恢复四个 ready 任务；路径重叠自动建依赖，无法映射时阻塞。
- task/group/handoff/workflow/operation expected-version 并发冲突只有一个提交成功。
- 本地 merge 后 push 失败、Git 副作用后进程崩溃、租约过期接管、remote ref/ancestry 对账。
- 部分 Release 资产上传、已有相同/冲突 tag、已有 draft Release、候选残留、重复幂等请求和 request hash 冲突。
- `status`、`workflow_step` 使用只读连接，不迁移、不创建目录、不分配任务、不推进状态。

## 4. Commit-bound Evidence 与 Gate

- 文档、Review、报告和候选文件从 `git show <commit>:<path>` 或受管审计区读取；未提交文件不能改变结果。
- 40 位 SHA-1 与 64 位 SHA-256 Commit、错误 object format、不可达 Commit、hash/Commit 漂移。
- G0 缺 Routing Decision；G2 缺 Migration Spec 或适用 Accepted ADR；G3/G4 分别缺任一必需检查/制品时阻塞。
- 审批提交错误 `evidence_bundle_hash`、过期 state/task/memory/operation version、Review 后新 Commit。
- Review findings 必须是 `id/severity/status/summary` 对象；开放 high/critical finding 禁止 accepted。
- 非零检查必须生成 failed Check Evidence 和可恢复错误，不能构造 passed + 非零退出码。

## 5. Verification、构建与供应链

- verification prepare 生成与 `uv.lock`、平台和有效期绑定的只读 wheelhouse、pip-audit snapshot 和 Trivy DB snapshot。
- 正式 G3 在 `--network none` 下离线消费缓存；缺失、过期、hash/platform 漂移均阻塞。
- Ruff、Pyright、pytest+coverage、文档、Secret、依赖、Bandit、Plugin/Skill/Hook/MCP、构建安装、镜像扫描和真实 OCI 全部形成结构化证据。
- Python 3.12.14 full Bookworm 保存 registry index digest、平台 digest、SBOM 和 Trivy 报告；未批准 high/critical finding 阻塞。
- Release build 在 OCI 中使用批准 wheelhouse 和可复现时间；staging 原子提升，重试时重新计算每个 hash。
- wheel/sdist 在干净克隆与全新虚拟环境安装，版本、CLI、Plugin 资源和导入均验证。

## 6. Routing、Plugin 与公共契约

- 七维 Routing Input、0～10 评分、risk、workflow、canonical profiles、理由和人工覆盖在 G0 前持久化。
- `backend-project`、`frontend-project`、`large-project` 为规范名；短名只返回 warning。
- Profile YAML 是资源事实源；impact paths 派生 allowed paths 与依赖；不安全映射阻塞。
- `frontend-engineer` Agent、`frontend-implementation` Skill、manifest、Profiles、MCP server 与发行包版本一致。
- 逐工具比较 CLI JSON、MCP Schema、应用输入/输出类型与 API 文档；覆盖 Release Candidate、Memory、Migration、Verification Prepare 和 Host Operation。
- 官方 Plugin validator、Skill validator、Hook fixture 与真实 stdio MCP 必须通过。

## 7. 安全负向矩阵

- 绝对路径、`..`、UNC/device/ADS、symlink/junction、managed Worktree/coordinator root 混淆。
- 请求体自报 producer/reviewer/approver、producer 自审、过期/错误 InvocationContext。
- dirty Worktree、未知文件、无沙箱、无 `gh`、Podman machine 停止、无镜像或 digest 不匹配。
- shell 注入、非 allowlist argv、网络开启、root、可写根、capability/socket 挂载、资源/超时限制。
- Trivy DB 过期、镜像 high/critical finding、Secret 进入日志/SQLite/Memory/FTS/审计摘要。

## 8. 质量门槛与发布顺序

1. Ruff、Pyright、全部单元/集成/E2E、文档、Secret、依赖、代码和镜像扫描全部通过。
2. 总体分支覆盖率 `>=85%`；变更行覆盖率 `>=90%`。
3. 真实 Podman 测试必须运行且通过，不得 skip。
4. 使用 API 1.2 新建真实自举 Release Workflow，旧未完成 Workflow 只保留历史证据。
5. G3 后才能以 PR 合入 `main`；只有独立 G4 授权后才能由持久化 publish operation 创建 `v0.2.0` 和 GitHub Release。
6. 缺少 Podman、`gh`、网络审批、GitHub 权限或独立 G4 授权时，开发验证可以继续，但相应 Gate 必须明确保持 blocked。

## 9. 需求与测试追溯

`.codex-os/test-traceability.yaml` 是机器可校验的追溯事实源。每项记录必须包含唯一 ID、Requirement refs、规格路径和真实测试文件；文档治理扫描会拒绝缺失文件、重复 ID和未映射 Requirement。

| 追溯组 | 覆盖范围 | 主要测试层 |
| --- | --- | --- |
| `TRACE-CORE-GOVERNANCE` | 总体、配置、文档和版本治理 | 规格一致性、文档、配置单元测试 |
| `TRACE-REPOSITORY-HYGIENE` | 仓库准备度、卫生和 Secret | 治理与 Secret 负向测试 |
| `TRACE-GATE-EVIDENCE` | G0-G4、Evidence、审批 | Evidence、正式检查和 Workflow 集成测试 |
| `TRACE-AGENT-COORDINATION` | Routing、Agent、Handoff、Worktree | 协调、路由和 Worktree 测试 |
| `TRACE-FRONTEND-PROTOTYPE` | 前端 Profile、原型与 UX Review | Prototype/Profile 测试 |
| `TRACE-EXECUTION-SANDBOX` | Execution 和真实 OCI | Execution/Podman 测试 |
| `TRACE-RELEASE` | Candidate、G4 与发布对账 | Release/G4 故障注入 |
| `TRACE-MEMORY` | Memory 生命周期和隔离 | Memory 集成测试 |

## 10. 完成定义

测试完成必须提供 Branch、Commit、remote/push、命令、退出码、报告路径/hash、开始/结束时间、跳过数和覆盖率。所有活跃规格的完成定义和 Requirement 必须有有效追溯记录；任何 required check 缺失、跳过、来源 Commit 不一致或证据不可复算，都不能将 0.2.0 标记为发布完成。
