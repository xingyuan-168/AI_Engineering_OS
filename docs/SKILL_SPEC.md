# Skill 实现规格

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"review-ready","owner":"architect","requirement_refs":["REQ-1.6.2","AGENT-001"]} -->

版本：V2.0-derived-skill
状态：可执行实现规格基线

## 1. Skill 定义

Skill 是一个可复用的专业能力单元，负责把明确输入转化为可审计产物。Skill 不拥有全局 Workflow 状态，不得绕过 Agent、审批和执行策略。

## 2. 标准元数据

每个 Skill 必须有目录和 `SKILL.md`，至少包含：

```yaml
schema_version: "1.0"
name: requirement-analysis
version: "1.0.0"
purpose: 分析业务目标并生成可验收需求
owner: product-manager
workflows: [new-project, feature-development]
inputs:
  - name: business_goal
    type: text
    required: true
outputs:
  - path: docs/PRODUCT_REQUIREMENTS.md
    type: document
    required: true
tools: [read_docs, codex_host, write_artifacts]
allowed_paths: [docs/]
approval: none
timeout_seconds: 1800
```

## 3. 输入契约

输入必须声明来源、类型、是否必填、校验规则和敏感级别。Skill 开始前读取 `SCOPE.md`、`PROJECT_MASTER.md` 及其领域文档；缺失或冲突时返回 `blocked`，不得自行猜测事实。

## 4. 输出契约

每个输出必须包含：

- 路径和文档类型。
- 内容 hash 和生成时间。
- 关联 `workflow_id`、`task_id` 和输入产物。
- 来源和假设。
- 验收检查结果。
- 是否需要人工审批。

输出不得直接覆盖用户未授权文件；通过临时文件和 Review 合并。

## 5. 权限模型

- `read_docs`：只读项目文档。
- `write_artifacts`：仅写入声明的允许路径。
- `run_checks`：执行低风险检查。
- `codex_host`：请求 Codex 宿主完成推理或文本产出。
- `execute_code`：默认不授予，需由 Execution Policy 和 Agent 同时允许。
- `network`：默认禁止，外部研究需人工确认或使用已批准代理。

Skill 权限是上限，Agent 和当前任务策略可以进一步收紧，不得放宽。

## 6. 返回状态

```yaml
status: completed | blocked | failed | needs_approval
artifacts: []
assumptions: []
questions: []
errors: []
```

- `completed`：所有必需产物和验收检查完成。
- `blocked`：输入不足、文档冲突或依赖不可用。
- `failed`：执行错误或输出不符合 Schema。
- `needs_approval`：已准备好但必须人工确认。

## 7. 注册与定制

Skill 注册时校验名称、版本、Schema、路径和权限。Plugin 的 `skills/` 提供默认 Skill；仓库新增项目专属 Skill 使用 `.agents/skills/`。同名 Skill 不合并，也不作为覆盖机制。项目对提示词、输入模板和允许路径的定制写入 `.codex-os/` 并由 Runtime 合并；安全权限只能收紧。

## 8. 首批 Skill

0.2.0 随 Plugin 交付以下 21 个 Skill：

`agent-manager`、`api-design`、`architecture-design`、`backend-implementation`、`bug-fix-orchestrator`、`code-review`、`database-design`、`execution-manager`、`feature-development-orchestrator`、`frontend-implementation`、`html-prototype`、`interaction-design`、`memory-manager`、`new-project-orchestrator`、`open-source-research`、`product-design`、`release-manager`、`requirement-analysis`、`security-review`、`testing`、`ui-design`。

该清单必须与 `plugins/ai-engineering-os/skills/` 的目录集合完全一致；新增、删除或改名 Skill 时，同一提交必须更新本节、Plugin 校验和公共契约测试。

## 9. 完成定义

Skill 只有在元数据、输入、输出、权限、失败状态、审批、来源追踪和验收检查均已定义，并有至少一个正向和一个失败测试场景时才算完成。
