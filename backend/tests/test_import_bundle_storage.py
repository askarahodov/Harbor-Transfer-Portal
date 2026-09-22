from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.imports import ImportIntakeMode
from app.services.import_bundle_storage import (
    ImportBundleStorageError,
    resolve_persisted_bundle_paths,
)


def _archive(
    tmp_path: Path,
    *,
    key: str = "a" * 48,
    filename: str = "bundle.htp.tar.gz",
) -> tuple[Path, str, str]:
    root = tmp_path / "staging"
    directory = root / key
    directory.mkdir(parents=True)
    archive = directory / filename
    archive.write_bytes(b"bundle")
    return root, key, filename


def test_upload_resolves_archive_without_sidecar(tmp_path: Path) -> None:
    root, key, filename = _archive(tmp_path)

    archive, sidecar = resolve_persisted_bundle_paths(
        root,
        key,
        filename,
        ImportIntakeMode.UPLOAD.value,
    )

    assert archive == (root / key / filename).resolve()
    assert sidecar is None


def test_incoming_requires_ready_sidecar(tmp_path: Path) -> None:
    root, key, filename = _archive(tmp_path)

    with pytest.raises(ImportBundleStorageError) as captured:
        resolve_persisted_bundle_paths(
            root,
            key,
            filename,
            ImportIntakeMode.INCOMING.value,
        )

    assert captured.value.code == "import_sidecar_missing"


@pytest.mark.parametrize("key", ["short", "A" * 48, "g" * 48])
def test_rejects_invalid_storage_key(tmp_path: Path, key: str) -> None:
    root = tmp_path / "staging"
    root.mkdir()

    with pytest.raises(ImportBundleStorageError) as captured:
        resolve_persisted_bundle_paths(
            root,
            key,
            "bundle.htp.tar.gz",
            ImportIntakeMode.UPLOAD.value,
        )

    assert captured.value.code == "import_bundle_path_invalid"


@pytest.mark.parametrize("filename", ["../bundle.htp.tar.gz", "nested/bundle.htp.tar.gz"])
def test_rejects_non_basename_filename(tmp_path: Path, filename: str) -> None:
    root = tmp_path / "staging"
    root.mkdir()

    with pytest.raises(ImportBundleStorageError) as captured:
        resolve_persisted_bundle_paths(
            root,
            "a" * 48,
            filename,
            ImportIntakeMode.UPLOAD.value,
        )

    assert captured.value.code == "import_bundle_path_invalid"


def test_rejects_symlink_archive(tmp_path: Path) -> None:
    root = tmp_path / "staging"
    key = "a" * 48
    directory = root / key
    directory.mkdir(parents=True)
    target = tmp_path / "outside.htp.tar.gz"
    target.write_bytes(b"outside")
    (directory / "bundle.htp.tar.gz").symlink_to(target)

    with pytest.raises(ImportBundleStorageError) as captured:
        resolve_persisted_bundle_paths(
            root,
            key,
            "bundle.htp.tar.gz",
            ImportIntakeMode.UPLOAD.value,
        )

    assert captured.value.code == "import_bundle_missing"


def test_incoming_rejects_symlink_sidecar(tmp_path: Path) -> None:
    root, key, filename = _archive(tmp_path)
    target = tmp_path / "outside.sha256"
    target.write_text("digest", encoding="utf-8")
    (root / key / f"{filename}.sha256").symlink_to(target)

    with pytest.raises(ImportBundleStorageError) as captured:
        resolve_persisted_bundle_paths(
            root,
            key,
            filename,
            ImportIntakeMode.INCOMING.value,
        )

    assert captured.value.code == "import_sidecar_missing"
