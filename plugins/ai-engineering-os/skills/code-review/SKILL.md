---
name: code-review
description: Review a concrete change for correctness, regressions, maintainability, and missing tests. Use for review evidence; do not modify code unless the user or task explicitly requests fixes.
---

# Code review

Review the diff in the context of its requirements and affected callers. Prioritize findings that can cause incorrect behavior, data loss, security exposure, broken compatibility, nondeterminism, or unrecoverable operations.

For each finding, identify the smallest relevant location, the triggering condition, the concrete impact, and a practical correction. Separate blocking findings from suggestions and state when no actionable finding was found.

Verify claims with tests or code paths when practical. Do not confuse style preference with a defect, and do not approve based only on green tests when the implementation violates an explicit contract.
