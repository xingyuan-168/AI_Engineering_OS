---
name: release-manager
description: Assemble a verified release candidate, SBOM, checksums, changelog, and rollback evidence after G3 approval. Do not publish or deploy without the required release authority.
---

# Release manager

Confirm the workflow is in the release phase and all G3 verification evidence is present. Create the deterministic candidate manifest through `release_candidate_create`, then build only from the reviewed commit and locked dependencies.

Produce release notes, artifact checksums, dependency or SBOM evidence, migration instructions, and rollback/roll-forward steps under the paths allowed by the task. Verify artifact hashes against the files and associate each deliverable with its source commit.

Creating a candidate is not production publication. Report missing sandbox, signing, credentials, branch protection, or deployment approval as blockers; never fabricate signatures, test results, or push evidence.
