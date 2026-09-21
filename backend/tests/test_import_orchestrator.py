from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import PortalContour, Settings
from app.db.base import Base
from app.db.models import Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import ArtifactStatus, BundleSource, OperationStatus
from app.domain.imports import ImportPreviewState
from app.services.bundle_package_service import (
    BundlePackageService,
    ContainerImagePackageInput,
    HelmChartPackageInput,
)
from app.services.helm_oci_service import (
    HelmPackageMetadata,
    HelmPushResult,
    HelmTargetInspection,
    HelmTargetState,
)
from app.services.import_orchestrator import ImportOrchestrationError, ImportOrchestrator
from app.services.key_management import KeyManagementService
from app.services.operation_manager import OperationManager
from app.services.skopeo_service import (
    ImportResult,
    SkopeoServiceError,
    TargetInspection,
    TargetState,
)

IMAGE_DIGEST = "sha256:" + "a" * 64
CHART_DIGEST = "sha256:" + "b" * 64
CONFLICT_DIGEST = "sha256:" + "c" * 64
DELIVERY_ID = "DELIVERY-20260911-IMPORT01"


class FakeSkopeoService:
    def __init__(
        self,
        state: TargetState = TargetState.ABSENT,
        *,
        fail_import: bool = False,
    ) -> None:
        self.state = state
        self.fail_import = fail_import
        self.inspect_calls = 0
        self.import_calls = 0

    async def inspect_target(self, _image, *, expected_digest=None):  # type: ignore[no-untyped-def]
        self.inspect_calls += 1
        digest = None
        if self.state is TargetState.SAME_DIGEST:
            digest = expected_digest
        elif self.state in {TargetState.CONFLICTING_DIGEST, TargetState.PRESENT}:
            digest = CONFLICT_DIGEST
        return TargetInspection(self.state, digest)

    async def import_image(self, _payload, _target, *, expected_digest):  # type: ignore[no-untyped-def]
        self.import_calls += 1
        if self.fail_import:
            raise SkopeoServiceError(
                "skopeo_digest_mismatch",
                "Synthetic TARGET digest mismatch",
            )
        return ImportResult(
            expected_digest=expected_digest,
            target_digest=expected_digest,
            verified=True,
        )


class FakeHelmService:
    def __init__(self, state: HelmTargetState = HelmTargetState.ABSENT) -> None:
        self.state = state
        self.inspect_calls = 0
        self.push_calls = 0
        self.allow_existing_values: list[bool] = []

    async def inspect_target(self, _chart, *, expected_digest=None):  # type: ignore[no-untyped-def]
        self.inspect_calls += 1
        digest = None
        if self.state is HelmTargetState.SAME_DIGEST:
            digest = expected_digest
        elif self.state in {
            HelmTargetState.CONFLICTING_DIGEST,
            HelmTargetState.PRESENT,
        }:
            digest = CONFLICT_DIGEST
        return HelmTargetInspection(self.state, digest)

    async def push_chart(
        self,
        package: Path,
        target,
        *,
        source_digest=None,
        allow_existing=False,
    ):  # type: ignore[no-untyped-def]
        self.push_calls += 1
        self.allow_existing_values.append(allow_existing)
        digest = source_digest or CHART_DIGEST
        return HelmPushResult(
            package=HelmPackageMetadata(
                path=package,
                name=target.name,
                version=target.version,
                sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
            ),
            target_digest=digest,
            source_digest=source_digest,
            digest_matches_source=(digest == source_digest if source_digest else None),
        )


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


def _build_bundle(tmp_path: Path, private_key: Path, trusted_dir: Path):  # type: ignore[no-untyped-def]
    data = tmp_path / "source" / "data"
    image = data / "export" / "image"
    (image / "blobs" / "sha256").mkdir(parents=True, exist_ok=True)
    (image / "oci-layout").write_text(
        '{"imageLayoutVersion":"1.0.0"}',
        encoding="utf-8",
    )
    (image / "index.json").write_text('{"schemaVersion":2}', encoding="utf-8")
    (image / "blobs" / "sha256" / "abc").write_bytes(b"image-content")
    chart = data / "packages" / "sample-1.2.3.tgz"
    chart.parent.mkdir(parents=True, exist_ok=True)
    chart.write_bytes(b"synthetic-chart-package")

    settings = Settings(
        _env_file=None,
        portal_contour=PortalContour.SOURCE,
        bundle_payload_root=data,
        bundle_temp_root=data / "tmp" / "bundles",
        bundle_outgoing_root=data / "outgoing",
        bundle_extract_root=data / "verified",
        bundle_signing_private_key_file=private_key,
        bundle_trusted_public_keys_dir=trusted_dir,
        bundle_max_archive_bytes=20 * 1024 * 1024,
        bundle_max_extracted_bytes=20 * 1024 * 1024,
        bundle_max_member_count=1000,
        bundle_max_compression_ratio=1000,
    )
    return BundlePackageService(settings).build_bundle(
        source=BundleSource(
            contour="SOURCE",
            harbor="harbor.source.local",
            portal_version="0.1.0",
        ),
        created_by="source-operator",
        delivery_id=DELIVERY_ID,
        created_at=datetime(2026, 9, 11, 9, 0, tzinfo=UTC),
        artifacts=(
            ContainerImagePackageInput(
                repository="project/app",
                reference="1.0.0",
                source_digest=IMAGE_DIGEST,
                source_path=image,
                payload_path="images/project-app",
            ),
            HelmChartPackageInput(
                repository="project/charts",
                name="sample",
                version="1.2.3",
                source_digest=CHART_DIGEST,
                source_path=chart,
                payload_path="charts/sample-1.2.3.tgz",
            ),
        ),
    )


def _target_environment(
    tmp_path: Path,
    trusted_dir: Path,
    *,
    image_state: TargetState = TargetState.ABSENT,
    helm_state: HelmTargetState = HelmTargetState.ABSENT,
    fail_image_import: bool = False,
    max_upload_bytes: int = 20 * 1024 * 1024,
    allow_overwrite: bool = False,
    contour: PortalContour = PortalContour.TARGET,
):  # type: ignore[no-untyped-def]
    data = tmp_path / "target" / "data"
    settings = Settings(
        _env_file=None,
        portal_contour=contour,
        database_url=f"sqlite:///{tmp_path / 'target.db'}",
        operation_workspace_root=data / "tmp" / "operations",
        operation_disk_reserve_bytes=0,
        skopeo_payload_root=data,
        helm_workspace_root=data / "packages",
        helm_temp_root=data / "tmp" / "helm",
        bundle_payload_root=data,
        bundle_temp_root=data / "tmp" / "bundles",
        bundle_outgoing_root=data / "outgoing",
        bundle_extract_root=data / "incoming" / "verified",
        bundle_trusted_public_keys_dir=trusted_dir,
        import_discovery_root=data / "incoming",
        import_staging_root=data / "incoming" / "staged",
        import_receipt_root=data / "receipts" / "imports",
        import_max_upload_bytes=max_upload_bytes,
        import_allow_overwrite=allow_overwrite,
        bundle_max_archive_bytes=20 * 1024 * 1024,
        bundle_max_extracted_bytes=20 * 1024 * 1024,
        bundle_max_member_count=1000,
        bundle_max_compression_ratio=1000,
    )
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    manager = OperationManager(session_factory, settings)
    skopeo = FakeSkopeoService(image_state, fail_import=fail_image_import)
    helm = FakeHelmService(helm_state)
    orchestrator = ImportOrchestrator(
        session_factory,
        settings,
        manager,
        skopeo_factory=lambda _session: skopeo,  # type: ignore[arg-type]
        helm_factory=lambda _session: helm,  # type: ignore[arg-type]
    )
    return settings, manager, skopeo, helm, orchestrator


async def _stream(payload: bytes, chunk_size: int = 17):
    for offset in range(0, len(payload), chunk_size):
        yield payload[offset : offset + chunk_size]
        await asyncio.sleep(0)


async def _upload_and_preview(
    manager: OperationManager,
    orchestrator: ImportOrchestrator,
    payload: bytes,
) -> int:
    await manager.startup()
    started = await orchestrator.accept_upload(
        _stream(payload),
        content_length=len(payload),
        actor_user_id=None,  # type: ignore[arg-type]
        actor_username="target-operator",
    )
    await manager.wait(started.operation_id)
    return started.operation_id


def test_signed_mixed_bundle_imports_and_writes_receipt(tmp_path: Path) -> None:
    private_key, trusted_dir = _write_keys(tmp_path)
    bundle = _build_bundle(tmp_path, private_key, trusted_dir)
    settings, manager, skopeo, helm, orchestrator = _target_environment(
        tmp_path,
        trusted_dir,
    )

    async def scenario() -> int:
        operation_id = await _upload_and_preview(
            manager,
            orchestrator,
            bundle.archive_path.read_bytes(),
        )
        preview = orchestrator.preview(operation_id)
        assert [item.classification for item in preview.artifacts] == [
            ImportPreviewState.NEW,
            ImportPreviewState.NEW,
        ]
        await orchestrator.start_import(
            operation_id,
            actor_username="target-operator",
            overwrite_conflicts=False,
        )
        await manager.wait(operation_id)
        await manager.shutdown()
        return operation_id

    operation_id = asyncio.run(scenario())
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.COMPLETED
    assert [item.status for item in operation.artifacts] == [
        ArtifactStatus.VERIFIED,
        ArtifactStatus.VERIFIED,
    ]
    assert skopeo.import_calls == 1
    assert helm.push_calls == 1

    receipt = orchestrator.receipt(operation_id)
    assert receipt.result == "COMPLETED"
    assert receipt.source_delivery_id == DELIVERY_ID
    assert receipt.bundle_sha256 == bundle.archive_sha256
    receipt_path = settings.import_receipt_root / f"import-{operation_id}.json"
    text = receipt_path.read_text(encoding="utf-8")
    assert "password" not in text.lower()
    assert "secret" not in text.lower()


def test_import_start_rejects_signer_disabled_after_verified_preview(
    tmp_path: Path,
) -> None:
    private_key, trusted_dir = _write_keys(tmp_path)
    bundle = _build_bundle(tmp_path, private_key, trusted_dir)
    settings, manager, skopeo, helm, orchestrator = _target_environment(
        tmp_path,
        trusted_dir,
    )

    async def scenario() -> int:
        operation_id = await _upload_and_preview(
            manager,
            orchestrator,
            bundle.archive_path.read_bytes(),
        )
        preview = orchestrator.preview(operation_id)
        mutation = KeyManagementService(settings).set_trusted_key_enabled(
            preview.signing_key_fingerprint,
            False,
        )
        assert mutation.action == "disabled"

        with pytest.raises(ImportOrchestrationError) as blocked:
            await orchestrator.start_import(
                operation_id,
                actor_username="target-operator",
                overwrite_conflicts=False,
            )
        assert blocked.value.code == "import_signing_key_untrusted"
        assert skopeo.import_calls == 0
        assert helm.push_calls == 0
        await manager.shutdown()
        return operation_id

    operation_id = asyncio.run(scenario())
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.READY


def test_same_artifacts_are_skipped_idempotently(tmp_path: Path) -> None:
    private_key, trusted_dir = _write_keys(tmp_path)
    bundle = _build_bundle(tmp_path, private_key, trusted_dir)
    _settings, manager, skopeo, helm, orchestrator = _target_environment(
        tmp_path,
        trusted_dir,
        image_state=TargetState.SAME_DIGEST,
        helm_state=HelmTargetState.SAME_DIGEST,
    )

    async def scenario() -> int:
        operation_id = await _upload_and_preview(
            manager,
            orchestrator,
            bundle.archive_path.read_bytes(),
        )
        await orchestrator.start_import(
            operation_id,
            actor_username="target-operator",
            overwrite_conflicts=False,
        )
        await manager.wait(operation_id)
        await manager.shutdown()
        return operation_id

    operation_id = asyncio.run(scenario())
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.COMPLETED
    assert all(item.status is ArtifactStatus.SKIPPED for item in operation.artifacts)
    assert skopeo.import_calls == 0
    assert helm.push_calls == 0


def test_conflict_is_blocked_and_overwrite_requires_server_policy(tmp_path: Path) -> None:
    private_key, trusted_dir = _write_keys(tmp_path)
    bundle = _build_bundle(tmp_path, private_key, trusted_dir)
    _settings, manager, _skopeo, _helm, orchestrator = _target_environment(
        tmp_path,
        trusted_dir,
        image_state=TargetState.CONFLICTING_DIGEST,
    )

    async def scenario() -> None:
        operation_id = await _upload_and_preview(
            manager,
            orchestrator,
            bundle.archive_path.read_bytes(),
        )
        with pytest.raises(ImportOrchestrationError) as blocked:
            await orchestrator.start_import(
                operation_id,
                actor_username="target-operator",
                overwrite_conflicts=False,
            )
        assert blocked.value.code == "import_conflict_blocked"
        with pytest.raises(ImportOrchestrationError) as disabled:
            await orchestrator.start_import(
                operation_id,
                actor_username="target-operator",
                overwrite_conflicts=True,
            )
        assert disabled.value.code == "import_overwrite_disabled"
        await manager.shutdown()

    asyncio.run(scenario())


def test_explicit_overwrite_reaches_image_and_helm_adapters(tmp_path: Path) -> None:
    private_key, trusted_dir = _write_keys(tmp_path)
    bundle = _build_bundle(tmp_path, private_key, trusted_dir)
    _settings, manager, skopeo, helm, orchestrator = _target_environment(
        tmp_path,
        trusted_dir,
        image_state=TargetState.CONFLICTING_DIGEST,
        helm_state=HelmTargetState.CONFLICTING_DIGEST,
        allow_overwrite=True,
    )

    async def scenario() -> int:
        operation_id = await _upload_and_preview(
            manager,
            orchestrator,
            bundle.archive_path.read_bytes(),
        )
        await orchestrator.start_import(
            operation_id,
            actor_username="target-admin",
            overwrite_conflicts=True,
        )
        await manager.wait(operation_id)
        await manager.shutdown()
        return operation_id

    operation_id = asyncio.run(scenario())
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.COMPLETED
    assert skopeo.import_calls == 1
    assert helm.push_calls == 1
    assert helm.allow_existing_values == [True]


def test_invalid_bundle_is_rejected_before_target_inspection(tmp_path: Path) -> None:
    _private_key, trusted_dir = _write_keys(tmp_path)
    settings, manager, skopeo, helm, orchestrator = _target_environment(
        tmp_path,
        trusted_dir,
    )

    async def scenario() -> int:
        operation_id = await _upload_and_preview(manager, orchestrator, b"not-a-valid-bundle")
        await manager.shutdown()
        return operation_id

    operation_id = asyncio.run(scenario())
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.REJECTED
    assert skopeo.inspect_calls == 0
    assert helm.inspect_calls == 0
    assert not any(settings.import_staging_root.glob("*"))


def test_streaming_upload_limit_cleans_partial_file(tmp_path: Path) -> None:
    _private_key, trusted_dir = _write_keys(tmp_path)
    settings, _manager, _skopeo, _helm, orchestrator = _target_environment(
        tmp_path,
        trusted_dir,
        max_upload_bytes=5,
    )

    async def scenario() -> None:
        with pytest.raises(ImportOrchestrationError) as exc_info:
            await orchestrator.accept_upload(
                _stream(b"123456", chunk_size=2),
                content_length=None,
                actor_user_id=None,  # type: ignore[arg-type]
                actor_username="target-operator",
            )
        assert exc_info.value.code == "import_upload_too_large"

    asyncio.run(scenario())
    assert not settings.import_staging_root.exists() or not any(
        settings.import_staging_root.iterdir()
    )


def test_incoming_discovery_requires_readiness_sidecar_and_handoff(tmp_path: Path) -> None:
    private_key, trusted_dir = _write_keys(tmp_path)
    bundle = _build_bundle(tmp_path, private_key, trusted_dir)
    settings, manager, _skopeo, _helm, orchestrator = _target_environment(
        tmp_path,
        trusted_dir,
    )
    settings.import_discovery_root.mkdir(parents=True, exist_ok=True)
    incoming_archive = settings.import_discovery_root / bundle.archive_path.name
    incoming_sidecar = incoming_archive.with_name(incoming_archive.name + ".sha256")
    incoming_handoff = settings.import_discovery_root / bundle.handoff_path.name
    shutil.copyfile(bundle.archive_path, incoming_archive)

    async def scenario() -> int:
        await manager.startup()
        not_ready = await orchestrator.discover_ready(
            actor_user_id=None,  # type: ignore[arg-type]
            actor_username="target-operator",
        )
        assert not_ready == ()
        shutil.copyfile(bundle.sidecar_path, incoming_sidecar)
        sidecar_only = await orchestrator.discover_ready(
            actor_user_id=None,  # type: ignore[arg-type]
            actor_username="target-operator",
        )
        assert sidecar_only == ()
        shutil.copyfile(bundle.handoff_path, incoming_handoff)
        ready = await orchestrator.discover_ready(
            actor_user_id=None,  # type: ignore[arg-type]
            actor_username="target-operator",
        )
        assert len(ready) == 1
        await manager.wait(ready[0].operation_id)
        await manager.shutdown()
        return ready[0].operation_id

    operation_id = asyncio.run(scenario())
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.READY
    assert not incoming_archive.exists()
    assert not incoming_sidecar.exists()
    assert not incoming_handoff.exists()


def test_bundle_change_after_preview_fails_before_import(tmp_path: Path) -> None:
    private_key, trusted_dir = _write_keys(tmp_path)
    bundle = _build_bundle(tmp_path, private_key, trusted_dir)
    settings, manager, skopeo, helm, orchestrator = _target_environment(
        tmp_path,
        trusted_dir,
    )

    async def scenario() -> int:
        operation_id = await _upload_and_preview(
            manager,
            orchestrator,
            bundle.archive_path.read_bytes(),
        )
        operation = manager.get_operation(operation_id)
        assert operation is not None and operation.import_storage_key is not None
        archive = (
            settings.import_staging_root
            / operation.import_storage_key
            / "bundle.htp.tar.gz"
        )
        with archive.open("ab") as handle:
            handle.write(b"tampered-after-preview")
        await orchestrator.start_import(
            operation_id,
            actor_username="target-operator",
            overwrite_conflicts=False,
        )
        await manager.wait(operation_id)
        await manager.shutdown()
        return operation_id

    operation_id = asyncio.run(scenario())
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.FAILED
    assert operation.error_code == "import_bundle_changed"
    assert skopeo.import_calls == 0
    assert helm.push_calls == 0


def test_post_import_digest_mismatch_fails_operation_and_receipt(tmp_path: Path) -> None:
    private_key, trusted_dir = _write_keys(tmp_path)
    bundle = _build_bundle(tmp_path, private_key, trusted_dir)
    _settings, manager, _skopeo, _helm, orchestrator = _target_environment(
        tmp_path,
        trusted_dir,
        fail_image_import=True,
    )

    async def scenario() -> int:
        operation_id = await _upload_and_preview(
            manager,
            orchestrator,
            bundle.archive_path.read_bytes(),
        )
        await orchestrator.start_import(
            operation_id,
            actor_username="target-operator",
            overwrite_conflicts=False,
        )
        await manager.wait(operation_id)
        await manager.shutdown()
        return operation_id

    operation_id = asyncio.run(scenario())
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.FAILED
    assert operation.error_code == "import_partial_failure"
    assert operation.artifacts[0].status is ArtifactStatus.FAILED
    receipt = orchestrator.receipt(operation_id)
    assert receipt.result == "FAILED"
    assert receipt.artifacts[0].error_code == "skopeo_digest_mismatch"


def test_source_contour_rejects_import_intake(tmp_path: Path) -> None:
    _private_key, trusted_dir = _write_keys(tmp_path)
    _settings, _manager, _skopeo, _helm, orchestrator = _target_environment(
        tmp_path,
        trusted_dir,
        contour=PortalContour.SOURCE,
    )

    async def scenario() -> None:
        with pytest.raises(ImportOrchestrationError) as exc_info:
            await orchestrator.accept_upload(
                _stream(b"bundle"),
                content_length=6,
                actor_user_id=None,  # type: ignore[arg-type]
                actor_username="operator",
            )
        assert exc_info.value.code == "import_wrong_contour"

    asyncio.run(scenario())


def test_browser_physical_handoff_upload_verifies_three_file_delivery(tmp_path: Path) -> None:
    private_key, trusted_dir = _write_keys(tmp_path)
    bundle = _build_bundle(tmp_path, private_key, trusted_dir)
    _settings, manager, _skopeo, _helm, orchestrator = _target_environment(
        tmp_path,
        trusted_dir,
    )

    async def scenario() -> int:
        await manager.startup()
        started = await orchestrator.accept_upload(
            _stream(bundle.archive_path.read_bytes()),
            content_length=bundle.archive_size,
            actor_user_id=None,  # type: ignore[arg-type]
            actor_username="target-operator",
            bundle_filename=bundle.archive_path.name,
            sidecar_payload=bundle.sidecar_path.read_bytes(),
            handoff_payload=bundle.handoff_path.read_bytes(),
        )
        await manager.wait(started.operation_id)
        await manager.shutdown()
        return started.operation_id

    operation_id = asyncio.run(scenario())
    operation = manager.get_operation(operation_id)
    assert operation is not None
    assert operation.status is OperationStatus.READY
    assert operation.bundle_filename == bundle.archive_path.name
    assert operation.bundle_sha256 == bundle.archive_sha256


def test_browser_physical_handoff_rejects_tampered_sidecar_before_operation(
    tmp_path: Path,
) -> None:
    private_key, trusted_dir = _write_keys(tmp_path)
    bundle = _build_bundle(tmp_path, private_key, trusted_dir)
    settings, manager, _skopeo, _helm, orchestrator = _target_environment(
        tmp_path,
        trusted_dir,
    )

    async def scenario() -> None:
        await manager.startup()
        with pytest.raises(ImportOrchestrationError) as exc_info:
            await orchestrator.accept_upload(
                _stream(bundle.archive_path.read_bytes()),
                content_length=bundle.archive_size,
                actor_user_id=None,  # type: ignore[arg-type]
                actor_username="target-operator",
                bundle_filename=bundle.archive_path.name,
                sidecar_payload=b"0" * 64 + b"  wrong.htp.tar.gz\n",
                handoff_payload=bundle.handoff_path.read_bytes(),
            )
        assert exc_info.value.code == "handoff_file_mismatch"
        await manager.shutdown()

    asyncio.run(scenario())
    with orchestrator.session_factory() as session:
        assert session.query(Operation).count() == 0
    assert not settings.import_staging_root.exists() or not any(
        settings.import_staging_root.iterdir()
    )
