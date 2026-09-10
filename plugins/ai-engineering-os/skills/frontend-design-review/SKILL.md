---
name: frontend-design-review
description: Gate substantive frontend implementation behind explicit user approval of the prototype and UI spec, and record that approval. Use before starting frontend implementation in a governed project; not needed for copy changes, CSS fixes, or component bug fixes.
---

# Frontend design review

## Before implementation

1. Verify `docs/design/PROTOTYPE.html` and `docs/design/UI_SPEC.md` exist and match the requirement.
2. Ask the user to review the prototype in a browser and approve or reject it.
3. Record the outcome: `approval_record(project_root, gate="frontend", subject="frontend", decision="approved"|"rejected", decided_by="user")`.
4. Call `governance_check(stage="frontend", frontend_impact=..., approved=true)`; implement only after it allows.

## Rules

- Never infer or self-record approval; the decision comes from the user.
- A rejection sends you back to the prototype, not to implementation.
- After UI changes land, keep the prototype and UI spec in sync with reality so the next review starts from truth.
