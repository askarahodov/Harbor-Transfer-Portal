from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.api.operations import _serialize_operation, _serialize_summary
from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.base import Base
from app.db.models import ArtifactResult, Operation, UserRole
from app.db.repositories import UserRepository
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType
from app.domain.imports import ImportPreviewState
from app.schemas.imports import (
    ImportArtifactPreviewResponse,
    ImportDestinationPlanRequest,
    ImportPreviewResponse,
)
from app.services.artifact_mapping_snapshot import persist_artifact_mapping_snapshot
from app.services.destination_mapping_policy import DestinationMappingPolicyService
from app.services.harbor_destination_validator import DestinationCapability
from app.services.helm_oci_service import HelmTargetInspection, HelmTargetState
from app.services.import_orchestrator import ImportOrchestrationError
from app.services.import_retry import ImportRetryService, retry_of_operation_id
from app.services.operation_manager import OperationManager
from app.services.policy_aware_destination_plan import PolicyAwareImportDestinationPlanOrchestrator
from app.services.skopeo_service import TargetInspection, TargetState

IMAGE_DIGEST = "sha256:" + "a" * 64
CHART_DIGEST = "sha256:" + "b" * 64
BUNDLE_SHA = "c" * 64
STORAGE_KEY = "d" * 48


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

    async def import_image(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        self.mutations += 1
        raise AssertionError("retry preparation must not mutate Harbor")


class FakeHelmService:
    def __init__(self) -> None:
        self.state = HelmTargetState.ABSENT
        self.mutations = 0

    async def inspect_target(self, _chart, *, expected_digest=None):  # type: ignore[no-untyped-def]
        digest = expected_digest if self.state is HelmTargetState.SAME_DIGEST else None
        if self.state is HelmTargetState.CONFLICTING_DIGEST:
            digest = "sha256:" + "f" * 64
        return HelmTargetInspection(self.state, digest)

    async def push_chart(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        self.mutations += 1
        raise AssertionError("retry preparation must not mutate Harbor")


def _settings(tmp_path: Path, database_url: str) -> Settings:
    data = tmp_path / "data"
    return Settings(
        _env_file=None,
        database_url=database_url,
        jwt_secret="retry-test-secret-" + "x" * 32,
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
    )


def _preview(operation_id: int) -> ImportPreviewResponse:
    return ImportPreviewResponse(
        operation_id=operation_id,
        status=OperationStatus.READY,
        source_delivery_id="DELIVERY-RETRY-001",
        bundle_sha256=BUNDLE_SHA,
        bundle_size_bytes=4096,
        signing_key_fingerprint="1" * 64,
        verified_at=datetime(2026, 9, 15, 10, 0, tzinfo=UTC),
        overwrite_allowed=True,
        artifacts=[
            ImportArtifactPreviewResponse(
                index=0,
                artifact_type="container-image",
                repository="source-team/app/api",
                reference="1.4.2",
                expected_digest=IMAGE_DIGEST,
                payload_size=1024,
                classification=ImportPreviewState.NEW,
            ),
            ImportArtifactPreviewResponse(
                index=1,
                artifact_type="helm-chart",
                repository="source-charts/platform",
                name="mis",
                version="4.88.6",
                expected_digest=CHART_DIGEST,
                payload_size=2048,
                classification=ImportPreviewState.NEW,
            ),
        ],
    )


def _environment(tmp_path: Path):  # type: ignore[no-untyped-def]
    database_url = f"sqlite:///{tmp_path / 'retry.db'}"
    settings = _settings(tmp_path, database_url)
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    manager = OperationManager(session_factory, settings)
    skopeo = FakeSkopeoService()
    helm = FakeHelmService()
    orchestrator = PolicyAwareImportDestinationPlanOrchestrator(
        session_factory,
        settings,
        manager,
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
            bundle_size_bytes=4096,
            import_storage_key=STORAGE_KEY,
            import_intake_mode="upload",
            source_delivery_id="DELIVERY-RETRY-001",
            bundle_signing_key_fingerprint="1" * 64,
            total_artifacts=2,
            progress_total=2,
        )
        session.add(operation)
        session.flush()
        operation.import_preview_json = _preview(operation.id).model_dump_json()
        session.add_all(
            [
                ArtifactResult(
                    operation_id=operation.id,
                    artifact_type="container-image",
                    repository="source-team/app/api",
                    reference="1.4.2",
                    source_digest=IMAGE_DIGEST,
                    size_bytes=1024,
                    status=ArtifactStatus.PENDING,
                ),
                ArtifactResult(
                    operation_id=operation.id,
                    artifact_type="helm-chart",
                    repository="source-charts/platform",
                    name="mis",
                    version="4.88.6",
                    source_digest=CHART_DIGEST,
                    size_bytes=2048,
                    status=ArtifactStatus.PENDING,
                ),
            ]
        )
        session.commit()
        operation_id = operation.id

    staging = settings.import_staging_root / STORAGE_KEY
    staging.mkdir(parents=True)
    (staging / "bundle.htp.tar.gz").write_bytes(b"persisted-retry-bundle")

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

    return session_factory, orchestrator, skopeo, helm, operation_id, plan, actor_user_id


def test_retry_reuses_frozen_mapping_and_revalidates_current_target(tmp_path: Path) -> None:
    (
        session_factory,
        orchestrator,
        skopeo,
        helm,
        operation_id,
        original_plan,
        actor_user_id,
    ) = _environment(tmp_path)
    skopeo.state = TargetState.SAME_DIGEST
    helm.state = HelmTargetState.CONFLICTING_DIGEST

    prepared = asyncio.run(
        ImportRetryService(orchestrator).prepare_retry(
            operation_id,
            actor_user_id=actor_user_id,
            actor_username="operator",
            destination_plan_id=original_plan.plan_id,
        )
    )

    assert prepared.operation_id != operation_id
    assert prepared.retry_of_operation_id == operation_id
    assert prepared.failure_policy == "continue-on-error"
    assert prepared.destination_plan.plan_id == original_plan.plan_id
    assert [item.target_project for item in prepared.destination_plan.artifacts] == [
        "docker-old",
        "helm-old",
    ]
    assert [item.classification for item in prepared.destination_plan.artifacts] == [
        ImportPreviewState.SAME,
        ImportPreviewState.CONFLICT,
    ]
    assert skopeo.mutations == 0
    assert helm.mutations == 0

    with session_factory() as session:
        original = session.get(Operation, operation_id)
        retry = session.get(Operation, prepared.operation_id)
        assert original is not None and retry is not None
        assert original.status is OperationStatus.FAILED
        assert original.error_code == "import_partial_failure"
        assert retry.status is OperationStatus.READY
        assert retry.actor_user_id == actor_user_id
        assert retry.actor_username == "operator"
        assert retry.import_storage_key == STORAGE_KEY
        assert retry.bundle_sha256 == BUNDLE_SHA
        assert retry_of_operation_id(retry) == operation_id
        summary = _serialize_summary(retry)
        detail = _serialize_operation(retry)
        assert summary.retry_of_operation_id == operation_id
        assert summary.failure_policy == "continue-on-error"
        assert detail.retry_of_operation_id == operation_id
        assert detail.failure_policy == "continue-on-error"
        policy = orchestrator._policy_object(retry)
        assert policy["mapping_request"]["container_image_project"] == "docker-old"
        assert policy["mapping_request"]["helm_chart_project"] == "helm-old"
        assert policy["retry"]["failure_policy"] == "continue-on-error"
        assert all(row.status is ArtifactStatus.PENDING for row in retry.artifacts)


def test_retry_rejects_wrong_plan_without_creating_operation(tmp_path: Path) -> None:
    (
        session_factory,
        orchestrator,
        _skopeo,
        _helm,
        operation_id,
        _plan,
        actor_user_id,
    ) = _environment(tmp_path)
    with session_factory() as session:
        before = session.query(Operation).count()

    with pytest.raises(ImportOrchestrationError) as exc_info:
        asyncio.run(
            ImportRetryService(orchestrator).prepare_retry(
                operation_id,
                actor_user_id=actor_user_id,
                actor_username="operator",
                destination_plan_id="0" * 64,
            )
        )

    assert exc_info.value.code == "import_retry_plan_mismatch"
    with session_factory() as session:
        assert session.query(Operation).count() == before


def test_retry_rejects_missing_immutable_artifact_snapshot(tmp_path: Path) -> None:
    (
        session_factory,
        orchestrator,
        _skopeo,
        _helm,
        operation_id,
        plan,
        actor_user_id,
    ) = _environment(tmp_path)
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        operation.artifacts[0].destination_plan_id = None
        session.commit()

    with pytest.raises(ImportOrchestrationError) as exc_info:
        asyncio.run(
            ImportRetryService(orchestrator).prepare_retry(
                operation_id,
                actor_user_id=actor_user_id,
                actor_username="operator",
                destination_plan_id=plan.plan_id,
            )
        )

    assert exc_info.value.code == "import_retry_state_invalid"
