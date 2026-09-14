from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import PortalContour, Settings
from app.services.bundle_package_service import BundlePackageError, BundlePackageService


def _settings(
    tmp_path: Path,
    *,
    contour: PortalContour,
    private_path: Path | None = None,
    trusted_dir: Path | None = None,
    limit: int = 1024,
) -> Settings:
    return Settings(
        _env_file=None,
        portal_contour=contour,
        bundle_payload_root=tmp_path / "data",
        bundle_temp_root=tmp_path / "data" / "tmp" / "bundles",
        bundle_outgoing_root=tmp_path / "data" / "outgoing",
        bundle_extract_root=tmp_path / "data" / "incoming" / "verified",
        bundle_signing_private_key_file=private_path
        or tmp_path / "keys" / "source-private.pem",
        bundle_trusted_public_keys_dir=trusted_dir
        or tmp_path / "keys" / "trusted",
        bundle_key_material_max_bytes=limit,
    )


def _private_pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_pem(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _forbid_open_of(monkeypatch: pytest.MonkeyPatch, forbidden: Path) -> None:
    expected = forbidden.resolve()
    real_open = Path.open

    def guarded_open(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if self.resolve() == expected:
            pytest.fail(f"oversized key material was opened for reading: {self}")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)


def test_oversized_signing_key_is_rejected_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_path = tmp_path / "keys" / "source-private.pem"
    private_path.parent.mkdir(parents=True)
    private_path.write_bytes(b"x" * 1025)
    os.chmod(private_path, 0o600)
    service = BundlePackageService(
        _settings(
            tmp_path,
            contour=PortalContour.SOURCE,
            private_path=private_path,
            limit=1024,
        )
    )
    _forbid_open_of(monkeypatch, private_path)

    with pytest.raises(BundlePackageError) as exc:
        service._load_signing_private_key()

    assert exc.value.code == "bundle_signing_key_invalid"
    assert "размер" in exc.value.message


def test_oversized_trusted_key_is_rejected_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_dir = tmp_path / "keys" / "trusted"
    trusted_dir.mkdir(parents=True)
    trusted_path = trusted_dir / "source.pem"
    trusted_path.write_bytes(b"x" * 1025)
    service = BundlePackageService(
        _settings(
            tmp_path,
            contour=PortalContour.TARGET,
            trusted_dir=trusted_dir,
            limit=1024,
        )
    )
    _forbid_open_of(monkeypatch, trusted_path)

    with pytest.raises(BundlePackageError) as exc:
        service._load_trusted_public_keys()

    assert exc.value.code == "bundle_trusted_key_invalid"
    assert "размер" in exc.value.message


def test_valid_signing_key_still_loads_with_bounded_reader(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "keys" / "source-private.pem"
    private_path.parent.mkdir(parents=True)
    private_path.write_bytes(_private_pem(private_key))
    os.chmod(private_path, 0o600)
    service = BundlePackageService(
        _settings(
            tmp_path,
            contour=PortalContour.SOURCE,
            private_path=private_path,
            limit=1024,
        )
    )

    loaded = service._load_signing_private_key()

    assert loaded.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ) == private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def test_valid_trusted_key_still_loads_with_bounded_reader(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    trusted_dir = tmp_path / "keys" / "trusted"
    trusted_dir.mkdir(parents=True)
    (trusted_dir / "source.pem").write_bytes(_public_pem(private_key))
    service = BundlePackageService(
        _settings(
            tmp_path,
            contour=PortalContour.TARGET,
            trusted_dir=trusted_dir,
            limit=1024,
        )
    )

    loaded = service._load_trusted_public_keys()

    assert len(loaded) == 1
    assert loaded[0].public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ) == private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
