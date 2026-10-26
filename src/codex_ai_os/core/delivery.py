"""Stateless committed-content and remote-ref observations; never fetch or push."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from codex_ai_os.core.gates import GateFinding
from codex_ai_os.core.github_remote import RemoteCheck, display_url
from codex_ai_os.domain.config import GitPushPolicy
from codex_ai_os.infrastructure.errors import DiagnosticError


@dataclass(frozen=True)
class DeliverySnapshot:
    commit: str
    dirty_paths: tuple[str, ...]
    content_digest: str


@dataclass(frozen=True)
class DeliveryCheck:
    commit: str
    remote: str | None
    remote_ref: str | None
    verified: bool
    checked_at: str
    observations: tuple[dict, ...]
    findings: tuple[GateFinding, ...]


def target_findings(git, remote_ref, policy) -> list[GateFinding]:
    findings = []
    if policy is GitPushPolicy.FIXTURE_LOCAL_ONLY:
        findings.append(
            GateFinding(
                "PUSH_FIXTURE_NOT_DELIVERY", "fixture readiness is not formal remote delivery"
            )
        )
    if not remote_ref:
        findings.append(
            GateFinding("PUSH_TARGET_REQUIRED", "supply an explicit refs/heads/... target")
        )
    elif (
        not remote_ref.startswith("refs/heads/")
        or git.run("check-ref-format", remote_ref).returncode
    ):
        findings.append(GateFinding("PUSH_TARGET_INVALID", "remote_ref must be a full branch ref"))
    return findings


def capture_delivery(git, code_paths) -> DeliverySnapshot:
    head = git.run("rev-parse", "--verify", "HEAD^{commit}")
    if head.returncode or not head.stdout.strip():
        raise DiagnosticError("delivery needs a committed HEAD", code="PUSH_COMMIT_REQUIRED")
    changed = git.run("diff", "--name-only", "-z", "--no-renames", "HEAD", "--")
    untracked = git.run("ls-files", "--others", "--exclude-standard", "-z")
    tree = git.run("ls-tree", "-r", "-z", "HEAD")
    if changed.returncode or untracked.returncode or tree.returncode:
        raise DiagnosticError("cannot inspect delivery content", code="PUSH_INSPECTION_FAILED")
    dirty = set(changed.stdout.split("\0")) - {""}
    dirty.update(
        name
        for name in untracked.stdout.split("\0")
        if name
        and any(
            name.casefold() == prefix.casefold()
            or name.casefold().startswith(prefix.casefold() + "/")
            for prefix in code_paths
        )
    )
    expected = {}
    for entry in tree.stdout.split("\0"):
        if not entry:
            continue
        metadata, name = entry.split("\t", 1)
        mode, kind, oid = metadata.split()
        if kind == "blob" and mode in {"100644", "100755"}:
            expected[name] = oid
        elif kind != "commit":
            raise DiagnosticError("unsupported delivery tree entry", code="PUSH_UNVERIFIABLE")
    digest = hashlib.sha256()
    names = []
    for name in sorted(expected):
        path = git.root / name
        if not path.is_file() or path.is_symlink() or path.is_junction():
            dirty.add(name)
            continue
        digest.update(name.encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        names.append(name)
    for offset in range(0, len(names), 64):
        batch = names[offset : offset + 64]
        attrs = git.run("check-attr", "-z", "filter", "--", *batch)
        fields = attrs.stdout.split("\0")
        if attrs.returncode or len(fields) != 3 * len(batch) + 1:
            raise DiagnosticError("cannot read content attributes", code="PUSH_UNVERIFIABLE")
        if any(value not in {"unspecified", "unset"} for value in fields[2::3]):
            raise DiagnosticError("external content filters are not run", code="PUSH_UNVERIFIABLE")
        actual = git.run("hash-object", "--", *batch)
        values = actual.stdout.splitlines()
        if actual.returncode or len(values) != len(batch):
            raise DiagnosticError("cannot hash delivery content", code="PUSH_INSPECTION_FAILED")
        dirty.update(name for name, oid in zip(batch, values, strict=True) if oid != expected[name])
    return DeliverySnapshot(head.stdout.rstrip("\r\n"), tuple(sorted(dirty)), digest.hexdigest())


def check_delivery(git, snapshot, checked: RemoteCheck, *, remote_ref, policy, code_paths):
    findings = target_findings(git, remote_ref, policy)
    findings.extend(
        GateFinding(code, message, details=checked.details) for code, message in checked.errors
    )
    observations = []
    if not checked.errors and not findings:
        by_url = {item.url: item for item in checked.observations}
        if not checked.push_urls:
            findings.append(GateFinding("PUSH_UNVERIFIABLE", "no observed push destinations"))
        for url in checked.push_urls:
            observed = by_url.get(url)
            tip = dict(observed.refs).get(remote_ref) if observed else None
            if observed is None or not observed.valid_refs:
                relation, code = "unverifiable", "PUSH_UNVERIFIABLE"
            elif tip is None:
                relation, code = "missing", "PUSH_REF_MISSING"
            elif tip == snapshot.commit:
                relation, code = "equal", None
            else:
                relation, code = _relation(git, snapshot.commit, tip)
            details = {
                "url": display_url(url),
                "tip": tip,
                "relation": relation,
                "checked_at": observed.checked_at if observed else None,
            }
            observations.append(details)
            if code:
                findings.append(
                    GateFinding(code, "remote delivery is " + relation, details=details)
                )
    current = capture_delivery(git, code_paths)
    if current.commit != snapshot.commit:
        findings.append(GateFinding("PUSH_HEAD_CHANGED", "HEAD changed during Finish"))
    if current.content_digest != snapshot.content_digest:
        findings.append(
            GateFinding("PUSH_CONTENT_CHANGED", "delivery content changed during Finish")
        )
    dirty = sorted(set(snapshot.dirty_paths) | set(current.dirty_paths))
    if dirty:
        findings.append(
            GateFinding(
                "PUSH_UNCOMMITTED_CHANGES",
                "commit task changes in an isolated delivery checkout",
                details={"paths": dirty},
            )
        )
    return DeliveryCheck(
        snapshot.commit,
        checked.selected,
        remote_ref,
        not findings,
        datetime.now(UTC).isoformat(),
        tuple(observations),
        tuple(findings),
    )


def _relation(git, commit: str, tip: str):
    if git.run("cat-file", "-e", tip + "^{commit}").returncode:
        return "unverifiable", "PUSH_UNVERIFIABLE"
    contained = git.run("merge-base", "--is-ancestor", commit, tip)
    if contained.returncode == 0:
        return "contains", None
    shallow = git.run("rev-parse", "--is-shallow-repository")
    if contained.returncode != 1 or shallow.returncode or shallow.stdout.strip() == "true":
        return "unverifiable", "PUSH_UNVERIFIABLE"
    behind = git.run("merge-base", "--is-ancestor", tip, commit)
    if behind.returncode == 0:
        return "behind", "PUSH_REQUIRED"
    return (
        ("diverged", "PUSH_DIVERGED")
        if behind.returncode == 1
        else ("unverifiable", "PUSH_UNVERIFIABLE")
    )
