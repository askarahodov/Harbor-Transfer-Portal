from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import Settings
from app.services.bundle_package_service import BundlePackageError, BundlePackageService


def _settings(tmp_path: Path) -> Settings:
    trusted_dir = tmp_path / "trusted"
    trusted_dir.mkdir()
    return Settings(
        _env_file=None,
        bundle_signing_private_key_file=tmp_path / "source-signing-private.pem",
        bundle_trusted_public_keys_dir=trusted_dir,
        bundle_key_material_max_bytes=1024,
    )


def _fail_read_bytes(_path: Path) -> bytes:
    pytest.fail("oversized key material must be rejected before Path.read_bytes()")


def test_oversized_signing_key_is_rejected_before_read_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    settings.bundle_signing_private_key_file.write_bytes(b"x" * 1025)
    os.chmod(settings.bundle_signing_private_key_file, 0o600)
    service = BundlePackageService(settings)
    monkeypatch.setattr(Path, "read_bytes", _fail_read_bytes)

    with pytest.raises(BundlePackageError) as exc_info:
        service._load_signing_private_key()

    assert exc_info.value.code == "bundle_signing_key_invalid"


def test_oversized_trusted_key_is_rejected_before_read_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    trusted_key = settings.bundle_trusted_public_keys_dir / "source.pem"
    trusted_key.write_bytes(b"x" * 1025)
    service = BundlePackageService(settings)
    monkeypatch.setattr(Path, "read_bytes", _fail_read_bytes)

    with pytest.raises(BundlePackageError) as exc_info:
        service._load_trusted_public_keys()

    assert exc_info.value.code == "bundle_trusted_key_invalid"


def test_signing_key_symlink_is_rejected_before_following_target(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    real_key = tmp_path / "real-signing-key.pem"
    key = Ed25519PrivateKey.generate()
    real_key.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(real_key, 0o600)
    settings.bundle_signing_private_key_file.symlink_to(real_key)
    service = BundlePackageService(settings)

    with pytest.raises(BundlePackageError) as exc_info:
        service._load_signing_private_key()

    assert exc_info.value.code == "bundle_signing_key_invalid"


def test_trusted_directory_symlink_is_not_accepted(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    real_dir = tmp_path / "real-trusted"
    real_dir.mkdir()
    key = Ed25519PrivateKey.generate()
    (real_dir / "source.pem").write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    settings.bundle_trusted_public_keys_dir.rmdir()
    settings.bundle_trusted_public_keys_dir.symlink_to(real_dir, target_is_directory=True)
    service = BundlePackageService(settings)

    with pytest.raises(BundlePackageError) as exc_info:
        service._load_trusted_public_keys()

    assert exc_info.value.code == "bundle_trust_not_configured"
