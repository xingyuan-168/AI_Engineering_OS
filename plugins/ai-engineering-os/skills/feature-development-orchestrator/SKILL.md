---
name: feature-development-orchestrator
description: Run an approved feature-development workflow through AI Engineering OS from requirements to release evidence. Do not use for untriaged bugs or a new project.
---

# Feature development orchestrator

Start `feature-development` through the `ai-engineering-os` MCP server with the user's approved goal. Treat every returned `next_action` as the task boundary; keep its Branch, Worktree, Agent, Skill, inputs, outputs, and allowed paths unchanged.

Maintain traceability from the existing requirement or issue through design, implementation, tests, review, release evidence, and Memory. At each gate, show the relevant artifacts and wait for the named approval. Never infer approval from earlier messages.

For a repository-changing task, verify the focused behavior and regression surface, make one coherent commit, push it, and submit the exact SHA, remote, artifact hashes, and checks through `task_complete`. Resume from the persisted checkpoint after interruption. Stop on a dirty tree, scope conflict, missing baseline, failed check, or push failure.
