---
name: testing
description: Plan and execute evidence-based verification for an AI Engineering OS task or release gate. Use for test design and execution; do not mark checks passed when they were skipped or unavailable.
---

# Testing

Map tests to acceptance criteria, contracts, risks, and regression boundaries. Include success, validation, authorization, concurrency, idempotency, migration, recovery, and failure paths in proportion to risk.

Run the narrowest useful checks during iteration and the complete required gate before handoff. Record the exact command, environment, result, duration when useful, and any skipped or blocked case in `docs/TEST_PLAN.md` or `reports/`.

Treat flaky, skipped, unavailable-sandbox, and unexecuted tests as distinct outcomes. Do not edit production behavior merely to make a test pass unless the assigned task authorizes the fix and the contract supports it.
