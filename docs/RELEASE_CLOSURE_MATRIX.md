# AI Engineering OS 0.2.0 发布收口矩阵

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"approved","owner":"product-manager","requirement_refs":["REQ-1.6.2","VERSION-001","GATE-001","RELEASE-001","EXEC-001","MEMORY-001","ROUTING-001","FRONTEND-001"]} -->

本矩阵把 `ab879c0` 候选基线的已知缺口绑定到实现、自动测试和 Gate。行状态只有在代码、文档、测试和可定位证据全部完成后才能关闭。

| ID | 缺口/风险 | 0.2.0 交付物 | 必需自动验证 | Gate |
| --- | --- | --- | --- | --- |
| `VERSION-DRIFT` | Release、G4、Evidence、DocumentManager 和 Plugin 各自硬编码版本 | `RuntimeVersions` 唯一对象；API/配置/文档/Profile 1.2；SQLite 0007 | 版本矩阵单元测试、CLI/MCP/manifest/plugin 契约测试 | G2、G3、G4 |
| `PROJECT-ROOT` | Worktree 内调用可被绝对配置静默重定向到旧 checkout | 可移植项目根解析；受管 Worktree 明确拒绝/引导；Gate 绑定登记来源 | 绝对路径、junction/symlink、managed root 混淆测试 | G2、G3 |
| `HOST-OP` | 审批提交后外部副作用无持久化 intent | `host_operations`、租约、尝试、请求 hash、幂等和调用审计 | 事务边界、崩溃、租约接管、重复请求、结果未知对账 | G2、G3、G4 |
| `MERGE-RECOVERY` | 本地 merge 后 push 失败会破坏 accepted Handoff | 保存 merge Commit/push pending；accepted 保持不变 | push 失败、进程中断、ancestry/remote ref 恢复 | G3 |
| `G4-RECOVERY` | 发布先于授权持久化且资产部分上传不可安全重放 | durable publish operation、draft Release、final manifest、资产 hash 对账 | 已有 tag、已有 draft、部分资产、远端结果未知、重复请求 | G4 |
| `EVIDENCE-COMMIT` | 未提交 Worktree 文件可替代 Commit 证据 | `git show`/审计区读取、40/64 位 Commit、bundle hash | 未提交文件欺骗、错误 Commit、hash 漂移、Review stale | G0-G4 |
| `VERIFY-CACHE` | 正式检查会隐式联网且无普通 prepare 接口 | lock-bound wheelhouse、pip-audit/Trivy snapshot、离线正式验证 | 缓存过期、lock/platform/hash 漂移、网络关闭、非零检查证据 | G3 |
| `RELEASE-IDEMPOTENCY` | 随机 SBOM 标识、残留半成品和已存 hash 可被复用 | 可复现时间、稳定 SBOM 标识、staging 原子提升、重算 hash | 候选残留、重试、candidate/source Commit、clean install | G3、G4 |
| `API-PARITY` | CLI、MCP、应用模型和文档工具集合/响应不同 | 统一 1.2 输入、响应、错误码和写并发契约 | 逐工具 Schema/JSON/类型比较；1.0/1.1 warning | G3 |
| `ROUTING-ASSETS` | 路由短名与 Profile 资源冲突，前端 Agent/Skill 缺失 | 七维输入、YAML 事实源、canonical profiles、前端资产 | 评分、override、alias warning、路径依赖、资产 validator | G0、G3 |
| `OCI-SUPPLY` | 镜像版本冲突且缺少平台 digest/SBOM/扫描 | Python 3.12.14 full Bookworm、digest provenance、SBOM、Trivy | 真实 Podman、离线扫描、high/critical 阻塞、资源策略 | G3 |
| `LEGACY-REVALIDATION` | 旧活动 Workflow 可能被伪造成 1.2 可恢复状态 | 原审计保留；`MIGRATION_REVALIDATION_REQUIRED`；不造任务组 | 0006→0007、恢复、FTS/FK、兼容和重验证测试 | G2、G3 |

## 关闭纪律

1. P0/P1 行未关闭时不得请求 G4。
2. “测试通过”必须引用绑定 Commit 的结构化 `CheckEvidence`；自由文本不计入。
3. 环境缺少 Podman machine、`gh`、有效 Trivy snapshot 或 GitHub 权限时，开发诊断可以继续，但真实 G3/G4 保持 blocked。
4. 当前实施请求不构成合并 `main`、创建 `v0.2.0` 或发布 GitHub Release 的独立 G4 授权。

## 2026-08-29 收口快照

- 发布对账、完整 G3/G4 证据策略、公共契约矩阵和故障恢复实现已落地到 `68437bb`；相对 `ab879c0` 共 26 个逻辑提交。
- 全量验证为 `248 passed`、`0 skipped`；总体 branch-aware 覆盖率 `86.89%`，变更行覆盖率 `90%`，真实 Podman OCI 已实际运行。
- 最新远端 Commit 已通过干净 clone、构建、独立虚拟环境 wheel 安装和 `doctor` 冒烟测试。
- Podman machine 当前运行，锁定 Python 3.12.14 Bookworm 镜像已存在；`gh 2.98.0` 已安装但未认证，因此真实 GitHub PR/draft Release/资产对账和 G4 发布保持 blocked。
- 详细证据与仍未关闭的正式发布前置条件见 [RELEASE_CLOSURE_EVIDENCE.md](RELEASE_CLOSURE_EVIDENCE.md)。
