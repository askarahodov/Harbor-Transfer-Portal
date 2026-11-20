"""Server-enforced compare-and-swap deletion for Git refs.

Use explicit --force-with-lease expected object IDs. Git receive-pack rejects
the update if a ref moved after the audit read; --atomic makes multi-ref
cleanup all-or-nothing.
"""
from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from urllib.parse import urlsplit

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SCP_REMOTE_RE = re.compile(r"^(?P<user>[^@]+)@(?P<host>[^:]+):(?P<path>.+)$")
_INVALID_REF_CHARS = set(" ~^:?*[\\")


def _valid_branch_name(name: str) -> bool:
    if (
        not name
        or name == "@"
        or name.startswith("/")
        or name.endswith("/")
        or name.endswith(".")
        or "//" in name
        or ".." in name
        or "@{" in name
        or any(ord(ch) < 32 or ord(ch) == 127 or ch in _INVALID_REF_CHARS for ch in name)
    ):
        return False
    components = name.split("/")
    return all(
        component not in {"", ".", ".."}
        and not component.startswith(".")
        and not component.endswith(".")
        and not component.endswith(".lock")
        for component in components
    )


def _is_expected_github_remote(remote_url: str, repository_full_name: str) -> bool:
    """Accept only the exact public GitHub repository over HTTPS or SSH."""
    remote = remote_url.strip()
    if not remote:
        return False

    if "://" in remote:
        parsed = urlsplit(remote)
        if parsed.scheme not in {"https", "ssh"}:
            return False
        if parsed.hostname is None or parsed.hostname.casefold() != "github.com":
            return False
        if parsed.query or parsed.fragment:
            return False
        try:
            port = parsed.port
        except ValueError:
            return False
        allowed_ports = {None, 443} if parsed.scheme == "https" else {None, 22}
        if port not in allowed_ports:
            return False
        if parsed.scheme == "https" and (parsed.username or parsed.password):
            return False
        if parsed.scheme == "ssh" and (
            parsed.username not in {None, "git"} or parsed.password
        ):
            return False
        path = parsed.path.lstrip("/")
    else:
        match = _SCP_REMOTE_RE.fullmatch(remote)
        if (
            match is None
            or match.group("user") != "git"
            or match.group("host").casefold() != "github.com"
        ):
            return False
        path = match.group("path")

    if path.endswith(".git"):
        path = path[:-4]
    return path.casefold() == repository_full_name.casefold()


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
        if not _valid_branch_name(name):
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
    if remote.returncode or not _is_expected_github_remote(
        remote.stdout, repository_full_name
    ):
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
