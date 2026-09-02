# OCI-First 项目环境治理

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"review-ready","owner":"architect","requirement_refs":["REQ-1.6.2","ENV-001","EXEC-001"]} -->

## 1. 目标与边界

0.2.1 将被管理项目的依赖、编译、测试与服务运行统一收敛到 OCI。宿主机只承载 `codex-os`、Codex Plugin、Git 与所选 OCI 引擎等控制平面。新项目固定使用 `environment_mode: oci-first`；未声明环境模式的存量项目按 `legacy` 读取，并在显式 adoption 前返回 `ENVIRONMENT_ADOPTION_REQUIRED`。

V1 提供 Docker 与 Podman 双适配器。`.codex-os/environment.yaml` 显式选择后端，默认 Podman；Runtime 禁止在所选后端不可用时静默切换到另一后端。

## 2. 仓库环境契约

受管项目必须提交以下事实文件：

- `.codex-os/environment.yaml`：环境模式、后端、Compose/Dockerfile、依赖锁、服务、持久化、共享资产与宿主预算。
- `.dockerignore`：排除 Git、状态库、Worktree、宿主依赖、缓存、制品、日志、模型与数据集。
- `compose.yaml`：可解析的服务拓扑；build context 不得越出项目根。
- `docker/README.md`：构建、启动、健康检查、备份、恢复和故障处理说明。
- `docs/ENVIRONMENT.md`：项目环境设计与数据归属。
- 每个构建服务对应的 Dockerfile；`FROM` 必须绑定 registry digest。

初始化可以生成空的 Compose 草案，但空服务、缺失 Dockerfile、未锁定基础镜像、无依赖 lockfile 或带占位内容的环境设计都不能满足 G2。

## 3. 主机洁净度

项目根禁止出现宿主 `.venv`、`node_modules`、`target`、`.next`、`build`、`dist`、依赖缓存、模型、大型数据集和运行日志。检查器同时报告项目文件、`.git`、OCI image/build cache 与 volume 的分类占用；它只产生 findings 和 repair actions，不自动 prune 或删除数据。

共享模型和数据集必须来自项目外批准根目录，并以只读挂载进入容器。禁止复制到项目、提交 Git 或通过 symlink/junction 绕过根边界。

## 4. 持久化契约

数据库、上传、向量库与其他有状态目录必须使用 named volume、外部存储或批准的持久化挂载，并声明健康检查、备份和恢复命令。G3 在隔离 Compose project name 下验证容器重建、无 `-v` 的 down/up 恢复和 disposable volume round-trip。

`compose down -v`、`volume rm` 与带 volume 的 prune 始终拒绝 Agent 调用。0.2.1 不提供删除真实持久 Volume 的公共 API。

## 5. 两阶段验证

1. `environment_prepare` 在有期 L2 网络批准下，从干净 Commit 构建锁定镜像，绑定依赖锁 hash、镜像 digest、Compose hash、后端和构建日志。
2. `environment_verify` 断网消费 prepare 结果，重新创建容器、执行健康检查和测试，并验证存储持久化与宿主洁净度。

两个阶段均通过持久化 Host Operation 执行，使用 request hash、幂等键、租约、source Commit 与 expected version。后端、Commit、Compose、lockfile 或镜像发生漂移时旧证据立即失效。

## 6. Gate 契约

- G0：保存 `environment_mode` 与 `oci_backend`。
- G2：要求本文件、`.codex-os/environment.yaml`、`.dockerignore`、`compose.yaml`、Dockerfile 清单和通过的 `environment-contract` Check。
- G3：要求 `environment-prepare-evidence`、`host-cleanliness`、`compose-build`、`container-recreate`、`storage-persistence` 与 `environment-smoke`。
- G4：归档 environment manifest、镜像 digest、Compose hash、重建报告与数据恢复说明。

Gate 的 `next_action.gate_requirements` 必须完整暴露这些要求。任何缺失、skip、非零退出或 hash 漂移都 fail closed。
