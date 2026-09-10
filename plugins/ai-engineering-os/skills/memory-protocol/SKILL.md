---
name: memory-protocol
description: Record durable engineering memory (decisions, bug root causes, failed approaches, reusable patterns) at the right moments, following the single-writer rule. Use when a major decision is finalized, a bug root cause is fixed, or a reusable lesson emerges. Never for chat logs, command output, or routine successes.
---

# Memory protocol

Storage layers: the Git-tracked `docs/memory/memory.jsonl` is the single source of truth; the SQLite `memory_index` table is a rebuildable search index.

## Record only

- `decision` — a major technical choice with its reason.
- `bug` — root cause and fix once a bug is understood.
- `lesson` — a failed approach worth avoiding later.
- `pattern` — a reusable engineering pattern proven in this project.
- `project-summary` — optional milestone summary.

Statuses: `active` / `superseded` / `invalid`. A superseded entry must name its successor.

## Single-writer rule

- Subagents never write `docs/memory/` (the hook enforces this in disposable worktrees). They submit candidates: `memory_record(..., candidate=true)`.
- The main session merges candidates into the JSONL at task finish, then runs `codex-os memory reindex`.

## Format and timing

One JSON object per line: id, type, title, summary, source, source_commit, tags, status, superseded_by. Write at decision/fix/lesson time — never per command, per task status, or for chat and reasoning traces. Secrets are rejected automatically.
