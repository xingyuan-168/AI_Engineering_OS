# 范围

## 范围内（治理层七件事 + 记忆）

1. GitHub 前置与仓库卫生检查。
2. 开源调研记录与分层 Gate。
3. 复制式版本管理禁止（副本目录/文件判定）。
4. 受影响文档同步检查。
5. Codex 原生子 Agent + disposable worktree 隔离与清理。
6. 前端原型人工批准 Gate。
7. 一次性文件清理（Finish Gate 仓库侧检查）。
8. 轻量项目记忆（JSONL 事实源 + 可重建索引）。

## 范围外（明确不做，不做占位不做空接口）

strict assurance Profile、SBOM、镜像扫描、dependency audit、Verification Cache、Release 发布器、Host Operation lease/对账、每命令 Evidence、自有 DAG 调度、自有 Agent/Tool Runtime、自研 Secret 引擎、CI/CD、模型推理。AIOS 不创建/调度/管理 Agent——Codex 主会话负责规划执行。

未来确有需求以独立插件讨论，先证明对八条目标有直接价值。

## 版本

版本号在 Phase 6 Dogfood 全部通过、用户确认后一次性定稿（package + CHANGELOG + tag）。
