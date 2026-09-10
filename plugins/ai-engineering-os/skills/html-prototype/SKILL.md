---
name: html-prototype
description: Produce a reviewable interactive HTML prototype at docs/design/PROTOTYPE.html plus a UI spec before any substantive frontend implementation. Use for new pages, new interaction flows, or major UI refactors in governed projects. Skip for copy changes, CSS fixes, and component bug fixes.
---

# HTML prototype

The goal is to see and confirm the interface before implementation, avoiding rework — not to produce UX documents.

## Flow

Requirement → user flow (may live in REQUIREMENTS/UI_SPEC) → interactive HTML prototype → UI spec → user approval → implementation.

## Artifacts

- `docs/design/PROTOTYPE.html` — one self-contained file, no build step, viewable directly in a browser. Cover every screen and interaction state the requirement names; keep styling minimal but realistic enough to judge layout and flow.
- `docs/design/UI_SPEC.md` — layout, components, and state/feedback rules the prototype demonstrates.

## Rules

- The prototype is disposable scaffolding for review, not production code; the real implementation follows after approval.
- Present the prototype to the user and record the explicit approval through `approval_record(gate="frontend")` before implementation starts (see frontend-design-review).
- Exempt changes do not need a prototype; do not create ceremony for copy edits or CSS fixes.
