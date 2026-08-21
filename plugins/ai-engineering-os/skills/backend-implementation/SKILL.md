---
name: backend-implementation
description: Implement an approved backend slice with migrations and tests inside an AI Engineering OS task contract. Use only after design approval and only within the returned allowed paths.
---

# Backend implementation

Read the approved requirements, API, database, security, and ADR evidence before editing. Implement the smallest coherent vertical slice that satisfies the assigned acceptance criteria; preserve existing public contracts unless the task explicitly changes them.

Keep validation at trust boundaries, make writes transactional and idempotent where required, avoid embedding secrets, and add migrations that are forward-safe with documented rollback behavior. Add focused unit and integration tests for success and failure paths.

Run the task's required checks, inspect the diff, commit only this logical unit, and push it. Hand off the exact commit SHA, artifact hashes, and test results through `task_complete`; a dirty worktree, failed push, or unverified migration is not completion.
