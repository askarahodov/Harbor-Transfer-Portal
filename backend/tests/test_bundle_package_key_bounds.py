from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import PortalContour, Settings
from app.services.bundle_package_service import BundlePackageError, BundlePackageService


def _settings(tmp_path: Path, contour: PortalContour) -> Settings:
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "keys" / "source-private.pem"
    trusted_dir = tmp_path / "keys" / "trusted"
    private_path.parent.mkdir(parents=True, exist_ok=True)
    trusted_dir.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(private_path, 0o600)
    (trusted_dir / "source.pem").write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return Settings(
        _env_file=None,
        portal_contour=contour,
        bundle_signing_private_key_file=private_path,
        bundle_trusted_public_keys_dir=trusted_dir,
    )


def _forbid_key_read(monkeypatch: pytest.MonkeyPatch, blocked: Path) -> None:
    original = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if path == blocked:
            pytest.fail("oversized key material must be rejected before open()")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)


class _BoundedReadProbe(io.BytesIO):
    def __init__(self, contents: bytes, expected_read_size: int) -> None:
        super().__init__(contents)
        self.expected_read_size = expected_read_size

    def read(self, size: int = -1) -> bytes:
        assert size == self.expected_read_size
        return super().read(size)


def _replace_key_open_contents(
    monkeypatch: pytest.MonkeyPatch,
    target: Path,
    contents: bytes,
    *,
    expected_read_size: int,
) -> None:
    original = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if path == target:
            return _BoundedReadProbe(contents, expected_read_size)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)


def test_oversized_signing_key_is_rejected_before_read_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, PortalContour.SOURCE).model_copy(
        update={"bundle_key_material_max_bytes": 64}
    )
    path = settings.bundle_signing_private_key_file.absolute()
    path.write_bytes(b"x" * 65)
    os.chmod(path, 0o600)
    _forbid_key_read(monkeypatch, path)

    with pytest.raises(BundlePackageError) as exc_info:
        BundlePackageService(settings)._load_signing_private_key()

    assert exc_info.value.code == "bundle_signing_key_invalid"


def test_oversized_trusted_key_is_rejected_before_read_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, PortalContour.TARGET).model_copy(
        update={"bundle_key_material_max_bytes": 64}
    )
    path = next(settings.bundle_trusted_public_keys_dir.glob("*.pem")).absolute()
    path.write_bytes(b"x" * 65)
    _forbid_key_read(monkeypatch, path)

    with pytest.raises(BundlePackageError) as exc_info:
        BundlePackageService(settings)._load_trusted_public_keys()

    assert exc_info.value.code == "bundle_trusted_key_invalid"


def test_signing_key_growth_after_stat_is_rejected_by_bounded_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = 256
    settings = _settings(tmp_path, PortalContour.SOURCE).model_copy(
        update={"bundle_key_material_max_bytes": limit}
    )
    path = settings.bundle_signing_private_key_file.absolute()
    assert path.stat().st_size <= limit
    _replace_key_open_contents(
        monkeypatch,
        path,
        b"x" * (limit + 1),
        expected_read_size=limit + 1,
    )

    with pytest.raises(BundlePackageError) as exc_info:
        BundlePackageService(settings)._load_signing_private_key()

    assert exc_info.value.code == "bundle_signing_key_invalid"


def test_trusted_key_growth_after_stat_is_rejected_by_bounded_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = 256
    settings = _settings(tmp_path, PortalContour.TARGET).model_copy(
        update={"bundle_key_material_max_bytes": limit}
    )
    path = next(settings.bundle_trusted_public_keys_dir.glob("*.pem")).absolute()
    assert path.stat().st_size <= limit
    _replace_key_open_contents(
        monkeypatch,
        path,
        b"x" * (limit + 1),
        expected_read_size=limit + 1,
    )

    with pytest.raises(BundlePackageError) as exc_info:
        BundlePackageService(settings)._load_trusted_public_keys()

    assert exc_info.value.code == "bundle_trusted_key_invalid"


def test_signing_key_symlink_is_rejected_before_following_target(tmp_path: Path) -> None:
    settings = _settings(tmp_path, PortalContour.SOURCE)
    target = settings.bundle_signing_private_key_file
    symlink = target.with_name("signing-link.pem")
    symlink.symlink_to(target.name)
    service = BundlePackageService(
        settings.model_copy(update={"bundle_signing_private_key_file": symlink})
    )

    with pytest.raises(BundlePackageError) as exc_info:
        service._load_signing_private_key()

    assert exc_info.value.code == "bundle_signing_key_invalid"


def test_trusted_key_directory_symlink_is_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path, PortalContour.TARGET)
    real_directory = settings.bundle_trusted_public_keys_dir
    symlink = real_directory.with_name("trusted-link")
    symlink.symlink_to(real_directory.name, target_is_directory=True)
    service = BundlePackageService(
        settings.model_copy(update={"bundle_trusted_public_keys_dir": symlink})
    )

    with pytest.raises(BundlePackageError) as exc_info:
        service._load_trusted_public_keys()

    assert exc_info.value.code == "bundle_trust_not_configured"
