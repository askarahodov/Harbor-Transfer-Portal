from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pypdf import PdfReader

from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.base import Base
from app.db.models import ArtifactResult, Operation, UserRole
from app.db.repositories import UserRepository
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import (
    ArtifactStatus,
    BundleManifest,
    BundleSource,
    ContainerImageArtifact,
    HelmChartArtifact,
    OperationStatus,
    OperationType,
)
from app.domain.imports import ImportPreviewState
from app.schemas.imports import (
    ImportArtifactPreviewResponse,
    ImportDestinationPlanRequest,
    ImportPreviewResponse,
)
from app.services.artifact_mapping_snapshot import persist_artifact_mapping_snapshot
from app.services.bundle_package_service import BundleVerificationResult
from app.services.destination_mapping_policy import DestinationMappingPolicyService
from app.services.harbor_destination_validator import DestinationCapability
from app.services.helm_oci_service import (
    HelmPackageMetadata,
    HelmPushResult,
    HelmTargetInspection,
    HelmTargetState,
)
from app.services.import_orchestrator import ImportOrchestrationError
from app.services.key_management import KeyManagementService
from app.services.import_retry import ImportRetryService, retry_of_operation_id
from app.services.operation_manager import OperationManager
from app.services.policy_aware_destination_plan import PolicyAwareImportDestinationPlanOrchestrator
from app.services.report_service import build_operation_pdf, iter_operation_csv
from app.services.skopeo_service import ImportResult, TargetInspection, TargetState

IMAGE_DIGEST = "sha256:" + "a" * 64
CHART_DIGEST = "sha256:" + "b" * 64
BUNDLE_BYTES = b"persisted deterministic retry bundle"
BUNDLE_SHA = hashlib.sha256(BUNDLE_BYTES).hexdigest()
STORAGE_KEY = "d" * 48
DELIVERY_ID = "DELIVERY-20260915-RETRY01"


class FakeDestinationValidator:
    registry_host = "harbor.target.local"

    async def validate(self, _project: str, _repository: str) -> DestinationCapability:
        return DestinationCapability(True, True)


class FakeSkopeoService:
    def __init__(self) -> None:
        self.state = TargetState.ABSENT
        self.mutations = 0

    async def inspect_target(self, _image, *, expected_digest=None):  # type: ignore[no-untyped-def]
        digest = expected_digest if self.state is TargetState.SAME_DIGEST else None
        if self.state is TargetState.CONFLICTING_DIGEST:
            digest = "sha256:" + "e" * 64
        return TargetInspection(self.state, digest)

    async def import_image(self, _payload, _target, *, expected_digest):  # type: ignore[no-untyped-def]
        self.mutations += 1
        return ImportResult(
            expected_digest=expected_digest,
            target_digest=expected_digest,
            verified=True,
        )


class FakeHelmService:
    def __init__(self) -> None:
        self.state = HelmTargetState.ABSENT
        self.mutations = 0

    async def inspect_target(self, _chart, *, expected_digest=None):  # type: ignore[no-untyped-def]
        digest = expected_digest if self.state is HelmTargetState.SAME_DIGEST else None
        if self.state is HelmTargetState.CONFLICTING_DIGEST:
            digest = "sha256:" + "f" * 64
        return HelmTargetInspection(self.state, digest)

    async def push_chart(
        self,
        package: Path,
        target,
        *,
        source_digest=None,
        allow_existing=False,
    ):  # type: ignore[no-untyped-def]
        del allow_existing
        self.mutations += 1
        package_sha = hashlib.sha256(package.read_bytes()).hexdigest()
        digest = source_digest or CHART_DIGEST
        return HelmPushResult(
            package=HelmPackageMetadata(
                path=package,
                name=target.name,
                version=target.version,
                sha256=package_sha,
            ),
            target_digest=digest,
            source_digest=source_digest,
            digest_matches_source=(digest == source_digest if source_digest else None),
        )


class FakePackageService:
    def __init__(self, signer_fingerprint: str) -> None:
        self.signer_fingerprint = signer_fingerprint
        self.chart_bytes = b"retry-chart-package"
        self.chart_sha = hashlib.sha256(self.chart_bytes).hexdigest()
        self.manifest = BundleManifest(
            schema_version="1.0",
            delivery_id=DELIVERY_ID,
            created_at=datetime(2026, 9, 15, 9, 0, tzinfo=UTC),
            created_by="source-operator",
            source=BundleSource(
                contour="SOURCE",
                harbor="harbor.source.local",
                portal_version="1.0.0",
            ),
            artifacts=[
                ContainerImageArtifact(
                    repository="source-team/app/api",
                    reference="1.4.2",
                    source_digest=IMAGE_DIGEST,
                    payload_path="images/api",
                    payload_sha256=hashlib.sha256(b"image-layout").hexdigest(),
                    payload_size=len(b"image-layout"),
                ),
                HelmChartArtifact(
                    repository="source-charts/platform",
                    name="mis",
                    version="4.88.6",
                    source_digest=CHART_DIGEST,
                    payload_path="charts/mis-4.88.6.tgz",
                    payload_sha256=self.chart_sha,
                    payload_size=len(self.chart_bytes),
                ),
            ],
        )

    def verify_bundle(self, *_args, extract_root=None, **_kwargs):  # type: ignore[no-untyped-def]
        root = Path(extract_root) if extract_root is not None else Path.cwd() / ".retry-extract"
        image_root = root / "images" / "api"
        image_root.mkdir(parents=True, exist_ok=True)
        (image_root / "layout.bin").write_bytes(b"image-layout")
        chart = root / "charts" / "mis-4.88.6.tgz"
        chart.parent.mkdir(parents=True, exist_ok=True)
        chart.write_bytes(self.chart_bytes)
        return BundleVerificationResult(
            manifest=self.manifest,
            archive_sha256=BUNDLE_SHA,
            archive_size=len(BUNDLE_BYTES),
            signing_key_fingerprint=self.signer_fingerprint,
            extracted_root=root,
        )


def _settings(tmp_path: Path, database_url: str) -> Settings:
    data = tmp_path / "data"
    return Settings(
        _env_file=None,
        database_url=database_url,
        jwt_secret="retry-acceptance-secret-" + "x" * 32,
        portal_contour=PortalContour.TARGET,
        operation_workspace_root=data / "tmp" / "operations",
        bundle_payload_root=data,
        bundle_temp_root=data / "tmp" / "bundles",
        bundle_outgoing_root=data / "outgoing",
        bundle_extract_root=data / "incoming" / "verified",
        import_discovery_root=data / "incoming",
        import_staging_root=data / "incoming" / "staged",
        import_receipt_root=data / "receipts" / "imports",
        operation_disk_reserve_bytes=0,
        bundle_trusted_public_keys_dir=data / "keys" / "trusted-source",
    )


def _preview(operation_id: int, signer_fingerprint: str) -> ImportPreviewResponse:
    return ImportPreviewResponse(
        operation_id=operation_id,
        status=OperationStatus.READY,
        source_delivery_id=DELIVERY_ID,
        bundle_sha256=BUNDLE_SHA,
        bundle_size_bytes=len(BUNDLE_BYTES),
        signing_key_fingerprint=signer_fingerprint,
        verified_at=datetime(2026, 9, 15, 10, 0, tzinfo=UTC),
        overwrite_allowed=True,
        artifacts=[
            ImportArtifactPreviewResponse(
                index=0,
                artifact_type="container-image",
                repository="source-team/app/api",
                reference="1.4.2",
                expected_digest=IMAGE_DIGEST,
                payload_size=len(b"image-layout"),
                classification=ImportPreviewState.NEW,
            ),
            ImportArtifactPreviewResponse(
                index=1,
                artifact_type="helm-chart",
                repository="source-charts/platform",
                name="mis",
                version="4.88.6",
                expected_digest=CHART_DIGEST,
                payload_size=len(b"retry-chart-package"),
                classification=ImportPreviewState.NEW,
            ),
        ],
    )


def _environment(tmp_path: Path):  # type: ignore[no-untyped-def]
    database_url = f"sqlite:///{tmp_path / 'retry-acceptance.db'}"
    settings = _settings(tmp_path, database_url)
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    manager = OperationManager(session_factory, settings)
    signer = Ed25519PrivateKey.generate()
    signer_pem = signer.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    signer_fingerprint = KeyManagementService(settings).add_trusted_public_key(
        signer_pem
    ).fingerprint
    skopeo = FakeSkopeoService()
    helm = FakeHelmService()
    package_service = FakePackageService(signer_fingerprint)

    def package_factory() -> FakePackageService:
        return package_service

    orchestrator = PolicyAwareImportDestinationPlanOrchestrator(
        session_factory,
        settings,
        manager,
        package_factory=package_factory,
        skopeo_factory=lambda _session: skopeo,  # type: ignore[arg-type]
        helm_factory=lambda _session: helm,  # type: ignore[arg-type]
        destination_validator_factory=lambda _session: FakeDestinationValidator(),
    )

    with session_factory() as session:
        actor = UserRepository(session).create(
            username="operator",
            password_hash=hash_password("operator-password-123"),
            role=UserRole.OPERATOR,
        )
        session.commit()
        actor_user_id = actor.id

        DestinationMappingPolicyService(session).update(
            {
                "container_image_project": "docker-old",
                "helm_chart_project": "helm-old",
            }
        )
        session.commit()
        operation = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.READY,
            actor_user_id=actor_user_id,
            actor_username=actor.username,
            bundle_filename="bundle.htp.tar.gz",
            bundle_sha256=BUNDLE_SHA,
            bundle_size_bytes=len(BUNDLE_BYTES),
            import_storage_key=STORAGE_KEY,
            import_intake_mode="upload",
            source_delivery_id=DELIVERY_ID,
            bundle_signing_key_fingerprint=signer_fingerprint,
            total_artifacts=2,
            progress_total=2,
        )
        session.add(operation)
        session.flush()
        operation.import_preview_json = _preview(
            operation.id,
            signer_fingerprint,
        ).model_dump_json()
        session.add_all(
            [
                ArtifactResult(
                    operation_id=operation.id,
                    artifact_type="container-image",
                    repository="source-team/app/api",
                    reference="1.4.2",
                    source_digest=IMAGE_DIGEST,
                    size_bytes=len(b"image-layout"),
                    status=ArtifactStatus.PENDING,
                ),
                ArtifactResult(
                    operation_id=operation.id,
                    artifact_type="helm-chart",
                    repository="source-charts/platform",
                    name="mis",
                    version="4.88.6",
                    source_digest=CHART_DIGEST,
                    size_bytes=len(b"retry-chart-package"),
                    status=ArtifactStatus.PENDING,
                ),
            ]
        )
        session.commit()
        operation_id = operation.id

    staging = settings.import_staging_root / STORAGE_KEY
    staging.mkdir(parents=True)
    (staging / "bundle.htp.tar.gz").write_bytes(BUNDLE_BYTES)

    plan = asyncio.run(
        orchestrator.build_destination_plan(operation_id, ImportDestinationPlanRequest())
    )
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        persist_artifact_mapping_snapshot(
            session,
            operation,
            plan,
            overwrite_approved=False,
        )
        rows = sorted(operation.artifacts, key=lambda item: item.id)
        rows[0].status = ArtifactStatus.VERIFIED
        rows[0].target_digest = IMAGE_DIGEST
        rows[1].status = ArtifactStatus.FAILED
        rows[1].error_code = "temporary_helm_failure"
        operation.successful_artifacts = 1
        operation.failed_artifacts = 1
        operation.progress_current = 2
        operation.status = OperationStatus.FAILED
        operation.error_code = "import_partial_failure"
        operation.error_message = "rollback не выполнялся"
        session.commit()

        DestinationMappingPolicyService(session).update(
            {
                "container_image_project": "docker-new",
                "helm_chart_project": "helm-new",
            }
        )
        session.commit()

    return (
        settings,
        session_factory,
        skopeo,
        helm,
        package_factory,
        orchestrator,
        operation_id,
        plan,
        actor_user_id,
    )


def _prepare_retry(
    tmp_path: Path,
    *,
    helm_state: HelmTargetState,
    image_state: TargetState = TargetState.SAME_DIGEST,
):  # type: ignore[no-untyped-def]
    (
        settings,
        session_factory,
        skopeo,
        helm,
        package_factory,
        orchestrator,
        operation_id,
        plan,
        actor_user_id,
    ) = _environment(tmp_path)
    skopeo.state = image_state
    helm.state = helm_state
    prepared = asyncio.run(
        ImportRetryService(orchestrator).prepare_retry(
            operation_id,
            actor_user_id=actor_user_id,
            actor_username="operator",
            destination_plan_id=plan.plan_id,
        )
    )
    return (
        settings,
        session_factory,
        skopeo,
        helm,
        package_factory,
        orchestrator,
        operation_id,
        plan,
        prepared,
    )


def test_successful_artifact_target_drift_is_default_deny_and_mapping_cannot_be_replaced(
    tmp_path: Path,
) -> None:
    (
        _settings_value,
        _session_factory,
        skopeo,
        helm,
        _package_factory,
        orchestrator,
        operation_id,
        _plan,
        prepared,
    ) = _prepare_retry(
        tmp_path,
        helm_state=HelmTargetState.ABSENT,
        image_state=TargetState.CONFLICTING_DIGEST,
    )

    assert [item.classification for item in prepared.destination_plan.artifacts] == [
        ImportPreviewState.CONFLICT,
        ImportPreviewState.NEW,
    ]
    with pytest.raises(ImportOrchestrationError) as conflict:
        asyncio.run(
            orchestrator.start_import(
                prepared.operation_id,
                actor_username="operator",
                overwrite_conflicts=False,
                destination_plan_id=prepared.destination_plan.plan_id,
            )
        )
    assert conflict.value.code == "import_conflict_blocked"

    with pytest.raises(ImportOrchestrationError) as remap:
        asyncio.run(
            orchestrator.build_destination_plan(
                prepared.operation_id,
                ImportDestinationPlanRequest(
                    container_image_project="docker-other",
                    helm_chart_project="helm-other",
                ),
                actor_username="operator",
            )
        )
    assert remap.value.code == "import_retry_mapping_immutable"
    assert skopeo.mutations == 0
    assert helm.mutations == 0

    with _session_factory() as session:
        original = session.get(Operation, operation_id)
        retry = session.get(Operation, prepared.operation_id)
        assert original is not None and retry is not None
        assert original.status is OperationStatus.FAILED
        assert retry_of_operation_id(retry) == operation_id


def test_retry_survives_restart_skips_same_and_retries_failed_artifact(tmp_path: Path) -> None:
    (
        settings,
        session_factory,
        skopeo,
        helm,
        package_factory,
        _orchestrator,
        operation_id,
        original_plan,
        prepared,
    ) = _prepare_retry(tmp_path, helm_state=HelmTargetState.ABSENT)

    restarted_manager = OperationManager(session_factory, settings)
    restarted = PolicyAwareImportDestinationPlanOrchestrator(
        session_factory,
        settings,
        restarted_manager,
        package_factory=package_factory,
        skopeo_factory=lambda _session: skopeo,  # type: ignore[arg-type]
        helm_factory=lambda _session: helm,  # type: ignore[arg-type]
        destination_validator_factory=lambda _session: FakeDestinationValidator(),
    )

    async def scenario() -> None:
        await restarted_manager.startup()
        await restarted.start_import(
            prepared.operation_id,
            actor_username="operator",
            overwrite_conflicts=False,
            destination_plan_id=prepared.destination_plan.plan_id,
        )
        await restarted_manager.wait(prepared.operation_id)
        await restarted_manager.shutdown()

    asyncio.run(scenario())

    with session_factory() as session:
        original = session.get(Operation, operation_id)
        retry = session.get(Operation, prepared.operation_id)
        assert original is not None and retry is not None
        assert original.status is OperationStatus.FAILED
        assert [row.status for row in sorted(original.artifacts, key=lambda item: item.id)] == [
            ArtifactStatus.VERIFIED,
            ArtifactStatus.FAILED,
        ]
        assert retry.status is OperationStatus.COMPLETED
        assert [row.status for row in sorted(retry.artifacts, key=lambda item: item.id)] == [
            ArtifactStatus.SKIPPED,
            ArtifactStatus.VERIFIED,
        ]
        assert retry_of_operation_id(retry) == operation_id
        assert all(row.destination_plan_id == original_plan.plan_id for row in retry.artifacts)

        csv_text = b"".join(iter_operation_csv(retry)).decode("utf-8")
        assert f"Retry of import #{operation_id}" in csv_text
        pdf = build_operation_pdf(retry, PortalContour.TARGET)
        try:
            pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
        finally:
            pdf.close()
        assert f"Retry of import #{operation_id}" in pdf_text

    assert skopeo.mutations == 0
    assert helm.mutations == 1

    receipt = restarted.receipt(prepared.operation_id)
    assert receipt.retry_of_operation_id == operation_id
    assert receipt.failure_policy == "continue-on-error"
    assert receipt.destination_plan_id == original_plan.plan_id
    assert [item.status for item in receipt.artifacts] == [
        ArtifactStatus.SKIPPED,
        ArtifactStatus.VERIFIED,
    ]
    receipt_path = settings.import_receipt_root / f"import-{prepared.operation_id}.json"
    receipt_text = receipt_path.read_text(encoding="utf-8")
    assert f'"retry_of_operation_id": {operation_id}' in receipt_text
    assert '"failure_policy": "continue-on-error"' in receipt_text


def test_failed_artifact_that_appeared_with_same_content_is_safely_skipped(tmp_path: Path) -> None:
    (
        settings,
        session_factory,
        skopeo,
        helm,
        package_factory,
        _orchestrator,
        _operation_id,
        _original_plan,
        prepared,
    ) = _prepare_retry(tmp_path, helm_state=HelmTargetState.SAME_DIGEST)

    manager = OperationManager(session_factory, settings)
    restarted = PolicyAwareImportDestinationPlanOrchestrator(
        session_factory,
        settings,
        manager,
        package_factory=package_factory,
        skopeo_factory=lambda _session: skopeo,  # type: ignore[arg-type]
        helm_factory=lambda _session: helm,  # type: ignore[arg-type]
        destination_validator_factory=lambda _session: FakeDestinationValidator(),
    )

    async def scenario() -> None:
        await manager.startup()
        await restarted.start_import(
            prepared.operation_id,
            actor_username="operator",
            overwrite_conflicts=False,
            destination_plan_id=prepared.destination_plan.plan_id,
        )
        await manager.wait(prepared.operation_id)
        await manager.shutdown()

    asyncio.run(scenario())

    with session_factory() as session:
        retry = session.get(Operation, prepared.operation_id)
        assert retry is not None
        assert retry.status is OperationStatus.COMPLETED
        assert all(row.status is ArtifactStatus.SKIPPED for row in retry.artifacts)
    assert skopeo.mutations == 0
    assert helm.mutations == 0
