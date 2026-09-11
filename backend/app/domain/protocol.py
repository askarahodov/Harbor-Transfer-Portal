from __future__ import annotations

import json
import tarfile
from collections.abc import Iterable
from pathlib import PurePosixPath

from app.domain.bundle import BundleManifest

SECURITY_CRITICAL_PATHS = {"manifest.json", "manifest.sig", "checksums.sha256"}
PAYLOAD_TOP_LEVEL_PATHS = {"images", "charts"}


def canonical_manifest_bytes(manifest: BundleManifest) -> bytes:
    payload = manifest.model_dump(mode="json", exclude_none=True)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_member_name(member: tarfile.TarInfo) -> str:
    raw_name = member.name
    name = raw_name.rstrip("/") if member.isdir() else raw_name
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in raw_name
        or "\x00" in raw_name
        or "\n" in raw_name
        or "\r" in raw_name
    ):
        raise ValueError(f"unsafe archive path: {raw_name!r}")
    normalized = path.as_posix()
    if normalized != name:
        raise ValueError(f"archive path is not canonical: {raw_name!r}")
    return normalized


def validate_archive_members(members: Iterable[tarfile.TarInfo]) -> None:
    seen: set[str] = set()
    payload_files: set[str] = set()
    payload_directories: set[str] = set()

    for member in members:
        normalized = _canonical_member_name(member)
        if normalized in seen:
            raise ValueError(f"duplicate archive member: {normalized}")
        seen.add(normalized)

        if member.issym() or member.islnk():
            raise ValueError(f"archive links are not allowed: {normalized}")
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"unsupported archive member type: {normalized}")

        if normalized in SECURITY_CRITICAL_PATHS:
            if not member.isfile():
                raise ValueError(f"security-critical member must be a file: {normalized}")
            continue

        path = PurePosixPath(normalized)
        if not path.parts or path.parts[0] not in PAYLOAD_TOP_LEVEL_PATHS:
            raise ValueError(f"archive member is outside payload layout: {normalized}")
        if member.isfile():
            payload_files.add(normalized)
        else:
            payload_directories.add(normalized)

    missing = SECURITY_CRITICAL_PATHS - seen
    if missing:
        raise ValueError(f"bundle is missing required members: {sorted(missing)}")

    for directory in payload_directories:
        prefix = directory + "/"
        if not any(path.startswith(prefix) for path in payload_files):
            raise ValueError(f"empty or undeclared archive directory: {directory}")
