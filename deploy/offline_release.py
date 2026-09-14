#!/usr/bin/env python3
"""Build and verify the file-level contract for the Harbor Transfer Portal offline kit.

Docker image construction/export is intentionally handled by deploy/build-offline-kit.sh.
This module stays stdlib-only so CI can test release layout, metadata and checksum behavior
without a Docker daemon or network access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

_PRODUCT = "harbor-transfer-portal"
_SCHEMA_VERSION = 1
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_ARCHITECTURES = {"amd64", "arm64"}

_STATIC_FILES = {
    ".env.example": ".env.example",
    "deploy/compose-offline.yml": "compose.yaml",
    "deploy/install.sh": "install.sh",
    "deploy/OFFLINE-README.md": "README.md",
}
_IMAGE_FILES = {
    "backend": "images/backend.tar",
    "frontend": "images/frontend.tar",
}
_GENERATED_FILES = {"VERSION", "ARCHITECTURE", "release.json", "SHA256SUMS"}


class ReleaseError(ValueError):
    """Release contract violation."""


def validate_version(value: str) -> str:
    value = value.strip()
    if not _VERSION_RE.fullmatch(value):
        raise ReleaseError(
            "release version must be X.Y.Z or X.Y.Z-prerelease and safe as a Docker tag"
        )
    return value


def validate_architecture(value: str) -> str:
    value = value.strip()
    if value not in _ARCHITECTURES:
        raise ReleaseError(f"unsupported release architecture: {value!r}")
    return value


def validate_source_commit(value: str) -> str:
    value = value.strip().lower()
    if not _COMMIT_RE.fullmatch(value):
        raise ReleaseError("source commit must be a full 40-character lowercase Git SHA")
    return value


def _regular_file(path: Path, *, label: str, require_nonempty: bool = False) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ReleaseError(f"{label} must be a regular file: {path}")
    if require_nonempty and path.stat().st_size <= 0:
        raise ReleaseError(f"{label} must not be empty: {path}")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseError(f"unsafe relative path in release metadata: {value!r}")
    return path


def _copy_regular(src: Path, dst: Path, *, mode: int = 0o644) -> None:
    _regular_file(src, label="release source file")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    os.chmod(dst, mode)


def _write_text(path: Path, text: str, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    os.chmod(path, mode)


def expected_payload_files() -> set[str]:
    return {
        *_STATIC_FILES.values(),
        *_IMAGE_FILES.values(),
        *_GENERATED_FILES,
    }


def _payload_files(root: Path, *, include_checksums: bool) -> list[Path]:
    result: list[Path] = []
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if path.is_symlink():
            raise ReleaseError(f"release payload must not contain symlinks: {path}")
        relative = path.relative_to(root).as_posix()
        if relative == "SHA256SUMS" and not include_checksums:
            continue
        result.append(path)
    return sorted(result, key=lambda item: item.relative_to(root).as_posix())


def _write_checksums(root: Path) -> None:
    lines = []
    for path in _payload_files(root, include_checksums=False):
        relative = path.relative_to(root).as_posix()
        lines.append(f"{sha256_file(path)}  {relative}")
    _write_text(root / "SHA256SUMS", "\n".join(lines) + "\n")


def verify_release_tree(root: Path) -> dict[str, object]:
    if root.is_symlink() or not root.is_dir():
        raise ReleaseError(f"release root is not a directory: {root}")

    actual = {
        path.relative_to(root).as_posix()
        for path in _payload_files(root, include_checksums=True)
    }
    expected = expected_payload_files()
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ReleaseError(f"release layout mismatch; missing={missing}, unexpected={unexpected}")

    version = validate_version((root / "VERSION").read_text(encoding="utf-8").strip())
    architecture = validate_architecture(
        (root / "ARCHITECTURE").read_text(encoding="utf-8").strip()
    )

    try:
        metadata = json.loads((root / "release.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ReleaseError(f"invalid release.json: {exc}") from exc

    if metadata.get("schema_version") != _SCHEMA_VERSION:
        raise ReleaseError("unsupported release metadata schema")
    if metadata.get("product") != _PRODUCT:
        raise ReleaseError("release metadata product mismatch")
    if metadata.get("version") != version:
        raise ReleaseError("release metadata version mismatch")
    if metadata.get("architecture") != architecture:
        raise ReleaseError("release metadata architecture mismatch")
    validate_source_commit(str(metadata.get("source_commit", "")))

    images = metadata.get("images")
    if not isinstance(images, list) or len(images) != 2:
        raise ReleaseError("release metadata must describe exactly backend and frontend images")

    seen_components: set[str] = set()
    for item in images:
        if not isinstance(item, dict):
            raise ReleaseError("invalid image entry in release metadata")
        component = item.get("component")
        if component not in _IMAGE_FILES or component in seen_components:
            raise ReleaseError("invalid or duplicate release image component")
        seen_components.add(component)
        archive_text = item.get("archive")
        if archive_text != _IMAGE_FILES[component]:
            raise ReleaseError(f"archive path mismatch for {component}")
        archive = root / _safe_relative_path(str(archive_text))
        expected_sha = item.get("sha256")
        if expected_sha != sha256_file(archive):
            raise ReleaseError(f"image archive checksum mismatch for {component}")
        if item.get("reference") != f"{_PRODUCT}-{component}:{version}":
            raise ReleaseError(f"image reference mismatch for {component}")

    checksum_lines = (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    expected_paths = expected - {"SHA256SUMS"}
    seen_paths: set[str] = set()
    for line in checksum_lines:
        if "  " not in line:
            raise ReleaseError("invalid SHA256SUMS line")
        digest, relative = line.split("  ", 1)
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ReleaseError(f"invalid checksum digest for {relative!r}")
        safe = _safe_relative_path(relative).as_posix()
        if safe in seen_paths:
            raise ReleaseError(f"duplicate checksum entry: {safe}")
        seen_paths.add(safe)
        if safe not in expected_paths:
            raise ReleaseError(f"unexpected checksum entry: {safe}")
        if sha256_file(root / safe) != digest:
            raise ReleaseError(f"checksum mismatch: {safe}")

    if seen_paths != expected_paths:
        raise ReleaseError(
            f"SHA256SUMS coverage mismatch; missing={sorted(expected_paths - seen_paths)}"
        )

    return metadata


def package_release(
    *,
    repo_root: Path,
    output_dir: Path,
    version: str,
    architecture: str,
    source_commit: str,
    backend_image_tar: Path,
    frontend_image_tar: Path,
    skopeo_version: str,
    helm_version: str,
    generated_at: str | None = None,
) -> Path:
    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    version = validate_version(version)
    architecture = validate_architecture(architecture)
    source_commit = validate_source_commit(source_commit)
    backend_image_tar = _regular_file(
        backend_image_tar.absolute(), label="backend image archive", require_nonempty=True
    )
    frontend_image_tar = _regular_file(
        frontend_image_tar.absolute(), label="frontend image archive", require_nonempty=True
    )

    if not skopeo_version.strip():
        raise ReleaseError("Skopeo version must not be empty")
    if not helm_version.strip():
        raise ReleaseError("Helm version must not be empty")

    for source in _STATIC_FILES:
        _regular_file(repo_root / source, label=source)

    output_dir.mkdir(parents=True, exist_ok=True)
    kit_name = f"{_PRODUCT}-v{version}-offline-install"
    archive_path = output_dir / f"{kit_name}.tar.gz"
    sidecar_path = output_dir / f"{kit_name}.tar.gz.sha256"
    if archive_path.exists() or sidecar_path.exists():
        raise ReleaseError(
            f"refusing to overwrite existing release artifact: {archive_path} / {sidecar_path}"
        )

    with tempfile.TemporaryDirectory(prefix=f"{kit_name}-", dir=output_dir) as temporary:
        stage_root = Path(temporary) / kit_name
        stage_root.mkdir()

        for source, destination in _STATIC_FILES.items():
            mode = 0o755 if destination == "install.sh" else 0o644
            _copy_regular(repo_root / source, stage_root / destination, mode=mode)

        _copy_regular(backend_image_tar, stage_root / _IMAGE_FILES["backend"])
        _copy_regular(frontend_image_tar, stage_root / _IMAGE_FILES["frontend"])

        _write_text(stage_root / "VERSION", f"{version}\n")
        _write_text(stage_root / "ARCHITECTURE", f"{architecture}\n")

        generated = generated_at or datetime.now(UTC).replace(microsecond=0).isoformat()
        metadata = {
            "schema_version": _SCHEMA_VERSION,
            "product": _PRODUCT,
            "version": version,
            "architecture": architecture,
            "source_commit": source_commit,
            "generated_at": generated,
            "runtime": {
                "skopeo": skopeo_version.strip(),
                "helm": helm_version.strip(),
            },
            "images": [
                {
                    "component": "backend",
                    "reference": f"{_PRODUCT}-backend:{version}",
                    "archive": _IMAGE_FILES["backend"],
                    "sha256": sha256_file(stage_root / _IMAGE_FILES["backend"]),
                },
                {
                    "component": "frontend",
                    "reference": f"{_PRODUCT}-frontend:{version}",
                    "archive": _IMAGE_FILES["frontend"],
                    "sha256": sha256_file(stage_root / _IMAGE_FILES["frontend"]),
                },
            ],
        }
        _write_text(
            stage_root / "release.json",
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        )
        _write_checksums(stage_root)
        verify_release_tree(stage_root)

        with tarfile.open(archive_path, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            archive.add(stage_root, arcname=kit_name, recursive=True)

    _write_text(sidecar_path, f"{sha256_file(archive_path)}  {archive_path.name}\n")
    return archive_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    package = subparsers.add_parser("package", help="assemble an offline install archive")
    package.add_argument("--repo-root", type=Path, required=True)
    package.add_argument("--output-dir", type=Path, required=True)
    package.add_argument("--version", required=True)
    package.add_argument("--architecture", required=True)
    package.add_argument("--source-commit", required=True)
    package.add_argument("--backend-image-tar", type=Path, required=True)
    package.add_argument("--frontend-image-tar", type=Path, required=True)
    package.add_argument("--skopeo-version", required=True)
    package.add_argument("--helm-version", required=True)

    verify = subparsers.add_parser("verify-tree", help="verify an extracted kit directory")
    verify.add_argument("root", type=Path)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "package":
            archive = package_release(
                repo_root=args.repo_root,
                output_dir=args.output_dir,
                version=args.version,
                architecture=args.architecture,
                source_commit=args.source_commit,
                backend_image_tar=args.backend_image_tar,
                frontend_image_tar=args.frontend_image_tar,
                skopeo_version=args.skopeo_version,
                helm_version=args.helm_version,
            )
            print(archive)
            return 0
        verify_release_tree(args.root)
        print(f"verified: {args.root}")
        return 0
    except ReleaseError as exc:
        print(f"offline release error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
