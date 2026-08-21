---
name: bug-fix-orchestrator
description: Run a governed bug-fix workflow for a reproducible defect in an existing project. Do not use for feature requests or incidents that still need scope triage.
---

# Bug fix orchestrator

Start `bug-fix` through the `ai-engineering-os` MCP server only after the observed behavior, expected behavior, affected version, and reproduction evidence are available. If impact suggests an architectural, data, or security change, stop for scope approval instead of treating it as a small repair.

Preserve a failing regression test or equivalent deterministic reproduction before changing implementation. Work only in the returned task Worktree and allowed paths. Confirm the smallest causal fix, run focused and relevant regression checks, obtain review, then provide one pushed logical commit and artifact hashes through `task_complete`.

Do not close a non-reproduced defect, weaken an assertion to make it pass, or suppress an unresolved failure. Record the root cause, affected range, verification, rollback note, and reusable failure lesson in the governed artifacts.
