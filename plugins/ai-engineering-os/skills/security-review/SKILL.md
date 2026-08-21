---
name: security-review
description: Review an authorized project change for trust-boundary, secret, dependency, path, command, and sandbox risks. Use for defensive verification, not open-ended exploitation.
---

# Security review

Start from assets, actors, entry points, privileges, data flows, and recovery impact. Check authentication and authorization, input validation, injection, path traversal and links, unsafe subprocess use, secret exposure, dependency provenance, network egress, and sandbox escape assumptions.

Reproduce only as far as needed to establish an authorized finding; avoid destructive payloads and external targets. Record severity, preconditions, affected boundary, evidence, remediation, and residual risk in `docs/SECURITY.md` or `reports/`.

Dependency and secret scanners are supporting evidence, not the whole review. A missing sandbox or unverifiable control blocks high-risk write execution rather than downgrading it silently.
