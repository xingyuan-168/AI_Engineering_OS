"""Minimal, reviewable documents created by "codex-os init" (ADR-0016).

Every new project gets the same small baseline: README, AGENTS.md, the
read-only input/ area, the deliverable output/ area, five documents under
docs/, and the ADR directory. Everything else (API spec, database, test
plan, security, deployment, frontend design artifacts, Docker environment)
is generated only when the caller explicitly asks for it.
"""

from __future__ import annotations

INCLUDE_CHOICES: tuple[str, ...] = (
    "api",
    "database",
    "test_plan",
    "security",
    "deployment",
    "frontend_design",
    "docker",
)

_GITKEEP = ""

BASE_DOCUMENTS: dict[str, str] = {
    "README.md": """# {{ project_name }}

本项目由 AI Engineering OS 初始化：项目事实保存在 docs/ 与 Git 历史，
运行状态保存在 .codex-os/（Git 忽略）。

- input/：用户原始资料，只读。
- output/：最终交付物，只放成品。
- docs/：需求、范围、架构、开源调研与决策记录。
""",
    "AGENTS.md": """# Project Instructions（宪法）

1. 不改变 Codex 原生工程方式：AIOS 只治理"能否做、何时做、做完留什么"。
2. 正式编码前必须通过 Code Start Gate：GitHub remote 可达、开源调研已记录、仓库无副本式脏乱。
3. input/ 只读：不得修改、重命名或删除其中内容。
4. 禁止复制式版本管理：不创建 src_v2/、backup/、copy/ 等副本；历史由 Git 保存。
5. 受影响的项目文档必须随本次变更同步更新。
6. 前端实现前必须先有 docs/design/PROTOTYPE.html 与 docs/design/UI_SPEC.md，并获得用户批准。
7. 复杂并行任务使用 Codex 原生子 Agent + .worktrees/ 隔离，完成后清理。
8. 一次性文件（临时脚本、缓存、调试产物）任务结束删除，不进入 Git。
9. 有价值的决策、Bug 根因与可复用经验写入 docs/memory/memory.jsonl。
10. 危险 Git/删除操作必须保护用户资产：主工作区禁 force push、禁删远端 ref、禁递归强删。
""",
    "input/.gitkeep": _GITKEEP,
    "output/.gitkeep": _GITKEEP,
    "docs/REQUIREMENTS.md": """# Requirements

## 目标

待补充。

## 需求

待补充。

## 验收标准

待补充。
""",
    "docs/SCOPE.md": """# Scope

## 范围内

待补充。

## 范围外

待补充。
""",
    "docs/ARCHITECTURE.md": """# Architecture

## 定位

待补充。

## 组件

待补充。

## 数据流

待补充。
""",
    "docs/OPEN_SOURCE_RESEARCH.md": """# Open Source Research

## Requirement

requirement_id: REQ-xxx
summary: 本次需求是什么。

## Candidates

### Project A

- URL:
- License:
- 解决什么：
- 可直接复用：
- 可二开：
- 值得学习：
- 风险：

## Decision

decision: build
reason: 为什么这个选择满足本次需求。
""",
    "docs/CHANGELOG.md": """# Changelog

## Unreleased

- 初始化项目文档骨架。
""",
    "docs/ADR/README.md": """# Architecture Decision Records

重大技术决策、被否决方案和演进后果记录在本目录；
文件名格式 ADR-NNNN-短标题.md。
""",
}

CONDITIONAL_DOCUMENTS: dict[str, dict[str, str]] = {
    "api": {
        "docs/API_SPEC.md": """# API Spec

## 接口

待补充。

## 错误

待补充。

## 兼容

待补充。
""",
    },
    "database": {
        "docs/DATABASE.md": """# Database

## Schema

待补充。

## 迁移

待补充。

## 恢复

待补充。
""",
    },
    "test_plan": {
        "docs/TEST_PLAN.md": """# Test Plan

## 测试范围

待补充。

## 验收矩阵

待补充。
""",
    },
    "security": {
        "docs/SECURITY.md": """# Security

## 信任边界

待补充。

## 威胁与对策

待补充。
""",
    },
    "deployment": {
        "docs/DEPLOYMENT.md": """# Deployment

## 环境

待补充。

## 发布步骤

待补充。

## 回滚

待补充。
""",
    },
    "frontend_design": {
        "docs/design/PROTOTYPE.html": """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{{ project_name }} Prototype</title>
    <style>
      body { font-family: system-ui, sans-serif; margin: 2rem; }
      .screen { border: 1px dashed #888; padding: 1rem; margin-bottom: 1rem; }
    </style>
  </head>
  <body>
    <h1>{{ project_name }} 交互原型</h1>
    <p>在此补充各界面与交互流程；用户批准后才能开始正式前端实现。</p>
    <div class="screen">Screen 1</div>
  </body>
</html>
""",
        "docs/design/UI_SPEC.md": """# UI Spec

## 布局

待补充。

## 组件

待补充。

## 状态与反馈

待补充。
""",
    },
    "docker": {
        "docker/Dockerfile": """FROM python:3.12-slim

WORKDIR /app
COPY . .
CMD ["python", "main.py"]
""",
        "compose.yaml": """services:
  app:
    build: ./docker
    volumes:
      - .:/app
""",
    },
}


def documents_for(
    project_type: str,
    *,
    include: frozenset[str] | set[str] = frozenset(),
) -> dict[str, str]:
    """Return the documents to create for one project initialization."""

    del project_type  # The baseline is identical for every project type.
    documents = dict(BASE_DOCUMENTS)
    for choice in include:
        documents.update(CONDITIONAL_DOCUMENTS.get(choice, {}))
    return documents


__all__ = ["BASE_DOCUMENTS", "CONDITIONAL_DOCUMENTS", "INCLUDE_CHOICES", "documents_for"]
