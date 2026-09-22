import asyncio
from datetime import UTC, datetime
from pathlib import Path

from app.config import PortalContour, Settings
from app.db.base import Base
from app.db.models import Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import OperationStatus, OperationType
from app.domain.imports import ImportPreviewState
from app.schemas.imports import (
    ImportArtifactPreviewResponse,
    ImportDestinationPlanRequest,
    ImportPreviewResponse,
)
from app.services.destination_mapping_policy import DestinationMappingPolicyService
from app.services.harbor_destination_validator import DestinationCapability
from app.services.helm_oci_service import HelmTargetInspection, HelmTargetState
from app.services.operation_manager import OperationManager
from app.services.policy_aware_destination_plan import PolicyAwareImportDestinationPlanOrchestrator
from app.services.skopeo_service import TargetInspection, TargetState

BUNDLE_SHA = "c" * 64
IMAGE_DIGEST = "sha256:" + "a" * 64


class FakeDestinationValidator:
    registry_host = "harbor.target.local"

    def __init__(
        self,
        *,
        missing_projects: set[str] | None = None,
        denied_projects: set[str] | None = None,
    ) -> None:
        self.missing_projects = missing_projects or set()
        self.denied_projects = denied_projects or set()

    async def validate(self, project: str, _repository: str) -> DestinationCapability:
        if project in self.missing_projects:
            return DestinationCapability(
                False,
                False,
                "import_destination_project_missing",
                "missing project",
            )
        if project in self.denied_projects:
            return DestinationCapability(
                True,
                False,
                "import_destination_write_forbidden",
                "push denied",
            )
        return DestinationCapability(True, True)


class FakeSkopeoService:
    async def inspect_target(self, _image, *, expected_digest=None):  # type: ignore[no-untyped-def]
        return TargetInspection(TargetState.ABSENT, None)


class FakeHelmService:
    async def inspect_target(self, _chart, *, expected_digest=None):  # type: ignore[no-untyped-def]
        return HelmTargetInspection(HelmTargetState.ABSENT, None)


def _settings(tmp_path: Path, database_url: str) -> Settings:
    data = tmp_path / "data"
    return Settings(
        _env_file=None,
        database_url=database_url,
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


def _preview(operation_id: int) -> ImportPreviewResponse:
    return ImportPreviewResponse(
        operation_id=operation_id,
        status=OperationStatus.READY,
        source_delivery_id="DELIVERY-20260915-POLICY01",
        bundle_sha256=BUNDLE_SHA,
        bundle_size_bytes=1024,
        signing_key_fingerprint="d" * 64,
        verified_at=datetime(2026, 9, 15, 9, 0, tzinfo=UTC),
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


def _environment(
    tmp_path: Path,
    *,
    validator: FakeDestinationValidator | None = None,
):
    database_url = f"sqlite:///{tmp_path / 'mapping-plan-policy.db'}"
    settings = _settings(tmp_path, database_url)
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    manager = OperationManager(factory, settings)
    validator = validator or FakeDestinationValidator()
    orchestrator = PolicyAwareImportDestinationPlanOrchestrator(
        factory,
        settings,
        manager,
        skopeo_factory=lambda _session: FakeSkopeoService(),  # type: ignore[arg-type]
        helm_factory=lambda _session: FakeHelmService(),  # type: ignore[arg-type]
        destination_validator_factory=lambda _session: validator,
    )
    with factory() as session:
        operation = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.READY,
            actor_username="operator",
            bundle_sha256=BUNDLE_SHA,
            source_delivery_id="DELIVERY-20260915-POLICY01",
        )
        session.add(operation)
        session.flush()
        operation.import_preview_json = _preview(operation.id).model_dump_json()
        session.commit()
        operation_id = operation.id
    return factory, orchestrator, operation_id


def test_existing_plan_keeps_policy_snapshot_until_operator_rebuilds(tmp_path: Path) -> None:
    factory, orchestrator, operation_id = _environment(tmp_path)
    with factory() as session:
        first_change = DestinationMappingPolicyService(session).update(
            {"container_image_project": "images-v1"}
        )
        session.commit()
        assert first_change.snapshot.revision == 1

    first = asyncio.run(
        orchestrator.build_destination_plan(
            operation_id,
            ImportDestinationPlanRequest(),
            actor_username="operator",
        )
    )
    assert first.mapping_policy_revision == 1
    assert first.artifacts[0].target_repository == "images-v1/app/api"

    with factory() as session:
        second_change = DestinationMappingPolicyService(session).update(
            {"container_image_project": "images-v2"}
        )
        session.commit()
        assert second_change.snapshot.revision == 2

    frozen = orchestrator.destination_plan(operation_id)
    assert frozen.mapping_policy_revision == 1
    assert frozen.plan_hash == first.plan_hash
    assert frozen.artifacts[0].target_repository == "images-v1/app/api"

    rebuilt = asyncio.run(
        orchestrator.build_destination_plan(
            operation_id,
            ImportDestinationPlanRequest(),
            actor_username="operator",
        )
    )
    assert rebuilt.mapping_policy_revision == 2
    assert rebuilt.plan_hash != first.plan_hash
    assert rebuilt.artifacts[0].target_repository == "images-v2/app/api"


def test_explicit_import_default_overrides_global_default(tmp_path: Path) -> None:
    factory, orchestrator, operation_id = _environment(tmp_path)
    with factory() as session:
        DestinationMappingPolicyService(session).update(
            {"container_image_project": "images-global"}
        )
        session.commit()

    plan = asyncio.run(
        orchestrator.build_destination_plan(
            operation_id,
            ImportDestinationPlanRequest(container_image_project="images-explicit"),
            actor_username="operator",
        )
    )

    assert plan.mapping_policy_revision == 1
    assert plan.artifacts[0].target_repository == "images-explicit/app/api"


def test_missing_global_default_project_keeps_plan_invalid(tmp_path: Path) -> None:
    validator = FakeDestinationValidator(missing_projects={"images-missing"})
    factory, orchestrator, operation_id = _environment(tmp_path, validator=validator)
    with factory() as session:
        DestinationMappingPolicyService(session).update(
            {"container_image_project": "images-missing"}
        )
        session.commit()

    plan = asyncio.run(
        orchestrator.build_destination_plan(
            operation_id,
            ImportDestinationPlanRequest(),
            actor_username="operator",
        )
    )

    assert plan.valid is False
    assert plan.mapping_policy_revision == 1
    assert plan.artifacts[0].error_code == "import_destination_project_missing"
    assert plan.artifacts[0].project_exists is False


def test_global_default_without_write_access_keeps_plan_invalid(tmp_path: Path) -> None:
    validator = FakeDestinationValidator(denied_projects={"images-denied"})
    factory, orchestrator, operation_id = _environment(tmp_path, validator=validator)
    with factory() as session:
        DestinationMappingPolicyService(session).update(
            {"container_image_project": "images-denied"}
        )
        session.commit()

    plan = asyncio.run(
        orchestrator.build_destination_plan(
            operation_id,
            ImportDestinationPlanRequest(),
            actor_username="operator",
        )
    )

    assert plan.valid is False
    assert plan.mapping_policy_revision == 1
    assert plan.artifacts[0].error_code == "import_destination_write_forbidden"
    assert plan.artifacts[0].project_exists is True
    assert plan.artifacts[0].write_allowed is False
