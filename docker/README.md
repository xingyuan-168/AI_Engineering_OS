# AI Engineering OS OCI environment

`docker/ai-os.Dockerfile` is the governed development/runtime image definition for
the repository's 0.2.1 OCI-first adoption. The base image is digest-pinned and
dependencies are resolved from `uv.lock` during the explicitly approved prepare
operation. Compose runs without host dependency mounts, privileges, or external
network access during verification.

Use `codex-os environment prepare` for the approved networked build and
`codex-os environment verify` for the offline recreate and smoke checks. Never
use `compose down -v`, `volume rm`, or a volume prune through an Agent task.
