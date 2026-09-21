from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import PortalContour, Settings
from app.domain.bundle import BundleManifest, BundleSource, ContainerImageArtifact
from app.services.key_management import KeyManagementService
from app.services.key_material import ed25519_public_key_fingerprint
from app.services.media_handoff import MediaHandoffError, MediaHandoffService

DELIVERY_ID = "DELIVERY-20260921-HANDOFF1"


def _source_settings(tmp_path: Path, private_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        portal_contour=PortalContour.SOURCE,
        bundle_signing_private_key_file=private_path,
        bundle_outgoing_root=tmp_path / "source" / "outgoing",
        bundle_max_metadata_bytes=1024 * 1024,
    )


def _target_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        portal_contour=PortalContour.TARGET,
        bundle_trusted_public_keys_dir=tmp_path / "target" / "keys" / "trusted-source",
        import_discovery_root=tmp_path / "target" / "incoming",
        bundle_max_metadata_bytes=1024 * 1024,
    )


def _private_key(tmp_path: Path) -> tuple[Ed25519PrivateKey, Path]:
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "source" / "keys" / "source-private.pem"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(path, 0o600)
    return key, path


def _public_pem(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _manifest() -> BundleManifest:
    return BundleManifest(
        schema_version="1.0",
        delivery_id=DELIVERY_ID,
        created_at=datetime(2026, 9, 21, 11, 0, tzinfo=UTC),
        created_by="source-operator",
        source=BundleSource(
            contour="SOURCE",
            harbor="https://harbor.source.local",
            portal_version="1.0.0",
        ),
        artifacts=[
            ContainerImageArtifact(
                repository="team/example",
                reference="1.0.0",
                source_digest="sha256:" + "a" * 64,
                payload_path="images/1",
                payload_sha256="b" * 64,
                payload_size=1,
            )
        ],
    )


def _published_files(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "source" / "outgoing"
    root.mkdir(parents=True, exist_ok=True)
    bundle = root / f"{DELIVERY_ID}.htp.tar.gz"
    bundle.write_bytes(b"signed-bundle-fixture")
    import hashlib

    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    sidecar = bundle.with_name(bundle.name + ".sha256")
    sidecar.write_text(f"{digest}  {bundle.name}\n", encoding="utf-8")
    return bundle, sidecar


def _stage_target(
    target: Settings,
    bundle: Path,
    sidecar: Path,
) -> tuple[Path, Path]:
    root = target.import_discovery_root
    root.mkdir(parents=True, exist_ok=True)
    target_bundle = root / bundle.name
    target_sidecar = root / sidecar.name
    target_bundle.write_bytes(bundle.read_bytes())
    target_sidecar.write_bytes(sidecar.read_bytes())
    return target_bundle, target_sidecar


def test_signed_handoff_round_trip_verifies_exact_physical_files(tmp_path: Path) -> None:
    key, private_path = _private_key(tmp_path)
    source = _source_settings(tmp_path, private_path)
    target = _target_settings(tmp_path)
    bundle, sidecar = _published_files(tmp_path)

    built = MediaHandoffService(source).build(
        manifest=_manifest(),
        bundle_path=bundle,
        sidecar_path=sidecar,
        private_key=key,
    )
    fingerprint = ed25519_public_key_fingerprint(key.public_key())
    assert built.signing_key_fingerprint == fingerprint
    assert built.filename == f"{DELIVERY_ID}.htp-handoff.json"
    assert b"PRIVATE KEY" not in built.payload

    KeyManagementService(target).add_trusted_public_key(_public_pem(key))
    _stage_target(target, bundle, sidecar)

    verified = MediaHandoffService(target).verify_from_discovery(built.payload)
    assert verified.delivery_id == DELIVERY_ID
    assert verified.signing_key_fingerprint == fingerprint
    assert verified.created_by == "source-operator"
    assert verified.bundle_size_bytes == bundle.stat().st_size


def test_signed_handoff_can_bind_optional_trust_packages(tmp_path: Path) -> None:
    key, private_path = _private_key(tmp_path)
    source = _source_settings(tmp_path, private_path)
    target = _target_settings(tmp_path)
    bundle, sidecar = _published_files(tmp_path)
    source_trust = tmp_path / "source" / "outgoing" / "bootstrap.htp-trust.tar.gz"
    pending_trust = tmp_path / "source" / "outgoing" / "rotation.htp-trust.tar.gz"
    source_trust.write_bytes(b"bootstrap-public-trust")
    pending_trust.write_bytes(b"pending-public-trust")

    built = MediaHandoffService(source).build(
        manifest=_manifest(),
        bundle_path=bundle,
        sidecar_path=sidecar,
        private_key=key,
        optional_files=(
            ("source-trust-package", source_trust),
            ("pending-trust-package", pending_trust),
        ),
    )
    document = json.loads(built.payload)
    assert [item["role"] for item in document["payload"]["files"]] == [
        "bundle",
        "bundle-sidecar",
        "source-trust-package",
        "pending-trust-package",
    ]

    KeyManagementService(target).add_trusted_public_key(_public_pem(key))
    _stage_target(target, bundle, sidecar)
    target.import_discovery_root.joinpath(source_trust.name).write_bytes(
        source_trust.read_bytes()
    )
    target.import_discovery_root.joinpath(pending_trust.name).write_bytes(
        pending_trust.read_bytes()
    )

    verified = MediaHandoffService(target).verify_from_discovery(built.payload)
    assert verified.delivery_id == DELIVERY_ID

    target.import_discovery_root.joinpath(pending_trust.name).write_bytes(b"tampered")
    with pytest.raises(MediaHandoffError) as exc:
        MediaHandoffService(target).verify_from_discovery(built.payload)
    assert exc.value.code == "handoff_file_mismatch"


def test_handoff_rejects_physical_bundle_tamper_before_import(tmp_path: Path) -> None:
    key, private_path = _private_key(tmp_path)
    source = _source_settings(tmp_path, private_path)
    target = _target_settings(tmp_path)
    bundle, sidecar = _published_files(tmp_path)
    built = MediaHandoffService(source).build(
        manifest=_manifest(),
        bundle_path=bundle,
        sidecar_path=sidecar,
        private_key=key,
    )
    KeyManagementService(target).add_trusted_public_key(_public_pem(key))
    target_bundle, _target_sidecar = _stage_target(target, bundle, sidecar)
    target_bundle.write_bytes(b"tampered-after-physical-copy")

    with pytest.raises(MediaHandoffError) as exc:
        MediaHandoffService(target).verify_from_discovery(built.payload)
    assert exc.value.code == "handoff_file_mismatch"


def test_handoff_rejects_signature_tamper_and_disabled_signer(tmp_path: Path) -> None:
    key, private_path = _private_key(tmp_path)
    source = _source_settings(tmp_path, private_path)
    target = _target_settings(tmp_path)
    bundle, sidecar = _published_files(tmp_path)
    built = MediaHandoffService(source).build(
        manifest=_manifest(),
        bundle_path=bundle,
        sidecar_path=sidecar,
        private_key=key,
    )
    mutation = KeyManagementService(target).add_trusted_public_key(_public_pem(key))
    _stage_target(target, bundle, sidecar)

    document = json.loads(built.payload)
    document["signature"] = base64.b64encode(b"\x00" * 64).decode("ascii")
    tampered = (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    with pytest.raises(MediaHandoffError) as bad_signature:
        MediaHandoffService(target).verify_from_discovery(tampered)
    assert bad_signature.value.code == "handoff_signature_invalid"

    KeyManagementService(target).set_trusted_key_enabled(mutation.fingerprint, False)
    with pytest.raises(MediaHandoffError) as disabled:
        MediaHandoffService(target).verify_from_discovery(built.payload)
    assert disabled.value.code == "handoff_signer_untrusted"
