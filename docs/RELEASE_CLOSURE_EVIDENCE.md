# AI Engineering OS 0.2.0 收口证据快照

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"approved","owner":"release-manager","requirement_refs":["REQ-1.6.2","RELEASE-001","GATE-001","EXEC-001"]} -->

## 证据边界

本文件记录 `codex/0.2.0-release-closure` 的开发收口验证，不是 G3 审批、G4 发布授权或 GitHub Release 记录。验证源 Commit 为 `68437bbdaf59e1bc8a03b01b34aec6935e0da5e6`，其父级代码收口包含 draft Release/资产对账、完整 Gate Policy、公共契约矩阵、恢复测试和覆盖率门槛。正式候选仍须由新建的 API 1.2 Release Workflow 在批准的离线缓存和 OCI 环境中重新构建并形成 Commit-bound Check Evidence。

## 自动验证

验证日期：2026-08-29（Asia/Shanghai）。

| 检查 | 结果 | 证据摘要 |
| --- | --- | --- |
| 全量 pytest | passed | `248 passed`，`0 skipped`，包含单元、集成、E2E、迁移、恢复、发布故障注入和真实 OCI |
| 总体覆盖率 | passed | branch-aware total `86.89%`，门槛 `85%` |
| 变更行覆盖率 | passed | `diff-cover ab879c0...68437bb`：2569 行、256 行未覆盖、`90%` |
| Ruff | passed | 全仓无 finding |
| Pyright | passed | `0 errors, 0 warnings` |
| Secret scan | passed | 230 个 Git 跟踪文件、0 finding |
| 文档与 diff | passed | formal docs check 与 `git diff --check` 通过 |
| 真实 Podman OCI | passed | 显式 `CODEX_OS_REAL_OCI=1`；非 root、断网、只读 rootfs、cap-drop、no-new-privileges、CPU/内存/PID 限制均验证 |

## 干净克隆安装

从远端 `codex/0.2.0-release-closure@68437bbdaf59e1bc8a03b01b34aec6935e0da5e6` 执行单分支干净 clone，在 clone 外新建 Python 3.12.13 虚拟环境，从该 clone 构建并安装 wheel。安装后的运行时返回软件 `0.2.0`、API `1.2`，`codex-os doctor --json` 返回 `ok=true`。

本次开发自举制品仅用于安装验证，不得作为正式 Release 资产：

| 制品 | SHA-256 | 字节 |
| --- | --- | ---: |
| `codex_ai_engineering_os-0.2.0-py3-none-any.whl` | `294ec9d4448ab268791c6e2e5ffc0c244ccae7f8f0d49596fb77a4dd9d9b0a78` | 193043 |
| `codex_ai_engineering_os-0.2.0.tar.gz` | `13711aedd61c6921505958e3b293464239c6e24f7a5e66ab3b41a72808b96d09` | 436953 |

正式 `release_prepare` 必须在锁定 OCI、批准 wheelhouse 和固定 `SOURCE_DATE_EPOCH` 下重新生成候选资产；不得复用上述临时目录或开发构建 hash。

## 环境事实

- `podman-machine-default`：running，WSL VM，2 CPU、4 GiB 内存。
- Podman：5.8.6；真实 OCI 测试已运行且零 skip。
- 执行镜像：Python 3.12.14 Bookworm，index digest `sha256:852282e520cc1754221fb2e061ab35b13b596e8112a731d60e2a8b471c973b7a`；本地 amd64 image ID `sha256:4a545b585af74088149994e3319fdc270e3a3f13181d23ed955a2a42261ee17b`。
- GitHub CLI：已安装 `gh 2.98.0`，但尚未认证任何 GitHub host；无法执行 PR、draft Release、远端资产下载复核或发布对账。

## 未关闭阻塞

以下项目仍使正式发布状态保持 `blocked`：

1. 尚未生成并批准本次正式 Workflow 使用的只读 wheelhouse、pip-audit snapshot 和 Trivy DB snapshot。
2. 尚未归档正式镜像 CycloneDX SBOM、Trivy 报告和 high/critical finding 审批结论。
3. 尚未使用 API 1.2 新建真实自举 Release Workflow，并以该 Workflow 的状态、Task Group、Handoff、G3、Memory、resume/reconcile 记录形成审计包。
4. `gh 2.98.0` 已安装但未认证，GitHub 权限与远端发布对账不可执行。
5. 未创建 PR 合入 `main`，未取得独立 G4 授权；不得创建 `v0.2.0` tag 或 GitHub Release。

## 下一动作

安装并认证 `gh` 后，创建新的 1.2 Release Workflow，执行并批准 verification cache prepare，离线运行完整 G3 并归档结构化证据。G3 通过后创建 PR；PR 合并且取得独立 G4 授权后，只能由持久化 `release_publish` Host Operation 对账 tag、draft Release、final manifest 和全部远端资产 hash。
