from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tarfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import PortalContour, Settings
from app.domain.bundle import BundleSource
from app.services import bundle_package_service as package_module
from app.services.bundle_package_service import (
    BundlePackageError,
    BundlePackageService,
    ContainerImagePackageInput,
    HelmChartPackageInput,
)

DIGEST = "sha256:" + "a" * 64
DELIVERY_ID = "DELIVERY-20260911-ABC123"


def _write_keys(tmp_path: Path) -> tuple[Path, Path]:
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
    return private_path, trusted_dir


def _settings(
    tmp_path: Path,
    *,
    contour: PortalContour = PortalContour.SOURCE,
    max_members: int = 1000,
) -> Settings:
    private_path, trusted_dir = _write_keys(tmp_path)
    return Settings(
        _env_file=None,
        portal_contour=contour,
        bundle_payload_root=tmp_path / "data",
        bundle_temp_root=tmp_path / "data" / "tmp" / "bundles",
        bundle_outgoing_root=tmp_path / "data" / "outgoing",
        bundle_extract_root=tmp_path / "data" / "verified",
        bundle_signing_private_key_file=private_path,
        bundle_trusted_public_keys_dir=trusted_dir,
        bundle_max_archive_bytes=20 * 1024 * 1024,
        bundle_max_extracted_bytes=20 * 1024 * 1024,
        bundle_max_member_count=max_members,
        bundle_max_path_bytes=512,
        bundle_max_metadata_bytes=1024 * 1024,
        bundle_max_compression_ratio=1000,
    )


def _inputs(tmp_path: Path) -> list[ContainerImagePackageInput | HelmChartPackageInput]:
    data = tmp_path / "data"
    image = data / "export" / "image"
    (image / "blobs" / "sha256").mkdir(parents=True, exist_ok=True)
    (image / "oci-layout").write_text(
        '{"imageLayoutVersion":"1.0.0"}',
        encoding="utf-8",
    )
    (image / "index.json").write_text('{"schemaVersion":2}', encoding="utf-8")
    (image / "blobs" / "sha256" / "abc").write_bytes(b"blob-content")
    chart = data / "packages" / "sample-1.2.3.tgz"
    chart.parent.mkdir(parents=True, exist_ok=True)
    chart.write_bytes(b"synthetic-chart-package")
    return [
        ContainerImagePackageInput(
            repository="project/app",
            reference="1.0.0",
            source_digest=DIGEST,
            source_path=image,
            payload_path="images/project-app",
        ),
        HelmChartPackageInput(
            repository="project/charts",
            name="sample",
            version="1.2.3",
            source_digest=DIGEST,
            source_path=chart,
            payload_path="charts/sample-1.2.3.tgz",
        ),
    ]


def _build(tmp_path: Path, settings: Settings | None = None):  # type: ignore[no-untyped-def]
    effective = settings or _settings(tmp_path)
    service = BundlePackageService(effective)
    result = service.build_bundle(
        source=BundleSource(
            contour="SOURCE",
            harbor="harbor.source.local",
            portal_version="0.1.0",
        ),
        created_by="operator",
        artifacts=_inputs(tmp_path),
        delivery_id=DELIVERY_ID,
        created_at=datetime(2026, 9, 11, 9, 0, tzinfo=UTC),
        comment="offline release",
    )
    return service, result


def _rewrite_archive(
    source: Path,
    destination: Path,
    transform: Callable[[str, bytes], bytes | None],
    *,
    extra: tarfile.TarInfo | None = None,
    extra_bytes: bytes = b"",
) -> None:
    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(source, "r:gz") as archive:
        for member in archive.getmembers():
            stream = archive.extractfile(member) if member.isfile() else None
            payload = stream.read() if stream is not None else None
            if payload is not None:
                payload = transform(member.name, payload)
                if payload is None:
                    continue
                member.size = len(payload)
            entries.append((member, payload))
    with destination.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for member, payload in entries:
                    archive.addfile(
                        member,
                        io.BytesIO(payload) if payload is not None else None,
                    )
                if extra is not None:
                    extra.size = len(extra_bytes) if extra.isfile() else extra.size
                    archive.addfile(
                        extra,
                        io.BytesIO(extra_bytes) if extra.isfile() else None,
                    )


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def test_valid_bundle_round_trip_and_independent_target_verification(tmp_path: Path) -> None:
    service, built = _build(tmp_path)

    assert built.archive_path.is_file()
    assert built.sidecar_path.is_file()
    assert built.handoff_path.is_file()
    assert built.handoff_path.name == f"{DELIVERY_ID}.htp-handoff.json"
    assert built.handoff_sha256 == hashlib.sha256(
        built.handoff_path.read_bytes()
    ).hexdigest()
    assert built.handoff_size == built.handoff_path.stat().st_size
    assert built.sidecar_path.read_text(encoding="utf-8").endswith(
        f"  {built.archive_path.name}\n"
    )
    verified = service.verify_bundle(
        built.archive_path,
        sidecar_path=built.sidecar_path,
    )

    assert verified.manifest == built.manifest
    assert verified.archive_sha256 == built.archive_sha256
    assert verified.signing_key_fingerprint == built.signing_key_fingerprint
    assert len(verified.manifest.artifacts) == 2
    image = verified.manifest.artifacts[0]
    assert image.payload_path == "images/project-app"
    assert image.payload_size > 0
    assert len(image.payload_sha256) == 64


def test_manifest_tamper_breaks_signature(tmp_path: Path) -> None:
    service, built = _build(tmp_path)
    tampered = tmp_path / "tampered-manifest.tar.gz"

    def transform(name: str, payload: bytes) -> bytes:
        if name != "manifest.json":
            return payload
        manifest = json.loads(payload)
        manifest["created_by"] = "attacker"
        return _canonical(manifest)

    _rewrite_archive(built.archive_path, tampered, transform)
    with pytest.raises(BundlePackageError) as exc:
        service.verify_bundle(tampered)
    assert exc.value.code == "bundle_signature_untrusted"


def test_json_schema_is_checked_before_signature(tmp_path: Path) -> None:
    service, built = _build(tmp_path)
    tampered = tmp_path / "schema-before-signature.tar.gz"

    def transform(name: str, payload: bytes) -> bytes:
        if name != "manifest.json":
            return payload
        manifest = json.loads(payload)
        del manifest["created_by"]
        return _canonical(manifest)

    _rewrite_archive(built.archive_path, tampered, transform)
    with pytest.raises(BundlePackageError) as exc:
        service.verify_bundle(tampered)
    assert exc.value.code == "bundle_schema_invalid"


def test_payload_tamper_breaks_checksum(tmp_path: Path) -> None:
    service, built = _build(tmp_path)
    tampered = tmp_path / "tampered-payload.tar.gz"
    _rewrite_archive(
        built.archive_path,
        tampered,
        lambda name, payload: b"changed" if name.endswith(".tgz") else payload,
    )

    with pytest.raises(BundlePackageError) as exc:
        service.verify_bundle(tampered)
    assert exc.value.code == "bundle_payload_checksum_mismatch"


def test_unsupported_schema_is_rejected_before_signature_check(tmp_path: Path) -> None:
    service, built = _build(tmp_path)
    tampered = tmp_path / "unsupported-schema.tar.gz"

    def transform(name: str, payload: bytes) -> bytes:
        if name != "manifest.json":
            return payload
        manifest = json.loads(payload)
        manifest["schema_version"] = "2.0"
        return _canonical(manifest)

    _rewrite_archive(built.archive_path, tampered, transform)
    with pytest.raises(BundlePackageError) as exc:
        service.verify_bundle(tampered)
    assert exc.value.code == "bundle_schema_unsupported"


@pytest.mark.parametrize("critical", ["manifest.json", "manifest.sig", "checksums.sha256"])
def test_missing_required_security_file_is_rejected(tmp_path: Path, critical: str) -> None:
    service, built = _build(tmp_path)
    malformed = tmp_path / f"missing-{critical}.tar.gz"
    _rewrite_archive(
        built.archive_path,
        malformed,
        lambda name, payload: None if name == critical else payload,
    )

    with pytest.raises(BundlePackageError) as exc:
        service.verify_bundle(malformed)
    assert exc.value.code == "bundle_archive_unsafe"


def test_duplicate_security_file_is_rejected(tmp_path: Path) -> None:
    service, built = _build(tmp_path)
    malformed = tmp_path / "duplicate-manifest.tar.gz"
    duplicate = tarfile.TarInfo("manifest.json")
    duplicate.type = tarfile.REGTYPE
    _rewrite_archive(
        built.archive_path,
        malformed,
        lambda _name, payload: payload,
        extra=duplicate,
        extra_bytes=b"{}",
    )

    with pytest.raises(BundlePackageError) as exc:
        service.verify_bundle(malformed)
    assert exc.value.code == "bundle_archive_unsafe"


@pytest.mark.parametrize(
    ("name", "member_type"),
    [
        ("../escape", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("images/link", tarfile.SYMTYPE),
        ("images/hard", tarfile.LNKTYPE),
        ("images/pipe", tarfile.FIFOTYPE),
    ],
)
def test_malicious_archive_members_are_rejected(
    tmp_path: Path,
    name: str,
    member_type: bytes,
) -> None:
    service, built = _build(tmp_path)
    malformed = tmp_path / "malicious.tar.gz"
    member = tarfile.TarInfo(name)
    member.type = member_type
    if member.issym() or member.islnk():
        member.linkname = "manifest.json"
    _rewrite_archive(
        built.archive_path,
        malformed,
        lambda _name, payload: payload,
        extra=member,
        extra_bytes=b"x" if member.isfile() else b"",
    )

    with pytest.raises(BundlePackageError) as exc:
        service.verify_bundle(malformed)
    assert exc.value.code == "bundle_archive_unsafe"


def test_member_outside_allowed_top_level_is_rejected(tmp_path: Path) -> None:
    service, built = _build(tmp_path)
    malformed = tmp_path / "unexpected-top-level.tar.gz"
    member = tarfile.TarInfo("unexpected/file.bin")
    member.type = tarfile.REGTYPE
    _rewrite_archive(
        built.archive_path,
        malformed,
        lambda _name, payload: payload,
        extra=member,
        extra_bytes=b"unexpected",
    )

    with pytest.raises(BundlePackageError) as exc:
        service.verify_bundle(malformed)
    assert exc.value.code == "bundle_archive_unsafe"


def test_member_limit_is_enforced_before_payload_read(tmp_path: Path) -> None:
    service, built = _build(tmp_path)
    limited_settings = service.settings.model_copy(
        update={"bundle_max_member_count": 4}
    )
    limited = BundlePackageService(limited_settings)

    with pytest.raises(BundlePackageError) as exc:
        limited.verify_bundle(built.archive_path)
    assert exc.value.code == "bundle_member_limit_exceeded"


def test_private_key_permissions_are_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    os.chmod(settings.bundle_signing_private_key_file, 0o644)
    service = BundlePackageService(settings)

    with pytest.raises(BundlePackageError) as exc:
        service.build_bundle(
            source=BundleSource(contour="SOURCE", harbor="harbor.source.local"),
            created_by="operator",
            artifacts=_inputs(tmp_path),
            delivery_id=DELIVERY_ID,
        )
    assert exc.value.code == "bundle_signing_key_permissions"


def test_target_contour_cannot_build_signed_bundle(tmp_path: Path) -> None:
    settings = _settings(tmp_path, contour=PortalContour.TARGET)
    service = BundlePackageService(settings)

    with pytest.raises(BundlePackageError) as exc:
        service.build_bundle(
            source=BundleSource(contour="SOURCE", harbor="harbor.source.local"),
            created_by="operator",
            artifacts=_inputs(tmp_path),
        )
    assert exc.value.code == "bundle_build_wrong_contour"


def test_sidecar_mismatch_is_rejected(tmp_path: Path) -> None:
    service, built = _build(tmp_path)
    built.sidecar_path.write_text(
        f"{'0' * 64}  {built.archive_path.name}\n",
        encoding="utf-8",
    )

    with pytest.raises(BundlePackageError) as exc:
        service.verify_bundle(built.archive_path, sidecar_path=built.sidecar_path)
    assert exc.value.code == "bundle_sidecar_checksum_mismatch"


def test_verified_bundle_extracts_only_under_configured_root(tmp_path: Path) -> None:
    service, built = _build(tmp_path)
    destination = service.extract_root / DELIVERY_ID
    verified = service.verify_bundle(
        built.archive_path,
        sidecar_path=built.sidecar_path,
        extract_to=destination,
    )

    assert verified.extracted_root == destination.resolve()
    assert (destination / "manifest.json").is_file()
    assert (destination / "charts" / "sample-1.2.3.tgz").is_file()

    with pytest.raises(BundlePackageError) as exc:
        service.verify_bundle(built.archive_path, extract_to=tmp_path / "outside")
    assert exc.value.code == "bundle_extract_path_outside_root"


def test_publish_writes_archive_before_readiness_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    service = BundlePackageService(settings)
    real_replace = os.replace
    destinations: list[str] = []

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        destinations.append(Path(destination).name)
        real_replace(source, destination)

    monkeypatch.setattr(package_module.os, "replace", recording_replace)
    result = service.build_bundle(
        source=BundleSource(contour="SOURCE", harbor="harbor.source.local"),
        created_by="operator",
        artifacts=_inputs(tmp_path),
        delivery_id=DELIVERY_ID,
    )

    archive_index = destinations.index(result.archive_path.name)
    sidecar_index = destinations.index(result.sidecar_path.name)
    handoff_index = destinations.index(result.handoff_path.name)
    assert archive_index < sidecar_index < handoff_index


def test_failed_sidecar_publish_cleans_archive_and_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    service = BundlePackageService(settings)
    real_replace = os.replace

    def failing_replace(source: str | Path, destination: str | Path) -> None:
        if Path(destination).name.endswith(".sha256"):
            raise OSError("synthetic sidecar publish failure")
        real_replace(source, destination)

    monkeypatch.setattr(package_module.os, "replace", failing_replace)
    with pytest.raises(BundlePackageError) as exc:
        service.build_bundle(
            source=BundleSource(contour="SOURCE", harbor="harbor.source.local"),
            created_by="operator",
            artifacts=_inputs(tmp_path),
            delivery_id=DELIVERY_ID,
        )

    assert exc.value.code == "bundle_io_error"
    outgoing = settings.bundle_outgoing_root
    assert not (outgoing / f"{DELIVERY_ID}.htp.tar.gz").exists()
    assert not (outgoing / f"{DELIVERY_ID}.htp.tar.gz.sha256").exists()


def test_concurrent_delivery_reservation_is_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = BundlePackageService(settings)
    service.outgoing_root.mkdir(parents=True, exist_ok=True)
    reservation = service.outgoing_root / f".{DELIVERY_ID}.reserve"
    reservation.write_text("reserved", encoding="utf-8")

    with pytest.raises(BundlePackageError) as exc:
        service.build_bundle(
            source=BundleSource(contour="SOURCE", harbor="harbor.source.local"),
            created_by="operator",
            artifacts=_inputs(tmp_path),
            delivery_id=DELIVERY_ID,
        )
    assert exc.value.code == "bundle_delivery_exists"


def test_non_normalized_payload_path_is_rejected(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    image = inputs[0]
    assert isinstance(image, ContainerImagePackageInput)
    inputs[0] = ContainerImagePackageInput(
        repository=image.repository,
        reference=image.reference,
        source_digest=image.source_digest,
        source_path=image.source_path,
        payload_path="images//project-app",
    )
    service = BundlePackageService(_settings(tmp_path))

    with pytest.raises(BundlePackageError) as exc:
        service.build_bundle(
            source=BundleSource(contour="SOURCE", harbor="harbor.source.local"),
            created_by="operator",
            artifacts=inputs,
            delivery_id=DELIVERY_ID,
        )
    assert exc.value.code == "bundle_payload_path_invalid"


def test_runtime_manifest_schema_matches_normative_document(tmp_path: Path) -> None:
    service = BundlePackageService(_settings(tmp_path))
    runtime_schema = service._load_manifest_schema()
    repository_root = Path(__file__).resolve().parents[2]
    normative = json.loads(
        (repository_root / "docs" / "schema" / "manifest-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert runtime_schema == normative
