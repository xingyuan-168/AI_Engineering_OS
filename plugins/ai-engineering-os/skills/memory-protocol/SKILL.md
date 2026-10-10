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

- Subagents never write `docs/memory/`; registered worktrees submit candidates through the shared coordinator store:

```python
memory_record(project_root="<registered-worktree>", record_type="lesson", title="Concise lesson", summary="Reusable finding and its reason", source="src/example.py", candidate=True)
```
- The main session accepts/rejects candidates at Finish. Accept errors preserve candidates unless the complete record is already present; retry identical records safely. Short write locks prevent lost records. Searches refresh the index from validated JSONL; invalid facts block instead of returning stale results.

## Format and timing

One JSON object per line: id, type, title, summary, source, source_commit, tags, status, superseded_by. Write at decision/fix/lesson time — never per command, per task status, or for chat and reasoning traces. Secrets are rejected automatically.
