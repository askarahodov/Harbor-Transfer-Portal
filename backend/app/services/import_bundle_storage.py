from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.domain.imports import ImportIntakeMode


@dataclass(slots=True)
class ImportBundleStorageError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def resolve_persisted_bundle_paths(
    staging_root: Path,
    storage_key: str | None,
    filename: str | None,
    intake_mode: str | None,
) -> tuple[Path, Path | None]:
    if storage_key is None or filename is None:
        raise ImportBundleStorageError(
            "import_bundle_missing",
            "Persisted bundle path metadata отсутствует",
        )

    if len(storage_key) != 48 or any(
        character not in "0123456789abcdef" for character in storage_key
    ):
        raise ImportBundleStorageError(
            "import_bundle_path_invalid",
            "Storage key import bundle некорректен",
        )

    if Path(filename).name != filename:
        raise ImportBundleStorageError(
            "import_bundle_path_invalid",
            "Имя import bundle некорректно",
        )

    root = staging_root.resolve()
    archive_path = root / storage_key / filename
    if archive_path.is_symlink():
        raise ImportBundleStorageError(
            "import_bundle_missing",
            "Import bundle отсутствует в staging",
        )
    archive = archive_path.resolve()
    try:
        archive.relative_to(root)
    except ValueError as exc:
        raise ImportBundleStorageError(
            "import_bundle_path_invalid",
            "Import bundle находится вне staging root",
        ) from exc

    if archive.is_symlink() or not archive.is_file():
        raise ImportBundleStorageError(
            "import_bundle_missing",
            "Import bundle отсутствует в staging",
        )

    if intake_mode != ImportIntakeMode.INCOMING.value:
        return archive, None

    sidecar = archive.with_name(archive.name + ".sha256")
    if sidecar.is_symlink() or not sidecar.is_file():
        raise ImportBundleStorageError(
            "import_sidecar_missing",
            "Incoming bundle не имеет readiness .sha256 sidecar",
        )

    return archive, sidecar
