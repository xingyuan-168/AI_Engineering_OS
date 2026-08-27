---
name: frontend-implementation
description: Implement an approved frontend slice with accessibility checks and tests inside an AI Engineering OS task contract. Use only after design approval and only within the returned allowed paths.
---

# Frontend implementation

Read the approved requirements, product design, interaction design, UI design, API, security, and ADR evidence before editing. Enter only the Branch and Worktree returned by `next_action`; never modify the host project root or another task Worktree. Implement the smallest coherent UI slice that satisfies the assigned acceptance criteria; preserve existing public contracts unless the task explicitly changes them.

Keep rendering deterministic, validate user input and API seams, avoid embedding secrets, preserve accessibility semantics, and keep generated assets within declared paths. Add focused unit, component, or integration tests for success and failure paths that matter to the assigned UI behavior.

Run the task's required checks, inspect the diff, commit only this logical unit, and push it. Hand off the exact commit SHA, artifact hashes, accessibility/test results, and known browser or environment assumptions through `task_complete`; a dirty worktree, failed push, or unverified UI path is not completion.
