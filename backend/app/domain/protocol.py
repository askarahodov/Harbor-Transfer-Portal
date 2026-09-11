from __future__ import annotations

import json
import tarfile
from pathlib import PurePosixPath
from typing import Iterable

from app.domain.bundle import BundleManifest

SECURITY_CRITICAL_PATHS = {"manifest.json", "manifest.sig", "checksums.sha256"}


def canonical_manifest_bytes(manifest: BundleManifest) -> bytes:
    payload = manifest.model_dump(mode="json", exclude_none=True)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def validate_archive_members(members: Iterable[tarfile.TarInfo]) -> None:
    seen: set[str] = set()
    for member in members:
        name = member.name
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts or "\\" in name or "\x00" in name:
            raise ValueError(f"unsafe archive path: {name!r}")
        normalized = path.as_posix()
        if normalized in seen:
            raise ValueError(f"duplicate archive member: {normalized}")
        seen.add(normalized)
        if member.issym() or member.islnk():
            raise ValueError(f"archive links are not allowed: {normalized}")
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"unsupported archive member type: {normalized}")

    missing = SECURITY_CRITICAL_PATHS - seen
    if missing:
        raise ValueError(f"bundle is missing required members: {sorted(missing)}")
