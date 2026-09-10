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
What this requirement is.

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
- use / fork / extract / build
- reason:
```

## Rules

- One Decision section per requirement; the Code Start gate checks for it.
- No supply-chain audits, SBOMs, or fixed field matrices for candidates you rejected.
- Never introduce a new agent runtime (LangGraph, CrewAI, MetaGPT, OpenHands) as a dependency; Codex already provides agent capability.
- Cite URLs so the decision stays auditable.
