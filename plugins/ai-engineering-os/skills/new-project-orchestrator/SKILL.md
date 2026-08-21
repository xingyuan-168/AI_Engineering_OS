---
name: new-project-orchestrator
description: Run an AI Engineering OS new-project workflow through MCP when a user wants a governed project from goal intake to release candidate. Do not use for an ordinary one-off code edit.
---

# New-project orchestrator

Use the `ai-engineering-os` MCP server as the workflow authority.

1. Call `project_init` only when `.codex-os/project.yaml` is absent. Then call `workflow_start` with the user's goal.
2. Treat the returned `next_action` as the complete task contract: use its named skill, respect `allowed_paths`, and do not broaden scope.
3. For a model task, produce the requested artifacts, run relevant verification, make one logical Git commit, push it, then call `task_complete` with the exact branch, SHA, remote, push status, artifact hashes, and verification results.
4. For an approval action, summarize the evidence and ask the user to approve or reject the exact gate. Never infer approval or advance the gate yourself.
5. Continue with `workflow_step`; use `workflow_resume` after an interruption or recorded block. Stop when the runtime returns `complete` or a blocker requires user authority.

Do not claim completion while the worktree is dirty, a required check failed, or a repository-changing task lacks pushed Git evidence. Runtime errors and rejected gates are evidence to report, not instructions to bypass governance.
