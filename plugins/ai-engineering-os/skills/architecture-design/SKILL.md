---
name: architecture-design
description: Produce implementation-ready architecture and G2 evidence from approved requirements and verified research. Use for system boundaries and contracts, not for speculative redesign outside the accepted scope.
---

# Architecture design

Trace every important design decision back to a requirement or risk. Define component ownership, data flow, failure behavior, trust boundaries, deployment assumptions, observability, migration, rollback, and verification seams.

Keep `docs/ARCHITECTURE.md`, `docs/API_SPEC.md`, `docs/DATABASE.md`, `docs/SECURITY.md`, and relevant ADRs mutually consistent. Use stable identifiers and specify inputs, outputs, error codes, idempotency, concurrency, and compatibility where they change implementation decisions.

Call out rejected alternatives and unresolved decisions. Do not pass G2 with placeholder contracts, contradictory documents, unbounded external access, or a design that cannot be tested under the declared sandbox policy.
