---
name: open-source-research
description: Research current technical options, versions, licenses, and reuse risks for an approved project requirement. Use during the research phase, not to replace project-specific architecture decisions.
---

# Open-source research

Verify changeable facts against primary sources such as official documentation, release notes, registries, and upstream repositories. Record the version or commit inspected, publication date when relevant, license, maintenance signal, known constraints, and source link.

Update `docs/OPEN_SOURCE_RESEARCH.md`, `docs/TECH_STACK.md`, and any decision ADR. Distinguish adopt, reference, and reject decisions; explain compatibility and license boundaries. Prefer locked versions and immutable image digests for selected dependencies.

Do not copy third-party implementation into the repository without confirming the license and attribution requirements. Mark unresolved claims explicitly instead of presenting search snippets or memory as verified evidence.
