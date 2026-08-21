# 运行与发布

## 本地运行

1. 安装 Python、uv 和 Git。
2. 创建虚拟环境并安装锁定依赖。
3. 执行 `codex-os init` 初始化项目。
4. 使用 `codex-os status` 查看状态，使用 `resume` 恢复流程。

## Windows 安装与适配

V1 只验收 Windows。安装前必须通过 Python 3.12、uv、Git、Docker Desktop/Podman 和磁盘权限检查。Codex Plugin 安装到全局 Plugin 目录，项目 `.codex/` 只保存项目级覆盖；卸载 CLI 或 Plugin 不得删除项目 `docs/`、SQLite 事件和审计数据。

## 诊断与恢复

执行 `codex-os doctor` 检查运行时、Git、容器、SQLite、配置 Schema、路径权限、磁盘空间和安全工具。升级前备份 SQLite 和配置；升级失败时恢复数据库快照和上一版本运行时，不创建 `backup` 或复制项目目录。

## 发布候选物

发布候选物必须包含：版本标签、构建/测试日志、Review 结果、安全扫描、依赖清单、CHANGELOG、相关 ADR 和回滚说明。

## 发布门禁

生产发布必须通过 G0-G4，并由人工确认。V1 不提供无人工确认的生产部署；部署脚本只在允许环境和明确目标下运行。

## 回滚

回滚使用 Git tag、可复现构建物和既定数据迁移回滚方案，不复制整个项目创建 `backup` 或 `v2` 目录。

详细发布检查见 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)，数据库恢复见 [MIGRATION_SPEC.md](MIGRATION_SPEC.md)。

## 发布检查清单

- [ ] 版本标签和构建物校验和已生成。
- [ ] G0-G4 门禁均有审批或验证证据。
- [ ] 单元、集成、端到端和安全检查通过。
- [ ] 依赖清单、License 和 SBOM 已归档。
- [ ] CHANGELOG、ADR 和回滚说明已更新。
- [ ] 发布目标、执行人和时间窗口已确认。
- [ ] 发布后健康检查和失败处理责任人已明确。

## 发布后记录

发布完成后记录版本、环境、变更摘要、健康检查结果、异常、回滚情况和改进事项；记录写入 `CHANGELOG.md`，重大问题另建 ADR。
