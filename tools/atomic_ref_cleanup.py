"""Server-enforced compare-and-swap deletion for Git refs.

Use explicit --force-with-lease expected object IDs. Git receive-pack rejects
the update if a ref moved after the audit read; --atomic makes multi-ref
cleanup all-or-nothing.
"""
from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def delete_refs(
    repository_full_name: str,
    refs: Iterable[tuple[str, str]],
    *,
    dry_run: bool = False,
) -> str:
    """Delete only refs still pointing at the exact audited SHAs.

    The remote Git server enforces each explicit lease during receive-pack.
    With --atomic, if any lease is stale or any delete is rejected, none of
    the requested refs are changed.
    """
    if not _REPO_RE.fullmatch(repository_full_name):
        raise ValueError("repository_full_name must be owner/name")

    refs = list(refs)
    if not refs:
        raise ValueError("at least one ref is required")

    names: set[str] = set()
    for name, expected_sha in refs:
        if (
            not name
            or name.startswith("/")
            or name.endswith("/")
            or ".." in name.split("/")
            or any(ch.isspace() for ch in name)
            or ":" in name
        ):
            raise ValueError(f"invalid branch name: {name!r}")
        if name in names:
            raise ValueError(f"duplicate branch name: {name}")
        names.add(name)
        if not _SHA_RE.fullmatch(expected_sha):
            raise ValueError(f"invalid expected SHA for {name}")

    auth = subprocess.run(
        ["gh", "auth", "setup-git"],
        text=True,
        capture_output=True,
        check=False,
    )
    if auth.returncode:
        raise RuntimeError(f"gh auth setup-git failed: {auth.stderr.strip()}")

    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        text=True,
        capture_output=True,
        check=False,
    )
    remote_url = remote.stdout.strip().replace(":", "/")
    if remote.returncode or f"github.com/{repository_full_name}" not in remote_url:
        raise RuntimeError("origin is not the expected GitHub repository")

    command = ["git", "push", "--atomic"]
    if dry_run:
        command.append("--dry-run")
    command.extend(
        f"--force-with-lease=refs/heads/{name}:{expected_sha}"
        for name, expected_sha in refs
    )
    command.append("origin")
    command.extend(f":refs/heads/{name}" for name, _ in refs)

    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "atomic compare-and-swap ref deletion failed: "
            f"{result.stderr.strip()}"
        )

    output = result.stdout.strip()
    if dry_run:
        return f"{output}\nCAS dry-run passed for {len(refs)} exact ref(s)".strip()
    return output or f"Deleted {len(refs)} exact ref(s) with atomic leases"
