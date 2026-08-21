---
name: execution-manager
description: Execute an approved task in the policy-selected Docker or Podman sandbox and preserve auditable results. Do not use for arbitrary host commands.
---

# Execution manager

Require an active task Worktree and use the configured OCI backend without changing it through a lower-priority override. Accept only digest-pinned images, allowlisted argv commands, explicit in-project mounts, disabled networking by default, non-root users, read-only roots, minimal capabilities, and configured CPU, memory, process, temporary-storage, and time limits.

Record the command hash, image digest, mount metadata, network mode, timestamps, container result, redacted logs, and Worktree state. Stop and report `SANDBOX_UNAVAILABLE`, boundary violations, timeouts, dirty state, or non-zero checks; never fall back to an ungoverned host write. Cancellation preserves logs and marks the Worktree for review.
