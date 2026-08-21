---
name: database-design
description: Design or review relational data models, constraints, migrations, and recovery behavior for an approved workflow. Use when persistence semantics materially affect implementation.
---

# Database design

Model business invariants with explicit keys, uniqueness, foreign keys, checks, and transaction boundaries. Specify ownership, lifecycle, retention, indexes, concurrency assumptions, and the queries that justify each index.

Update `docs/DATABASE.md` and related ADR or migration notes. Number migrations monotonically; never edit an applied migration. Define backup, integrity check, failure recovery, and rollback or roll-forward behavior before destructive schema operations.

Test migration from the previous supported schema, clean initialization, repeated application, and failure recovery. Do not rely on application code alone for invariants the database can safely enforce.
