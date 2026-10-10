---
name: open-source-research
description: Research existing open-source projects before implementing a new requirement, module, major feature, new stack, or third-party integration, and record the use/fork/extract/build decision in docs/OPEN_SOURCE_RESEARCH.md. Not needed for typo fixes, small bug fixes, tests-only changes, or edits inside an existing approach.
---

# Open source research

Answer four questions before writing code for anything research-required: can an existing project be used directly, forked, mined for a module or design, or must this be built — and why.

## Layered applicability

- Required: new project, new module, major feature, new technology stack, new third-party integration, or a clearly mature wheel exists.
- Exempt: typos, small bug fixes, test-only changes, small modifications inside an established approach.

## Minimum output

Keep the research in `docs/OPEN_SOURCE_RESEARCH.md` using exactly this shape:

```markdown
# Open Source Research

## Requirement
requirement_id: REQ-EXAMPLE
summary: Evaluate an existing approach for this requirement.
scope: example-module
updated_at: 2026-09-24

## Candidates
### Project A
- URL: https://github.com/example/project
- License:
- 解决什么：
- 可直接复用：
- 可二开：
- 值得学习：
- 风险：

## Decision
decision: build
reason: Explain why the inspected candidates do not meet the scoped requirement.
```

## Rules

- Replace the illustrative candidate URL and metadata with verified facts (or an explicit no-suitable-candidate statement). Pass the same requirement id:

```python
governance_check(project_root="<project-root>", stage="start", change_class="major_feature", requirement_id="REQ-EXAMPLE")
```
- No supply-chain audits, SBOMs, or fixed field matrices for candidates you rejected.
- Never introduce a new agent runtime (LangGraph, CrewAI, MetaGPT, OpenHands) as a dependency; Codex already provides agent capability.
- Cite URLs so the decision stays auditable.
