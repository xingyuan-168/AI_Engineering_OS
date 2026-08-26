# 发布检查清单

版本：V2.0-derived-release
状态：可执行实现规格基线
适用：Windows 本地 CLI、Codex Plugin、项目配置和沙箱运行时。

当前目标为需求 `REQ-1.6.2`、发行版 `0.2.0`、Plugin API/配置/文档/Profile `1.2`、SQLite `0007`。`ab879c0` 仅是候选基线；本清单和 [发布收口矩阵](RELEASE_CLOSURE_MATRIX.md) 中的未勾选项不得因实现完成而自动视为通过。

## 1. 版本与依赖

- [ ] 版本号、Git tag、构建提交和变更范围已确定。
- [ ] `uv.lock` 或等效锁文件已更新且可复现安装。
- [ ] 直接依赖、传递依赖、License 和安全扫描已归档。
- [ ] SBOM、依赖 hash 和构建环境信息已生成。
- [ ] 重大技术变化有 ADR，用户可见变化已写入 CHANGELOG。
- [ ] `RuntimeVersions` 输出与 pyproject、Plugin manifest、MCP、配置、文档、Profile、数据库及 manifests 完全一致。

## 2. Windows 安装与升级

- [x] Windows 支持版本和 Python 版本已验证。
- [ ] `codex-os init/status/doctor` 在干净环境可运行。
- [ ] 安装、升级、卸载不会删除用户项目文档和审计数据。
- [ ] 配置迁移和 SQLite 迁移在升级前自动备份。
- [ ] 升级失败可恢复到上一版本和数据库快照。

## 3. Codex 适配

- [ ] 全局 `.codex-plugin/` 清单可安装并发现全部首批 Skill。
- [ ] 项目 `.codex/` 可覆盖提示词、输入模板和允许路径。
- [ ] 安全字段不可被项目覆盖放宽。
- [ ] Plugin 更新、卸载和版本冲突行为已验证。
- [ ] Plugin Hook 成功、超时、重试和失败隔离行为已验证，核心 Workflow 状态未被回滚。
- [ ] Codex 宿主不可用时，CLI 能明确报告并保留 Workflow 状态。

## 4. 沙箱与安全

- [ ] `doctor` 已确认正式阶段的 Podman machine、锁定镜像、Trivy snapshot 和 `gh` 均可用；当前主机 Podman machine 已停止且未安装 `gh`。
- [x] 默认网络关闭、挂载路径最小化、Docker socket 未挂载。
- [ ] L0-L4 命令风险策略已测试。
- [ ] 容器不可用时高风险任务进入 `blocked`，没有宿主机静默降级。
- [ ] Secret、路径、依赖和镜像安全扫描通过。
- [ ] 取消、超时、资源超限和强制终止均保留审计证据。
- [ ] Worktree 路径、Branch、清理和失败现场保留规则已验证。
- [ ] 官方 Python 3.12.14 Bookworm 的 registry index/platform digest、SBOM 与 Trivy 报告已归档，未批准 high/critical finding 为零。

## 5. 质量与验收

- [ ] 单元、集成、端到端、恢复、迁移和安全测试通过。
- [ ] G0-G3 证据齐全，G4 已获得人工批准。
- [ ] Routing Decision、Agent Handoff 的评分/理由、hash、Commit、测试和阻塞证据齐全。
- [ ] Memory 记录已完成脱敏、来源 hash、项目隔离和失效状态检查。
- [ ] ERP 采购模块试点通过 [PILOT_ACCEPTANCE.md](PILOT_ACCEPTANCE.md)。
- [ ] 发布候选物包含构建日志、测试报告、Review、安全报告、SBOM 和回滚包。
- [ ] 发布后健康检查、故障联系人和回滚窗口已确认。
- [ ] 所有 `integration_prepare`、`integration_merge`、`release_prepare`、`release_publish` Host Operation 均为 succeeded，或已完成结果未知对账且无残留 lease。
- [ ] candidate/final manifest 区分 source/candidate/PR merge Commit；tag、draft/published Release 和全部资产 hash 已对账。
- [ ] 真实 Podman 测试实际运行且通过，测试汇总中没有必需检查 skip。

## 6. 发布后

- [ ] 记录版本、环境、时间、执行人和健康检查结果。
- [ ] 导出发布审计包并保存到指定归档位置。
- [ ] 任何异常建立问题记录；重大异常建立 ADR。
- [ ] 评估指标、失败率、恢复率和审批等待时间。

## 7. 通过标准

任一必选项未完成，发布状态为 `blocked`。只有所有必选项完成、证据可从绑定 Commit/审计区复算、回滚已验证、PR 已合并并获得独立 G4 人工批准，持久化 publish operation 才能创建/对账 `v0.2.0` 和 GitHub Release。本实施请求本身不等同于 G4 发布授权。
