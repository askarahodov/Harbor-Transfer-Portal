from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import PortalContour, Settings
from app.db.base import Base
from app.db.session import create_db_engine, create_session_factory
from app.domain.artifacts import ArtifactKind
from app.domain.bundle import ArtifactStatus, OperationStatus
from app.schemas.exports import ExportArtifactSelection
from app.services.bundle_package_service import BundlePackageService
from app.services.export_orchestrator import ExportOrchestrator
from app.services.harbor_client import HarborArtifact
from app.services.helm_oci_service import HelmPackageMetadata, HelmPullResult
from app.services.nexus_helm_http import NexusHelmHttpError
from app.services.operation_manager import OperationManager
from app.services.skopeo_service import ImageReference, SkopeoServiceError

CHART_DIGEST = "sha256:" + "a" * 64
API_DIGEST = "sha256:" + "b" * 64
UI_DIGEST = "sha256:" + "c" * 64


class FakeHarborClient:
    def __init__(self) -> None:
        self.artifacts = {
            ("helm", "admin/admin", "1.0.0-build184"): HarborArtifact(
                digest=CHART_DIGEST,
                type="CHART",
                size=100,
            ),
            ("clever", "admin-api", "1.8.0-build549"): HarborArtifact(
                digest=API_DIGEST,
                type="IMAGE",
                size=200,
            ),
            ("clever", "admin-ui", "1.8.0-build317"): HarborArtifact(
                digest=UI_DIGEST,
                type="IMAGE",
                size=300,
            ),
        }

    def get_artifact(self, project: str, repository: str, reference: str) -> HarborArtifact:
        return self.artifacts[(project, repository, reference)]

    def close(self) -> None:
        pass


class FakeHelmService:
    def __init__(
        self, *, fail_with_nexus_http: bool = False, fail_unexpected: bool = False,
    ) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail_with_nexus_http = fail_with_nexus_http
        self.fail_unexpected = fail_unexpected

    async def pull_chart(self, chart, destination: Path) -> HelmPullResult:  # type: ignore[no-untyped-def]
        self.calls.append((chart.repository, chart.name, chart.version))
        if self.fail_unexpected:
            raise RuntimeError("password=CHART-SECRET-NEVER-PUBLIC")
        if self.fail_with_nexus_http:
            raise NexusHelmHttpError(
                "helm_index_unavailable",
                "Synthetic Helm HTTP retrieval failure",
            )
        destination.mkdir(parents=True)
        package = destination / f"{chart.name}-{chart.version}.tgz"
        package.write_bytes(b"chart-package")
        return HelmPullResult(
            source_digest=CHART_DIGEST,
            package=HelmPackageMetadata(
                path=package,
                name=chart.name,
                version=chart.version,
                sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
            ),
        )


class FailingSkopeoService:
    def __init__(self, *, fail_unexpected: bool = False) -> None:
        self.calls: list[ImageReference] = []
        self.fail_unexpected = fail_unexpected

    async def export_image(self, image: ImageReference, _destination: Path):  # type: ignore[no-untyped-def]
        self.calls.append(image)
        if self.fail_unexpected:
            raise RuntimeError("authorization=IMAGE-SECRET-NEVER-PUBLIC")
        raise SkopeoServiceError(
            "skopeo_command_failed",
            "Skopeo завершился с ошибкой: synthetic copy failure",
        )


def _write_keys(tmp_path: Path) -> tuple[Path, Path]:
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "data" / "keys" / "source-private.pem"
    trusted_dir = tmp_path / "data" / "keys" / "trusted"
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


def _selections() -> tuple[ExportArtifactSelection, ...]:
    return (
        ExportArtifactSelection(
            kind=ArtifactKind.HELM_CHART,
            project="helm",
            repository="admin/admin",
            reference="1.0.0-build184",
            digest=CHART_DIGEST,
        ),
        ExportArtifactSelection(
            kind=ArtifactKind.CONTAINER_IMAGE,
            project="clever",
            repository="admin-api",
            reference="1.8.0-build549",
            digest=API_DIGEST,
        ),
        ExportArtifactSelection(
            kind=ArtifactKind.CONTAINER_IMAGE,
            project="clever",
            repository="admin-ui",
            reference="1.8.0-build317",
            digest=UI_DIGEST,
        ),
    )


@pytest.mark.parametrize(
    "failure_mode",
    ["skopeo_typed", "helm_typed", "skopeo_unexpected", "helm_unexpected"],
)
def test_mixed_export_keeps_root_failure_on_actual_artifact(
    tmp_path: Path, failure_mode: str, caplog: pytest.LogCaptureFixture,
) -> None:
    private_key, trusted_dir = _write_keys(tmp_path)
    data = tmp_path / "data"
    database_url = f"sqlite:///{tmp_path / 'export.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    settings = Settings(
        _env_file=None,
        portal_contour=PortalContour.SOURCE,
        harbor_url="https://harbor.source.local",
        database_url=database_url,
        operation_workspace_root=data / "tmp" / "operations",
        operation_disk_reserve_bytes=0,
        skopeo_payload_root=data,
        skopeo_temp_root=data / "tmp" / "skopeo",
        helm_workspace_root=data / "packages",
        helm_temp_root=data / "tmp" / "helm",
        bundle_payload_root=data,
        bundle_temp_root=data / "tmp" / "bundles",
        bundle_outgoing_root=data / "outgoing",
        bundle_extract_root=data / "incoming" / "verified",
        bundle_signing_private_key_file=private_key,
        bundle_trusted_public_keys_dir=trusted_dir,
    )
    session_factory = create_session_factory(engine)
    manager = OperationManager(session_factory, settings)
    harbor = FakeHarborClient()
    helm = FakeHelmService(
        fail_with_nexus_http=failure_mode == "helm_typed",
        fail_unexpected=failure_mode == "helm_unexpected",
    )
    skopeo = FailingSkopeoService(fail_unexpected=failure_mode == "skopeo_unexpected")
    package_service = BundlePackageService(settings)
    orchestrator = ExportOrchestrator(
        session_factory,
        settings,
        manager,
        harbor_client_factory=lambda: harbor,
        skopeo_factory=lambda _session: skopeo,  # type: ignore[arg-type]
        helm_factory=lambda _session: helm,  # type: ignore[arg-type]
        package_factory=lambda: package_service,
    )

    async def scenario() -> int:
        await manager.startup()
        started = await orchestrator.start_export(
            _selections(),
            actor_user_id=None,  # type: ignore[arg-type]
            actor_username="admin",
            comment=None,
        )
        await manager.wait(started.operation_id)
        await manager.shutdown()
        return started.operation_id

    operation = manager.get_operation(asyncio.run(scenario()))
    assert operation is not None
    assert operation.status is OperationStatus.FAILED
    unexpected = failure_mode.endswith("unexpected")
    helm_failure = failure_mode.startswith("helm")
    expected_code = (
        "export_artifact_internal_error" if unexpected else
        "helm_index_unavailable" if helm_failure else "skopeo_command_failed"
    )
    assert operation.error_code == expected_code
    if unexpected:
        assert operation.error_message == "Внутренняя ошибка обработки артефакта при экспорте"
        log_records = [
            record for record in caplog.records
            if record.message == "unexpected export artifact materialization failure"
        ]
        assert log_records
        assert log_records[0].operation_id == operation.id
        assert log_records[0].artifact_id == (
            min(item.id for item in operation.artifacts)
            if helm_failure else
            sorted(item.id for item in operation.artifacts)[1]
        )

    artifacts = sorted(operation.artifacts, key=lambda item: item.id)
    assert [artifact.repository for artifact in artifacts] == [
        "helm/admin/admin",
        "clever/admin-api",
        "clever/admin-ui",
    ]
    assert [artifact.status for artifact in artifacts] == [
        ArtifactStatus.FAILED,
        ArtifactStatus.FAILED,
        ArtifactStatus.FAILED,
    ]
    expected_artifact_codes = (
        [expected_code, "export_aborted", "export_aborted"]
        if helm_failure else ["export_aborted", expected_code, "export_aborted"]
    )
    assert [artifact.error_code for artifact in artifacts] == expected_artifact_codes
    assert [artifact.error_code for artifact in artifacts].count(expected_code) == 1
    if failure_mode == "helm_typed":
        assert "Synthetic Helm HTTP retrieval failure" in (artifacts[0].error_message or "")
    elif failure_mode == "skopeo_typed":
        assert "synthetic copy failure" in (artifacts[1].error_message or "")
    if unexpected:
        public = "\\n".join(
            [operation.error_message or ""]
            + [item.error_message or "" for item in artifacts]
        )
        assert "CHART-SECRET-NEVER-PUBLIC" not in public
        assert "IMAGE-SECRET-NEVER-PUBLIC" not in public
        assert "operation_worker_failed" not in public
    assert helm.calls == [("helm/admin", "admin", "1.0.0-build184")]
    assert [(call.repository, call.reference) for call in skopeo.calls] == (
        [] if helm_failure else [("clever/admin-api", "1.8.0-build549")]
    )
    assert operation.bundle_filename is None
    assert operation.bundle_sha256 is None
    assert operation.bundle_handoff_filename is None
    assert not list((data / "outgoing").glob("*")) if (data / "outgoing").exists() else True
    assert not (data / "tmp" / "operations" / str(operation.id)).exists()
    assert not (
        data / "packages" / "export-operations" / f"operation-{operation.id}"
    ).exists()
