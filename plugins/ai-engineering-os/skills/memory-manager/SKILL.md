---
name: memory-manager
description: Curate durable, source-linked project memory after a governed workflow or release. Use for decisions and reusable lessons, not raw chat transcripts, secrets, or unverifiable claims.
---

# Memory manager

Record only information likely to change future engineering decisions: accepted ADRs, constraints, failure causes, recovery procedures, release evidence, and reusable test knowledge. Keep the canonical fact in Markdown/Git and treat runtime memory as an index.

For every record include scope, type, title, content reference, source references and hashes, confidence, tags, status, and expiry or invalidation condition when relevant. Deduplicate by meaning and source evidence.

Exclude credentials, tokens, private user data, transient logs, and unsupported conclusions. Before G4, verify referenced paths and hashes still match the source commit and mark superseded knowledge instead of overwriting its history.
