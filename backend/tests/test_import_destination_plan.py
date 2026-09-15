from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.auth.security import hash_password
from app.config import PortalContour, Settings
from app.db.base import Base
from app.db.models import ArtifactResult, Operation, UserRole
from app.db.repositories import UserRepository
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType
from app.domain.imports import ImportPreviewState
from app.main import create_app
from app.schemas.imports import (
    ImportArtifactDestinationOverride,
    ImportArtifactPreviewResponse,
    ImportDestinationPlanRequest,
    ImportPreviewResponse,
)
from app.services.helm_oci_service import HelmTargetInspection, HelmTargetState
from app.services.import_destination_plan import (
    DestinationCapability,
    HarborDestinationValidator,
    ImportDestinationPlanOrchestrator,
)
from app.services.import_orchestrator import ImportOrchestrationError
from app.services.operation_manager import OperationManager
from app.services.skopeo_service import TargetInspection, TargetState

IMAGE_DIGEST = "sha256:" + "a" * 64
CHART_DIGEST = "sha256:" + "b" * 64
BUNDLE_SHA = "c" * 64
JWT_SECRET = "test-destination-plan-jwt-secret-123456789"


class FakeDestinationValidator:
    registry_host = "harbor.target.local"

    def __init__(
        self,
        *,
        missing_projects: set[str] | None = None,
        denied_repositories: set[str] | None = None,
    ) -> None:
        self.missing_projects = missing_projects or set()
        self.denied_repositories = denied_repositories or set()
        self.calls: list[tuple[str, str]] = []

    async def validate(self, project: str, repository: str) -> DestinationCapability:
        self.calls.append((project, repository))
        if project in self.missing_projects:
            return DestinationCapability(
                False,
                False,
                "import_destination_project_missing",
                "missing project",
            )
        if repository in self.denied_repositories:
            return DestinationCapability(
                True,
                False,
                "import_destination_write_forbidden",
                "push denied",
            )
        return DestinationCapability(True, True)


class FakeSkopeoService:
    def __init__(self) -> None:
        self.inspected: list[tuple[str, str]] = []

    async def inspect_target(self, image, *, expected_digest=None):  # type: ignore[no-untyped-def]
        self.inspected.append((image.repository, image.reference))
        return TargetInspection(TargetState.ABSENT, None)


class FakeHelmService:
    def __init__(self) -> None:
        self.inspected: list[tuple[str, str, str]] = []

    async def inspect_target(self, chart, *, expected_digest=None):  # type: ignore[no-untyped-def]
        self.inspected.append((chart.repository, chart.name, chart.version))
        return HelmTargetInspection(HelmTargetState.ABSENT, None)


def _settings(tmp_path: Path, database_url: str) -> Settings:
    data = tmp_path / "data"
    return Settings(
        _env_file=None,
        database_url=database_url,
        jwt_secret=JWT_SECRET,
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
        source_delivery_id="DELIVERY-20260915-PLAN01",
        bundle_sha256=BUNDLE_SHA,
        bundle_size_bytes=4096,
        signing_key_fingerprint="d" * 64,
        verified_at=datetime(2026, 9, 15, 7, 0, tzinfo=UTC),
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


def _environment(
    tmp_path: Path,
    *,
    validator: FakeDestinationValidator | None = None,
):
    database_url = f"sqlite:///{tmp_path / 'destination-plan.db'}"
    settings = _settings(tmp_path, database_url)
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    manager = OperationManager(session_factory, settings)
    skopeo = FakeSkopeoService()
    helm = FakeHelmService()
    validator = validator or FakeDestinationValidator()
    orchestrator = ImportDestinationPlanOrchestrator(
        session_factory,
        settings,
        manager,
        skopeo_factory=lambda _session: skopeo,  # type: ignore[arg-type]
        helm_factory=lambda _session: helm,  # type: ignore[arg-type]
        destination_validator_factory=lambda _session: validator,
    )
    with session_factory() as session:
        operation = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.READY,
            actor_username="operator",
            bundle_sha256=BUNDLE_SHA,
        )
        session.add(operation)
        session.flush()
        operation.import_preview_json = _preview(operation.id).model_dump_json()
        operation.source_delivery_id = "DELIVERY-20260915-PLAN01"
        session.commit()
        operation_id = operation.id
    return settings, session_factory, manager, skopeo, helm, validator, orchestrator, operation_id


def test_mixed_bundle_resolves_separate_image_and_helm_projects(tmp_path: Path) -> None:
    (
        _settings_value,
        session_factory,
        _manager,
        skopeo,
        helm,
        validator,
        orchestrator,
        operation_id,
    ) = _environment(tmp_path)

    async def scenario():
        request = ImportDestinationPlanRequest(
            container_image_project="docker-prod",
            helm_chart_project="helm-prod",
        )
        first = await orchestrator.build_destination_plan(operation_id, request)
        second = await orchestrator.build_destination_plan(operation_id, request)
        return first, second

    first, second = asyncio.run(scenario())

    assert first.valid is True
    assert first.plan_id == second.plan_id
    assert first.artifacts[0].target_repository == "docker-prod/app/api"
    assert first.artifacts[0].final_reference == "harbor.target.local/docker-prod/app/api:1.4.2"
    assert first.artifacts[1].target_repository == "helm-prod/platform"
    assert first.artifacts[1].final_reference == (
        "oci://harbor.target.local/helm-prod/platform/mis:4.88.6"
    )
    assert skopeo.inspected[-1] == ("docker-prod/app/api", "1.4.2")
    assert helm.inspected[-1] == ("helm-prod/platform", "mis", "4.88.6")
    assert ("docker-prod", "docker-prod/app/api") in validator.calls
    assert ("helm-prod", "helm-prod/platform/mis") in validator.calls

    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None and operation.import_policy_json is not None
        persisted = operation.import_policy_json.lower()
        assert "password" not in persisted
        assert "credential" not in persisted
        assert "secret" not in persisted
        policy = json.loads(operation.import_policy_json)
        assert policy["destination_plan"]["plan_id"] == first.plan_id


def test_receipt_persists_actual_destination_references(tmp_path: Path) -> None:
    settings, session_factory, *_rest, orchestrator, operation_id = _environment(tmp_path)
    plan = asyncio.run(
        orchestrator.build_destination_plan(
            operation_id,
            ImportDestinationPlanRequest(
                container_image_project="docker-prod",
                helm_chart_project="helm-prod",
            ),
        )
    )
    with session_factory() as session:
        session.add_all(
            [
                ArtifactResult(
                    operation_id=operation_id,
                    artifact_type="container-image",
                    repository="source-team/app/api",
                    reference="1.4.2",
                    source_digest=IMAGE_DIGEST,
                    target_digest=IMAGE_DIGEST,
                    status=ArtifactStatus.VERIFIED,
                    size_bytes=1024,
                ),
                ArtifactResult(
                    operation_id=operation_id,
                    artifact_type="helm-chart",
                    repository="source-charts/platform",
                    name="mis",
                    version="4.88.6",
                    source_digest=CHART_DIGEST,
                    target_digest=CHART_DIGEST,
                    status=ArtifactStatus.VERIFIED,
                    size_bytes=2048,
                ),
            ]
        )
        session.commit()

    requested_at = datetime(2026, 9, 15, 7, 5, tzinfo=UTC)
    orchestrator._write_receipt(
        operation_id,
        _preview(operation_id),
        False,
        requested_at,
        0,
    )

    receipt = orchestrator.receipt(operation_id)
    assert receipt.destination_plan_id == plan.plan_id
    assert receipt.artifacts[0].repository == "source-team/app/api"
    assert receipt.artifacts[0].target_repository == "docker-prod/app/api"
    assert receipt.artifacts[0].final_reference == (
        "harbor.target.local/docker-prod/app/api:1.4.2"
    )
    assert receipt.artifacts[1].repository == "source-charts/platform"
    assert receipt.artifacts[1].target_repository == "helm-prod/platform"
    assert receipt.artifacts[1].final_reference == (
        "oci://harbor.target.local/helm-prod/platform/mis:4.88.6"
    )
    persisted = (settings.import_receipt_root / f"import-{operation_id}.json").read_text(
        encoding="utf-8"
    )
    assert plan.plan_id in persisted
    assert "docker-prod/app/api" in persisted
    assert "helm-prod/platform" in persisted


def test_mapping_precedence_is_override_then_source_mapping_then_kind_default(
    tmp_path: Path,
) -> None:
    *_, orchestrator, operation_id = _environment(tmp_path)

    plan = asyncio.run(
        orchestrator.build_destination_plan(
            operation_id,
            ImportDestinationPlanRequest(
                container_image_project="docker-default",
                helm_chart_project="helm-default",
                project_mappings={
                    "source-team": "mapped-images",
                    "source-charts": "mapped-charts",
                },
                artifact_overrides=[
                    ImportArtifactDestinationOverride(index=0, target_project="override-images")
                ],
            ),
        )
    )

    assert plan.artifacts[0].target_project == "override-images"
    assert plan.artifacts[0].target_repository == "override-images/app/api"
    assert plan.artifacts[1].target_project == "mapped-charts"
    assert plan.artifacts[1].target_repository == "mapped-charts/platform"


def test_missing_project_and_no_write_access_fail_closed_before_target_inspection(
    tmp_path: Path,
) -> None:
    validator = FakeDestinationValidator(
        missing_projects={"missing"},
        denied_repositories={"readonly/platform/mis"},
    )
    (
        _settings_value,
        _session_factory,
        _manager,
        skopeo,
        helm,
        _validator,
        orchestrator,
        operation_id,
    ) = _environment(tmp_path, validator=validator)

    plan = asyncio.run(
        orchestrator.build_destination_plan(
            operation_id,
            ImportDestinationPlanRequest(
                container_image_project="missing",
                helm_chart_project="readonly",
            ),
        )
    )

    assert plan.valid is False
    assert [item.error_code for item in plan.artifacts] == [
        "import_destination_project_missing",
        "import_destination_write_forbidden",
    ]
    assert skopeo.inspected == []
    assert helm.inspected == []
    with pytest.raises(ImportOrchestrationError) as blocked:
        asyncio.run(
            orchestrator.start_import(
                operation_id,
                actor_username="operator",
                overwrite_conflicts=False,
                destination_plan_id=plan.plan_id,
            )
        )
    assert blocked.value.code == "import_destination_plan_invalid"


def test_different_mapping_changes_plan_id_and_stale_plan_is_rejected(tmp_path: Path) -> None:
    *_, orchestrator, operation_id = _environment(tmp_path)

    first = asyncio.run(
        orchestrator.build_destination_plan(
            operation_id,
            ImportDestinationPlanRequest(
                container_image_project="docker-a",
                helm_chart_project="helm-a",
            ),
        )
    )
    second = asyncio.run(
        orchestrator.build_destination_plan(
            operation_id,
            ImportDestinationPlanRequest(
                container_image_project="docker-b",
                helm_chart_project="helm-b",
            ),
        )
    )

    assert first.plan_id != second.plan_id
    with pytest.raises(ImportOrchestrationError) as stale:
        asyncio.run(
            orchestrator.start_import(
                operation_id,
                actor_username="operator",
                overwrite_conflicts=False,
                destination_plan_id=first.plan_id,
            )
        )
    assert stale.value.code == "import_destination_plan_stale"


def test_registry_token_scope_requires_exact_repository_push_action() -> None:
    payload = {
        "access": [
            {
                "type": "repository",
                "name": "docker-prod/app/api",
                "actions": ["pull", "push"],
            }
        ]
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    token = f"header.{encoded}.signature"

    assert HarborDestinationValidator._jwt_allows_push(token, "docker-prod/app/api") is True
    assert HarborDestinationValidator._jwt_allows_push(token, "docker-prod/app/ui") is False


def _migrate(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def test_destination_plan_api_returns_resolved_final_references(tmp_path: Path) -> None:
    from app.api.imports import get_import_orchestrator

    database_url = f"sqlite:///{tmp_path / 'destination-plan-api.db'}"
    _migrate(database_url)
    settings = _settings(tmp_path, database_url)
    app = create_app(settings)

    with app.state.session_factory() as session:
        users = UserRepository(session)
        operator = users.create(
            username="operator",
            password_hash=hash_password("operator-password-123"),
            role=UserRole.OPERATOR,
        )
        session.commit()
        session.refresh(operator)
        operation = Operation(
            type=OperationType.IMPORT,
            status=OperationStatus.READY,
            actor_user_id=operator.id,
            actor_username=operator.username,
            bundle_sha256=BUNDLE_SHA,
        )
        session.add(operation)
        session.flush()
        operation.import_preview_json = _preview(operation.id).model_dump_json()
        operation.source_delivery_id = "DELIVERY-20260915-PLAN01"
        session.commit()
        operation_id = operation.id

    validator = FakeDestinationValidator()
    skopeo = FakeSkopeoService()
    helm = FakeHelmService()
    orchestrator = ImportDestinationPlanOrchestrator(
        app.state.session_factory,
        settings,
        app.state.operation_manager,
        skopeo_factory=lambda _session: skopeo,  # type: ignore[arg-type]
        helm_factory=lambda _session: helm,  # type: ignore[arg-type]
        destination_validator_factory=lambda _session: validator,
    )
    app.dependency_overrides[get_import_orchestrator] = lambda: orchestrator

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "operator", "password": "operator-password-123"},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = client.put(
            f"/api/imports/{operation_id}/destination-plan",
            headers=headers,
            json={
                "container_image_project": "docker-prod",
                "helm_chart_project": "helm-prod",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["artifacts"][0]["target_repository"] == "docker-prod/app/api"
    assert body["artifacts"][1]["target_repository"] == "helm-prod/platform"
