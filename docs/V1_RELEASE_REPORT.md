# AI Engineering OS V1 发布候选验收报告

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"review-ready","owner":"release-manager","requirement_refs":["REQ-1.6.2","RELEASE-001"]} -->

日期：2026-08-21

版本：`0.1.0`

实施分支：`codex/m6-v1-expansion`

验收基线提交：`cae41b67351fafbc5c086ba7fe01a7ba50b8841c`

状态：M0-M6 实现、自动化验收与真实 Podman OCI 复验完成；发布和合并因 Codex Hook 信任复核与 G4 人工批准未完成而保持 `blocked`。

## 1. 已落地范围

- Python 3.12/uv 本地运行时、严格配置、SQLite 0001-0003 迁移、事件、原子文档和治理检查。
- `new-project`、`feature-development`、`bug-fix`、`release` 双轴工作流，G0-G4、暂停/恢复、幂等任务和审批。
- Git push、提交、允许路径、制品 hash、Handoff 和干净 Worktree 完成证据。
- Docker 与 Podman OCI 适配器：固定 digest、非 root、默认断网、只读根、最小权限、显式挂载和资源限制。
- Codex 私有 Plugin、19 个 Skill、7 个 Agent、stdio MCP 11 个工具和可信生命周期 Hooks。
- frontend/backend/large 增量 Profile 与项目隔离、来源可验证的 Memory 生命周期。
- 空仓库到 FastAPI + SQLite ERP 采购发布候选的端到端试点。

明确排除：DeepSeek Harness、公开插件、Web UI、FastAPI 控制面和生产部署。

## 2. M6 Git 证据

| 逻辑单元 | Commit | 远端状态 |
| --- | --- | --- |
| V1 变更和发布工作流 | `2823701` | 已推送 `origin/codex/m6-v1-expansion` |
| Memory 来源校验生命周期 | `4fe95cd` | 已推送 |
| Podman 沙箱适配 | `1b31dba` | 已推送 |
| V1 Skills 与 Profiles | `ed0b41e` | 已推送 |
| 仓库文档治理扫描修复 | `5200392` | 已推送 |
| 真实 Podman 沙箱集成测试 | `cae41b6` | 已推送 |

远端固定为 `git@github.com:xingyuan-168/AI_Engineering_OS.git`。真实 OCI 验收基线时本地 HEAD 与远端 M6 分支均为 `cae41b67351fafbc5c086ba7fe01a7ba50b8841c`，工作区干净。

## 3. 自动化验收结果

| 门禁 | 结果 | 证据 |
| --- | --- | --- |
| 冻结依赖 | 通过 | `uv sync --frozen --all-groups`，73 个包 |
| 构建 | 通过 | wheel 与 sdist 均成功；wheel SHA256：`E71DE6F47BBC95E8CC1087D3AFDFED75F1BD7947AF06C9CBDF2E498DA2222793` |
| Lint | 通过 | Ruff 无错误 |
| 类型 | 通过 | Pyright 0 errors、0 warnings |
| 测试 | 通过 | 115 passed（含显式启用的真实 Podman 测试），分支覆盖率 83.73%，门槛 80% |
| 文档治理 | 通过 | 44 个 Markdown 文件；必需文档、标题、本地链接和复制目录检查无错误 |
| 依赖安全 | 通过 | `pip-audit` 无已知漏洞；本地未发布包按预期跳过 PyPI 查询 |
| Secret | 通过 | `detect-secrets` 扫描全部 Git 跟踪文件，结果为空 |
| Plugin | 通过 | 官方 Plugin validator 通过，19 个 Skill 全部通过 `quick_validate.py` |
| MCP | 通过 | 真实 stdio 子进程列出并调用 11 个工具，Host 握手走集成测试 |
| Wheel 安装 | 通过 | 全新 Python 3.12 虚拟环境安装 wheel，`init/status/check-docs` 均成功，Schema 为 0003 |
| ERP E2E | 通过 | PA-001～PA-010 全部满足；PA-007 已用生产 `PodmanSandbox`、真实 Git Worktree 和锁定镜像复验 |

构建使用 Python 3.12.13、uv 0.12.3、SQLite 3.53.1。`uv.lock` SHA256 为 `F2809647B9C2425E5427AC9DF18359CEA55D6AC39DAFACE703174F9FAAB14D66`。

## 4. 环境门禁与剩余阻塞项

### ENV-SANDBOX-001：已解除

当前主机已安装 WSL 2.7.12（内核 6.18.33.2）和 Podman 5.8.3，`podman-machine-default` 以 rootless WSL machine 运行，资源为 2 CPU、4 GiB 内存和 20 GiB 磁盘。Podman 服务端版本为 5.8.6，`codex-os doctor --json` 返回 exit 0、`ok=true`。

锁定镜像 `python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2` 已预拉取并通过 RepoDigest 核对。`EXEC-REAL-OCI-001` 使用生产 `PodmanSandbox` 在真实受管 Worktree 中执行，command hash 为 `36be837556551c0c04e6826524d943027624b02c5e59aecee27a3a6470db2927`，验证 UID/GID `65532:65532`、`--network none`、只读根文件系统、capabilities 全部移除、`no-new-privileges`、2 CPU、4 GiB、PID 256、禁止运行时拉取且 Worktree 干净。证据由提交 `cae41b6` 中的 opt-in 集成测试固化。

### ENV-CODEX-001：Codex Plugin 自动重装未复验

Plugin cachebuster 已更新为 `0.1.0+codex.20260821051926`。当前 Codex Host 已发现该 Plugin 的 19 个 Skill，并连接全部 11 个 `ai_engineering_os` MCP 工具；但 WindowsApps 中的 `codex.exe` 仍返回 WinError 5/拒绝访问，且本次环境复验未取得 `/hooks` 信任页面证据。Plugin manifest、Skills、Hooks、启动器和 MCP 子进程均已通过仓库级验证。

解除条件：在 Codex App 的 `/hooks` 页面复核当前 Plugin Hook hash 信任状态；CLI 自动重装行为在修复 WindowsApps 执行 ACL 后单独复验。

### GOV-G4-001：人工发布批准和主分支合并待完成

当前仅创建并推送里程碑分支，没有创建 tag、生产发布或改写 `main`。Codex Hook 信任门禁解除后，由用户完成 G4；随后按仓库保护规则通过 PR 合并，或在确认未启用保护时使用保留历史的 `--no-ff` 合并并立即推送。

## 5. 结论

V1 功能实现、自动化验收和真实 Podman OCI 复验已完成，未发现审批绕过、越权写入、重复任务、缺失 Git 证据或工作区遗留改动。由于 Codex Hook 信任复核和 G4 人工批准两个阻塞项仍存在，本报告不授权发布、打 tag 或合并 `main`。
