import stat
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import PortalContour, Settings
from app.domain.bundle import BundleSource
from app.services.bundle_package_service import (
    BundlePackageError,
    BundlePackageService,
    HelmChartPackageInput,
)
from app.services.key_management import (
    KeyManagementError,
    KeyManagementService,
    public_key_fingerprint,
)


def _private_pem(key: Ed25519PrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _public_pem(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def _settings(tmp_path: Path, contour: PortalContour) -> Settings:
    return Settings(
        portal_contour=contour,
        bundle_payload_root=tmp_path / "payload",
        bundle_temp_root=tmp_path / "tmp",
        bundle_outgoing_root=tmp_path / "outgoing",
        bundle_extract_root=tmp_path / "verified",
        bundle_signing_private_key_file=tmp_path / "keys" / "source-private.pem",
        bundle_trusted_public_keys_dir=tmp_path / "keys" / "trusted",
    )


def test_source_private_key_install_is_atomic_private_and_fingerprint_only(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    settings = _settings(tmp_path, PortalContour.SOURCE)
    service = KeyManagementService(settings)

    assert service.source_status().configured is False
    status = service.install_source_private_key(_private_pem(key))

    assert status.configured is True
    assert status.fingerprint == public_key_fingerprint(key.public_key())
    assert "PRIVATE" not in status.fingerprint
    mode = stat.S_IMODE(settings.bundle_signing_private_key_file.stat().st_mode)
    assert mode == 0o600
    assert service.source_status() == status


def test_target_rejects_private_key_as_public_and_supports_overlap_rotation(tmp_path: Path) -> None:
    first = Ed25519PrivateKey.generate()
    second = Ed25519PrivateKey.generate()
    service = KeyManagementService(_settings(tmp_path, PortalContour.TARGET))

    with pytest.raises(KeyManagementError, match="public key"):
        service.add_trusted_key(_private_pem(first))

    first_status = service.add_trusted_key(_public_pem(first))
    second_status = service.add_trusted_key(_public_pem(second))
    assert first_status.enabled is True
    assert second_status.enabled is True
    assert service.list_trusted_keys() == tuple(
        sorted((first_status, second_status), key=lambda item: item.fingerprint)
    )

    disabled = service.set_trusted_key_enabled(first_status.fingerprint, enabled=False)
    assert disabled.enabled is False
    enabled = service.set_trusted_key_enabled(first_status.fingerprint, enabled=True)
    assert enabled.enabled is True

    replacement = service.replace_trusted_key(first_status.fingerprint, _public_pem(second))
    assert replacement.fingerprint == second_status.fingerprint
    assert service.list_trusted_keys() == (second_status,)

    service.remove_trusted_key(second_status.fingerprint)
    assert service.list_trusted_keys() == ()


def test_managed_target_trust_is_consumed_by_bundle_verifier(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    source_settings = _settings(tmp_path / "source", PortalContour.SOURCE)
    source_settings.bundle_payload_root.mkdir(parents=True)
    chart = source_settings.bundle_payload_root / "demo-1.2.3.tgz"
    chart.write_bytes(b"offline chart fixture")
    KeyManagementService(source_settings).install_source_private_key(_private_pem(key))

    build = BundlePackageService(source_settings).build_bundle(
        source=BundleSource(contour="SOURCE", harbor="source.local", portal_version="test"),
        created_by="operator",
        delivery_id="DELIVERY-20260914-KEYTEST1",
        artifacts=(
            HelmChartPackageInput(
                repository="library/charts",
                name="demo",
                version="1.2.3",
                source_digest=None,
                source_path=chart,
                payload_path="charts/demo-1.2.3.tgz",
            ),
        ),
    )

    target_settings = _settings(tmp_path / "target", PortalContour.TARGET)
    trust = KeyManagementService(target_settings)
    record = trust.add_trusted_key(_public_pem(key))
    verified = BundlePackageService(target_settings).verify_bundle(
        build.archive_path,
        sidecar_path=build.sidecar_path,
    )
    assert verified.signing_key_fingerprint == record.fingerprint

    trust.set_trusted_key_enabled(record.fingerprint, enabled=False)
    with pytest.raises(BundlePackageError) as exc_info:
        BundlePackageService(target_settings).verify_bundle(
            build.archive_path,
            sidecar_path=build.sidecar_path,
        )
    assert exc_info.value.code == "bundle_trust_not_configured"


def test_fingerprint_matches_package_service_semantics() -> None:
    key = Ed25519PrivateKey.generate().public_key()
    assert public_key_fingerprint(key) == BundlePackageService._public_key_fingerprint(key)
