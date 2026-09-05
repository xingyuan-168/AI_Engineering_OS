---
name: html-prototype
description: Build a reviewable offline HTML interaction prototype for an approved frontend workflow before implementation begins. Use only inside the AI Engineering OS prototype task and its allowed paths.
---

# HTML interaction prototype

Read the approved product, interaction, and UI design documents. Work only in the Branch, Worktree, and allowed paths returned by the active `prototype` action.

Create `docs/prototypes/<prototype-id>/index.html` as a self-contained offline artifact: embed CSS and JavaScript, make no network requests, and do not depend on external fonts, scripts, styles, images, or package installation. Preserve semantic HTML, labelled form controls, native keyboard interaction, visible focus, and deterministic local behavior.

Expose demonstrable states with `data-state` values for `success`, `empty`, `loading`, `validation`, `permission`, `failure`, `retry`, `cancel`, and `resume`. Controls must let the reviewer move through the meaningful flow rather than showing static screenshots. Remove unfinished markers and placeholder copy before submission.

Commit the prototype and the three approved design documents on the same task branch. Submit exact hashes through `task_complete`, then request an independent `prototype_review_submit`. Do not begin frontend implementation until the validator passes, the UX prototype review is accepted, and G2 is approved. If the prototype changes afterward, use `task amend-evidence` and obtain a new review.
