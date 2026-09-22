from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import PortalContour, Settings
from app.db.base import Base
from app.db.models import Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import (
    ContainerImageArtifact,
    OperationStatus,
    OperationType,
)
from app.domain.imports import ImportPreviewState
from app.schemas.imports import (
    ImportArtifactPreviewResponse,
    ImportDestinationPlanRequest,
    ImportPreviewResponse,
)
from app.services.harbor_destination_validator import DestinationCapability
from app.services.helm_oci_service import HelmTargetInspection, HelmTargetState
from app.services.import_destination_plan import ImportDestinationPlanOrchestrator
from app.services.import_orchestrator import ImportOrchestrationError
from app.services.operation_manager import OperationManager
from app.services.skopeo_service import TargetInspection, TargetState

IMAGE_DIGEST = "sha256:" + "a" * 64
CHART_DIGEST = "sha256:" + "b" * 64
OTHER_DIGEST = "sha256:" + "f" * 64
BUNDLE_SHA = "c" * 64
DELIVERY_ID = "DELIVERY-20260915-PLAN01"
JWT_SECRET = "test-destination-hardening-jwt-secret-123456789"


class FakeDestinationValidator:
    registry_host = "harbor.target.local"

    async def validate(self, project: str, repository: str) -> DestinationCapability:
        return DestinationCapability(True, True)


class MutableSkopeoService:
    def __init__(self) -> None:
        self.state = TargetState.ABSENT
        self.digest: str | None = None
        self.inspected: list[tuple[str, str]] = []

    async def inspect_target(self, image, *, expected_digest=None):  # type: ignore[no-untyped-def]
        self.inspected.append((image.repository, image.reference))
        return TargetInspection(self.state, self.digest)


class MutableHelmService:
    def __init__(self) -> None:
        self.state = HelmTargetState.ABSENT
        self.digest: str | None = None

    async def inspect_target(self, chart, *, expected_digest=None):  # type: ignore[no-untyped-def]
        return HelmTargetInspection(self.state, self.digest)


def _settings(tmp_path: Path, database_url: str) -> Settings:
    data = tmp_path / "data"
    return Settings(
        _env_file=None,
        database_url=database_url,
        jwt_secret=JWT_SECRET,
        portal_contour=PortalContour.TARGET,
        harbor_url="https://harbor.target.local",
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


def _single_image_preview(operation_id: int) -> ImportPreviewResponse:
    return ImportPreviewResponse(
        operation_id=operation_id,
        status=OperationStatus.READY,
        source_delivery_id=DELIVERY_ID,
        bundle_sha256=BUNDLE_SHA,
        bundle_size_bytes=4096,
        signing_key_fingerprint="d" * 64,
        verified_at=datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
        artifacts=[
            ImportArtifactPreviewResponse(
                index=0,
                artifact_type="container-image",
                repository="source-team/app/api",
                reference="1.4.2",
                expected_digest=IMAGE_DIGEST,
                payload_size=1024,
                classification=ImportPreviewState.NEW,
            )
        ],
    )


def _environment(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'destination-hardening.db'}"
    settings = _settings(tmp_path, database_url)
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    manager = OperationManager(session_factory, settings)
    skopeo = MutableSkopeoService()
    helm = MutableHelmService()
    orchestrator = ImportDestinationPlanOrchestrator(
        session_factory,
        settings,
        manager,
        skopeo_factory=lambda _session: skopeo,  # type: ignore[arg-type]
        helm_factory=lambda _session: helm,  # type: ignore[arg-type]
        destination_validator_factory=lambda _session: FakeDestinationValidator(),
    )
    with session_factory() as session:
        operation = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.READY,
            actor_username="operator",
            bundle_sha256=BUNDLE_SHA,
            source_delivery_id=DELIVERY_ID,
        )
        session.add(operation)
        session.flush()
        operation.import_preview_json = _single_image_preview(operation.id).model_dump_json()
        session.commit()
        operation_id = operation.id
    return session_factory, manager, skopeo, helm, orchestrator, operation_id


def _build_image_plan(
    orchestrator: ImportDestinationPlanOrchestrator,
    operation_id: int,
    *,
    actor_username: str = "operator",
):
    return asyncio.run(
        orchestrator.build_destination_plan(
            operation_id,
            ImportDestinationPlanRequest(container_image_project="docker-prod"),
            actor_username=actor_username,
        )
    )


def test_execute_requires_explicit_persisted_plan_id(tmp_path: Path) -> None:
    _session_factory, manager, _skopeo, _helm, orchestrator, operation_id = _environment(
        tmp_path
    )
    _build_image_plan(orchestrator, operation_id)
    manager.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    with pytest.raises(ImportOrchestrationError) as blocked:
        asyncio.run(
            orchestrator.start_import(
                operation_id,
                actor_username="operator",
                overwrite_conflicts=False,
                destination_plan_id=None,
            )
        )

    assert blocked.value.code == "import_destination_plan_required"


def test_execute_rejects_plan_confirmed_by_different_actor(tmp_path: Path) -> None:
    _session_factory, manager, _skopeo, _helm, orchestrator, operation_id = _environment(
        tmp_path
    )
    plan = _build_image_plan(orchestrator, operation_id, actor_username="operator")
    manager.submit = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    with pytest.raises(ImportOrchestrationError) as blocked:
        asyncio.run(
            orchestrator.start_import(
                operation_id,
                actor_username="admin",
                overwrite_conflicts=False,
                destination_plan_id=plan.plan_id,
            )
        )

    assert blocked.value.code == "import_destination_plan_actor_mismatch"


def test_persisted_plan_hash_detects_destination_tampering(tmp_path: Path) -> None:
    session_factory, _manager, _skopeo, _helm, orchestrator, operation_id = _environment(
        tmp_path
    )
    _build_image_plan(orchestrator, operation_id)

    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None and operation.import_policy_json is not None
        policy = json.loads(operation.import_policy_json)
        policy["destination_plan"]["artifacts"][0]["target_repository"] = "evil/app/api"
        operation.import_policy_json = json.dumps(policy, sort_keys=True, separators=(",", ":"))
        session.commit()

    with pytest.raises(ImportOrchestrationError) as blocked:
        orchestrator.destination_plan(operation_id)

    assert blocked.value.code == "import_destination_plan_invalid"


def test_persisted_plan_is_bound_to_current_delivery(tmp_path: Path) -> None:
    session_factory, _manager, _skopeo, _helm, orchestrator, operation_id = _environment(
        tmp_path
    )
    _build_image_plan(orchestrator, operation_id)

    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        operation.source_delivery_id = "DELIVERY-20260915-OTHER01"
        session.commit()

    with pytest.raises(ImportOrchestrationError) as blocked:
        orchestrator.destination_plan(operation_id)

    assert blocked.value.code == "import_destination_plan_stale"


def test_mixed_image_and_helm_collision_blocks_whole_plan(tmp_path: Path) -> None:
    session_factory, _manager, _skopeo, _helm, orchestrator, operation_id = _environment(
        tmp_path
    )
    preview = ImportPreviewResponse(
        operation_id=operation_id,
        status=OperationStatus.READY,
        source_delivery_id=DELIVERY_ID,
        bundle_sha256=BUNDLE_SHA,
        bundle_size_bytes=4096,
        signing_key_fingerprint="d" * 64,
        verified_at=datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
        artifacts=[
            ImportArtifactPreviewResponse(
                index=0,
                artifact_type="container-image",
                repository="source-images/platform/mis",
                reference="4.88.6",
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
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        operation.import_preview_json = preview.model_dump_json()
        session.commit()

    plan = asyncio.run(
        orchestrator.build_destination_plan(
            operation_id,
            ImportDestinationPlanRequest(
                container_image_project="shared",
                helm_chart_project="shared",
            ),
            actor_username="operator",
        )
    )

    assert plan.valid is False
    assert [item.error_code for item in plan.artifacts] == [
        "import_destination_collision",
        "import_destination_collision",
    ]


def test_pre_mutation_revalidation_observes_new_conflict(tmp_path: Path) -> None:
    _session_factory, _manager, skopeo, helm, orchestrator, _operation_id = _environment(
        tmp_path
    )
    descriptor = ContainerImageArtifact(
        repository="docker-prod/app/api",
        reference="1.4.2",
        source_digest=IMAGE_DIGEST,
        payload_path="images/api",
        payload_sha256="e" * 64,
        payload_size=1024,
    )

    skopeo.state = TargetState.CONFLICTING_DIGEST
    skopeo.digest = OTHER_DIGEST
    classification, digest = asyncio.run(
        orchestrator._preflight_target(descriptor, skopeo, helm)
    )

    assert classification is ImportPreviewState.CONFLICT
    assert digest == OTHER_DIGEST
    assert skopeo.inspected[-1] == ("docker-prod/app/api", "1.4.2")


def test_pre_mutation_revalidation_turns_matching_target_into_safe_skip(tmp_path: Path) -> None:
    _session_factory, _manager, skopeo, helm, orchestrator, _operation_id = _environment(
        tmp_path
    )
    descriptor = ContainerImageArtifact(
        repository="docker-prod/app/api",
        reference="1.4.2",
        source_digest=IMAGE_DIGEST,
        payload_path="images/api",
        payload_sha256="e" * 64,
        payload_size=1024,
    )

    skopeo.state = TargetState.SAME_DIGEST
    skopeo.digest = IMAGE_DIGEST
    classification, digest = asyncio.run(
        orchestrator._preflight_target(descriptor, skopeo, helm)
    )

    assert classification is ImportPreviewState.SAME
    assert digest == IMAGE_DIGEST
