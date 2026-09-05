# ADR-0007：OCI-First 项目环境治理

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"accepted","owner":"architect","requirement_refs":["REQ-1.6.2","ENV-001","EXEC-001"]} -->

- 状态：Accepted
- 日期：2026-09-02
- 决策版本：0.2.1

## 背景

0.2.0 已能在 Docker 或 Podman 中执行单个任务，但项目依赖、Compose 服务、持久化 Volume、共享模型、重建验证和磁盘占用仍缺少统一契约。Windows 宿主因此可能积累 `.venv`、`node_modules`、构建产物、模型、数据集和日志，使项目不可重建并占用大量磁盘。

## 决策

1. 新项目使用 `environment_mode: oci-first`；旧项目缺失该字段时按 `legacy`，经显式 adoption 才启用硬门禁。
2. V1 同时支持 Docker 与 Podman，默认 Podman。每个项目显式锁定后端，禁止静默回退。
3. 宿主只运行 AI Engineering OS 控制平面、Git 和 OCI 引擎；项目依赖安装、编译、测试及服务运行进入 OCI。
4. `.codex-os/environment.yaml` 是项目环境事实源；Compose、Dockerfile、lockfile、digest、Volume、健康检查和备份恢复必须可审计。
5. 联网准备与断网验证分离，并使用可恢复 Host Operation 持久化 intent、版本、租约、请求 hash 和结果。
6. Runtime 是安全边界；Hook 只提供即时拒绝和防御纵深。
7. Runtime 不自动 prune，也不提供删除真实持久 Volume 的公共 API。

## 后果

- 新项目在 G2 前必须完成实际技术栈的环境设计，初始化产生的空 Compose 不能被误认作证据。
- 存量项目不会被静默重写，首次环境检查会给出 `ENVIRONMENT_ADOPTION_REQUIRED` 和修复动作。
- G3 能证明相同 Commit、lockfile、Compose 与镜像可在断网环境重建并保留声明的数据。
- Docker/Podman 差异由 Adapter 和契约测试吸收；所选后端缺失会阻塞而不是改用另一后端。
