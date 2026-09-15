from __future__ import annotations

from app.domain.bundle import (
    BundleManifest,
    BundleSource,
    ContainerImageArtifact,
    HelmChartArtifact,
)
from app.domain.imports import ImportPreviewState
from app.schemas.imports import (
    ImportDestinationArtifactPlanResponse,
    ImportDestinationPlanResponse,
)
from app.services.bundle_package_service import BundleVerificationResult
from app.services.import_destination_plan import ImportDestinationPlanOrchestrator


def test_project_verified_bundle_uses_persisted_target_repositories(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    manifest = BundleManifest(
        schema_version="1.0",
        delivery_id="DELIVERY-20260915-WORKER1",
        created_at="2026-09-15T07:00:00Z",
        created_by="operator",
        source=BundleSource(contour="SOURCE", harbor="harbor.source.local", portal_version="1.0.0"),
        artifacts=[
            ContainerImageArtifact(
                repository="source-team/app/api",
                reference="1.4.2",
                source_digest="sha256:" + "a" * 64,
                payload_path="images/api",
                payload_sha256="b" * 64,
                payload_size=123,
            ),
            HelmChartArtifact(
                repository="source-charts/platform",
                name="mis",
                version="4.88.6",
                source_digest="sha256:" + "c" * 64,
                payload_path="charts/mis.tgz",
                payload_sha256="d" * 64,
                payload_size=456,
            ),
        ],
    )
    verified = BundleVerificationResult(
        manifest=manifest,
        archive_sha256="e" * 64,
        archive_size=1024,
        signing_key_fingerprint="f" * 64,
    )
    artifacts = [
        ImportDestinationArtifactPlanResponse(
            index=0,
            artifact_type="container-image",
            source_repository="source-team/app/api",
            source_project="source-team",
            reference="1.4.2",
            expected_digest="sha256:" + "a" * 64,
            payload_size=123,
            target_project="docker-prod",
            target_repository="docker-prod/app/api",
            final_reference="harbor.target.local/docker-prod/app/api:1.4.2",
            project_exists=True,
            write_allowed=True,
            classification=ImportPreviewState.NEW,
        ),
        ImportDestinationArtifactPlanResponse(
            index=1,
            artifact_type="helm-chart",
            source_repository="source-charts/platform",
            source_project="source-charts",
            name="mis",
            version="4.88.6",
            expected_digest="sha256:" + "c" * 64,
            payload_size=456,
            target_project="helm-prod",
            target_repository="helm-prod/platform",
            final_reference="oci://harbor.target.local/helm-prod/platform/mis:4.88.6",
            project_exists=True,
            write_allowed=True,
            classification=ImportPreviewState.NEW,
        ),
    ]
    plan = ImportDestinationPlanResponse(
        operation_id=17,
        bundle_sha256="e" * 64,
        plan_id=ImportDestinationPlanOrchestrator._plan_id("e" * 64, artifacts),
        created_at="2026-09-15T07:01:00Z",
        valid=True,
        artifacts=artifacts,
    )

    orchestrator = object.__new__(ImportDestinationPlanOrchestrator)
    monkeypatch.setattr(orchestrator, "destination_plan", lambda _operation_id: plan)

    projected = orchestrator._project_verified_bundle(17, verified)

    image = projected.manifest.artifacts[0]
    chart = projected.manifest.artifacts[1]
    assert isinstance(image, ContainerImageArtifact)
    assert image.repository == "docker-prod/app/api"
    assert image.reference == "1.4.2"
    assert isinstance(chart, HelmChartArtifact)
    assert chart.repository == "helm-prod/platform"
    assert chart.name == "mis"
    assert chart.version == "4.88.6"

    # The cryptographically verified source manifest is not mutated in place.
    assert manifest.artifacts[0].repository == "source-team/app/api"
    assert manifest.artifacts[1].repository == "source-charts/platform"
