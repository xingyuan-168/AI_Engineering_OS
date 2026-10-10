---
name: frontend-design-review
description: Gate substantive frontend implementation behind explicit user approval of the prototype and UI spec, and record that approval. Use before starting frontend implementation in a governed project; not needed for copy changes, CSS fixes, or component bug fixes.
---

# Frontend design review

## Before implementation

1. Verify `docs/design/PROTOTYPE.html` and `docs/design/UI_SPEC.md` exist and match the requirement.
2. Ask the user to review the prototype in a browser and approve or reject it.
3. Record the actual user outcome with the same scope as the gate (use `decision="rejected"` for a rejection):

```python
approval_record(project_root="<project-root>", gate="frontend", subject="dashboard", scope="dashboard", decision="approved", decided_by="user")
governance_check(project_root="<project-root>", stage="frontend", frontend_impact="new_page", frontend_scope="dashboard")
```

4. Implement only after the gate allows. Both outcomes update the Git-tracked approval block; rejection revokes prior approval. Scope, prototype and UI spec digests must match. Changed designs or legacy approval blocks without digests require another explicit user review.

## Rules

- Never infer or self-record approval; the decision comes from the user.
- A rejection sends you back to the prototype, not to implementation.
- After UI changes land, keep the prototype and UI spec in sync with reality so the next review starts from truth.
