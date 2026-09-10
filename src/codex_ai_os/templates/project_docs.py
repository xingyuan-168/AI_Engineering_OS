"""Minimal, reviewable documents created by ``codex-os init``."""

from __future__ import annotations

from codex_ai_os.domain.artifacts import DEFAULT_ARTIFACT_CATALOG


def _catalog_path(artifact_id: str) -> str:
    path = DEFAULT_ARTIFACT_CATALOG.path_for(artifact_id)
    if path is None:
        raise RuntimeError(
            f"artifact catalog is missing a document template artifact: {artifact_id}"
        )
    return path


BASE_DOCUMENTS: dict[str, str] = {
    "README.md": """# {{ project_name }}

该项目由 AI Engineering OS 初始化。项目事实位于 `docs/`，运行状态位于 `.codex-os/state/`。

开始实现前必须完成 G0-G2；发布候选需要 G3，最终完成需要 G4。
""",
    "AGENTS.md": """# Project Instructions

- Read `docs/PROJECT_MASTER.md`, `docs/SCOPE.md`, accepted ADRs, and the relevant subsystem documents before editing.
- Treat `.codex-os/context/PROJECT_CONTEXT.md` as generated context, not a source of truth.
- Keep every Agent task in its assigned branch and worktree.
- Do not mark a mutating task complete without tests, a commit SHA, and push evidence when a remote exists.
- Route commands and file writes through AI Engineering OS execution policy.
- Keep project dependencies, builds, tests, and services inside the OCI environment selected by `.codex-os/environment.yaml`.
- Do not run host package managers or delete persistent OCI volumes.
""",
    "docs/README.md": """# 文档索引

- [项目总文档](PROJECT_MASTER.md)
- [范围](SCOPE.md)
- [产品需求](PRODUCT_REQUIREMENTS.md)
- [架构](ARCHITECTURE.md)
- [技术栈](TECH_STACK.md)
- [安全](SECURITY.md)
- [测试](TEST_PLAN.md)
- [变更记录](CHANGELOG.md)
""",
    "docs/PROJECT_MASTER.md": """# {{ project_name }} 项目总文档

状态：草案

## 目标

待 G0 确认。

## 成功标准

待 G0 确认。
""",
    "docs/SCOPE.md": """# 项目范围

状态：草案

## 范围内

待 G0/G1 确认。

## 范围外

待 G0/G1 确认。
""",
    "docs/PRODUCT_REQUIREMENTS.md": """# 产品需求

状态：草案

## 业务目标

待 G1 确认。

## 验收标准

待 G1 确认。
""",
    "docs/USER_STORY.md": """# 用户故事

状态：草案

待 G1 确认。
""",
    "docs/BUSINESS_RULES.md": """# 业务规则

状态：草案

待 G1 确认。
""",
    "docs/ARCHITECTURE.md": """# 系统架构

状态：草案

待 G2 确认。
""",
    "docs/TECH_STACK.md": """# 技术栈

状态：草案

所有依赖必须记录版本、License 和安全核验结果。
""",
    "docs/SECURITY.md": """# 安全设计

状态：草案

默认拒绝网络、越权路径和未审批的高风险操作。
""",
    "docs/TEST_PLAN.md": """# 测试计划

状态：草案

测试必须覆盖正向、失败、恢复和安全边界。
""",
    "docs/CHANGELOG.md": """# Changelog

## Unreleased

- 初始化项目文档骨架。
""",
    "docs/ENVIRONMENT.md": """# 项目环境

状态：草案

在 G2 前补齐 Compose 服务、Dockerfile、依赖锁、镜像 digest、健康检查、持久化、备份恢复和共享只读资产。
""",
    "docs/ADR/README.md": """# Architecture Decision Records

重大技术决策、未采用方案和演进后果记录在本目录。
""",
}

BACKEND_DOCUMENTS: dict[str, str] = {
    "docs/API_SPEC.md": """# API 规范

状态：草案

待 G2 确认接口、错误、幂等和权限。
""",
    "docs/DATABASE.md": """# 数据库设计

状态：草案

待 G2 确认 Schema、约束、迁移和回滚。
""",
}

FRONTEND_DOCUMENTS: dict[str, str] = {
    _catalog_path("product-design"): """# Product Design

状态：草案

## 设计范围

待 G2 确认产品目标、用户价值与范围边界。

## 信息架构

待 G2 确认页面结构与导航关系。
""",
    _catalog_path("interaction-design"): """# Interaction Design

状态：草案

## 交互状态

待 G2 确认加载、空态、错误与成功反馈。
""",
    _catalog_path("ui-design"): """# UI Design

状态：草案

## 视觉规范

待 G2 确认布局、色彩、字体与组件规范。
""",
    _catalog_path("ux-research"): "# UX Research\n\n状态：草案\n",
    _catalog_path("user-flow"): "# User Flow\n\n状态：草案\n",
    _catalog_path("wireframe"): "# Wireframe\n\n状态：草案\n",
    _catalog_path("ui-spec"): "# UI Spec\n\n状态：草案\n",
}


def documents_for(project_type: str) -> dict[str, str]:
    documents = dict(BASE_DOCUMENTS)
    if project_type in {"backend", "fullstack"}:
        documents.update(BACKEND_DOCUMENTS)
    if project_type in {"frontend", "fullstack"}:
        documents.update(FRONTEND_DOCUMENTS)
    return documents
