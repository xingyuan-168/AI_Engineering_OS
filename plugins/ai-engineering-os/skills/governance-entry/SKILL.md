---
name: governance-entry
description: Apply the AI Engineering OS governance gates at task start, before frontend implementation, and at finish. Use whenever a governed project is about to start repository-changing work, or when unsure whether a gate applies. Do not use for trivial read-only questions.
---

# Governance entry

Run the three stateless gates through the `ai-engineering-os` MCP server (or the equivalent `codex-os` CLI commands). The gates answer "allowed / not allowed and why"; they never tell you how to do professional work.

## Task start (Code Start Gate)

1. Capture `git rev-parse HEAD` as the task base ref in native context (use `EMPTY_TREE` only for an unborn repository). Call the start gate with the actual project and change class:

```python
governance_check(project_root="<project-root>", stage="start", change_class="bugfix")
```
Start also reports shared configuration readiness for Finish/Memory. Missing .codex-os configuration blocks without initialization; .aios belongs to a different runtime family. Select `remote` explicitly when appropriate; otherwise Git push configuration chooses it, and ambiguous remotes block.

The MCP default change class is `None`; Start requires an explicit class. CLI `codex-os check` without a class is only a repository preflight, not a passed Code Start. Finish requires the class when formal code changed; document-only work may omit it.

2. Change classes that require recorded research: new_project, new_module, major_feature, new_stack, new_integration, mature_wheel_candidate. Exempt: bugfix, typo, tests_only, small_change.
3. Without a reachable GitHub remote you may still read input/, analyze, research, plan, and write documents — but you must not start formal src/ implementation. Ask the user for the repository instead.
4. The user's own uncommitted work never counts as dirt; only copy-style version directories/files, tracked pollution, and unresolved conflicts block.

## Frontend (Frontend Approval Gate)

Substantive frontend work (new page, new interaction flow, major UI refactor) requires an approved prototype and UI spec first. Copy changes, CSS fixes, and component bug fixes are exempt. See the frontend-design-review skill.

## Finish (Finish Gate)

Run the finish-checklist skill with the captured base ref, a real test command, and an honest Memory decision. Document sync remains native review, not an API boolean. Research-required changes must also supply the current `requirement_id`.

## Rules

- Same input produces the same decision; if a gate blocks, fix the observable fact it names, then re-run.
- Never bypass or fake a gate result; report blocks to the user instead.
- Record user approvals with `approval_record` before treating frontend work as approved.
