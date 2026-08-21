---
name: agent-manager
description: Map persisted workflow tasks to the smallest suitable specialist Agent and verify structured handoffs. Use only for AI Engineering OS task orchestration.
---

# Agent manager

Assign an Agent only from the task's declared capability, artifacts, risk, paths, and review needs. Preserve the runtime task ID, Branch, Worktree, deadlines, and permissions. Concurrency is allowed only for tasks with independent writable paths and explicit dependency boundaries.

Before a consumer starts, verify the producer's commit, artifact hashes, tests, open risks, and allowed paths. Reject incomplete or conflicting handoffs as `blocked`; do not reconstruct missing facts from conversation. Reassignment keeps the task identity and records the reason, while failed or cancelled Agent worktrees remain available for review until approved cleanup.
