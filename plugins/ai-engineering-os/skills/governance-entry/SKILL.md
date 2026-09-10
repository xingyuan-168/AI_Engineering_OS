---
name: governance-entry
description: Apply the AI Engineering OS governance gates at task start, before frontend implementation, and at finish. Use whenever a governed project is about to start repository-changing work, or when unsure whether a gate applies. Do not use for trivial read-only questions.
---

# Governance entry

Run the three stateless gates through the `ai-engineering-os` MCP server (or the equivalent `codex-os` CLI commands). The gates answer "allowed / not allowed and why"; they never tell you how to do professional work.

## Task start (Code Start Gate)

1. Call `governance_check(stage="start", change_class=...)`.
2. Change classes that require recorded research: new_project, new_module, major_feature, new_stack, new_integration, mature_wheel_candidate. Exempt: bugfix, typo, tests_only, small_change.
3. Without a reachable GitHub remote you may still read input/, analyze, research, plan, and write documents — but you must not start formal src/ implementation. Ask the user for the repository instead.
4. The user's own uncommitted work never counts as dirt; only copy-style version directories/files, tracked pollution, and unresolved conflicts block.

## Frontend (Frontend Approval Gate)

Substantive frontend work (new page, new interaction flow, major UI refactor) requires an approved prototype and UI spec first. Copy changes, CSS fixes, and component bug fixes are exempt. See the frontend-design-review skill.

## Finish (Finish Gate)

Run the finish-checklist skill; call `governance_check(stage="finish", ...)` with the task facts (tests passed, documents synced, memory written or explicitly not needed).

## Rules

- Same input produces the same decision; if a gate blocks, fix the observable fact it names, then re-run.
- Never bypass or fake a gate result; report blocks to the user instead.
- Record user approvals with `approval_record` before treating frontend work as approved.
