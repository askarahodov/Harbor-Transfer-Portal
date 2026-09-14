from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.domain.bundle import BundleManifest, BundleSource, ContainerImageArtifact, OperationStatus
from app.schemas.imports import ImportPreviewResponse
from app.services.bundle_package_service import BundleVerificationResult
from app.services.import_preview_projection import ImportPreviewProjectionOrchestrator


class _Context:
    def __init__(self) -> None:
        self.transitions: list[OperationStatus] = []

    def transition(self, status: OperationStatus) -> None:
        self.transitions.append(status)


class _PackageService:
    def __init__(self, verified: BundleVerificationResult) -> None:
        self.verified = verified
        self.calls: list[tuple[Path, Path | None]] = []

    def verify_bundle(
        self,
        archive: Path,
        *,
        sidecar_path: Path | None = None,
    ) -> BundleVerificationResult:
        self.calls.append((archive, sidecar_path))
        return self.verified


def test_verified_preview_projects_signed_manifest_and_policy(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    created_at = datetime(2026, 9, 14, 5, 30, tzinfo=UTC)
    manifest = BundleManifest(
        schema_version="1.0",
        delivery_id="DELIVERY-20260914-PREVIEW1",
        created_at=created_at,
        created_by="source-operator",
        source=BundleSource(
            contour="SOURCE",
            harbor="harbor.source.local",
            portal_version="0.1.0",
        ),
        comment="offline delivery",
        artifacts=[
            ContainerImageArtifact(
                repository="project/app",
                reference="1.0.0",
                source_digest="sha256:" + "a" * 64,
                payload_path="images/project-app",
                payload_sha256="b" * 64,
                payload_size=123,
            )
        ],
    )
    verified = BundleVerificationResult(
        manifest=manifest,
        archive_sha256="c" * 64,
        archive_size=456,
        signing_key_fingerprint="d" * 64,
    )
    package = _PackageService(verified)
    orchestrator = object.__new__(ImportPreviewProjectionOrchestrator)
    orchestrator.settings = SimpleNamespace(import_allow_overwrite=True)
    orchestrator.package_factory = lambda: package

    operation = SimpleNamespace(
        bundle_sha256=verified.archive_sha256,
        bundle_filename="bundle.htp.tar.gz",
        import_intake_mode="incoming",
    )
    persisted: list[ImportPreviewResponse] = []
    monkeypatch.setattr(
        orchestrator,
        "_bundle_paths",
        lambda _operation_id: (Path("bundle.htp.tar.gz"), Path("bundle.htp.tar.gz.sha256")),
    )
    monkeypatch.setattr(orchestrator, "_get_import_operation", lambda _operation_id: operation)

    async def classify(_artifacts):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr(orchestrator, "_classify_manifest", classify)
    monkeypatch.setattr(
        orchestrator,
        "_persist_preview",
        lambda _operation_id, preview: persisted.append(preview),
    )

    context = _Context()
    asyncio.run(orchestrator._preview_worker(context, 41))

    assert context.transitions == [OperationStatus.VERIFYING, OperationStatus.READY]
    assert package.calls == [(Path("bundle.htp.tar.gz"), Path("bundle.htp.tar.gz.sha256"))]
    assert len(persisted) == 1
    preview = persisted[0]
    assert preview.source_delivery_id == manifest.delivery_id
    assert preview.source_harbor == "harbor.source.local"
    assert preview.source_portal_version == "0.1.0"
    assert preview.source_created_at == created_at
    assert preview.source_created_by == "source-operator"
    assert preview.source_comment == "offline delivery"
    assert preview.bundle_filename == "bundle.htp.tar.gz"
    assert preview.intake_mode == "incoming"
    assert preview.bundle_sha256 == verified.archive_sha256
    assert preview.signing_key_fingerprint == verified.signing_key_fingerprint
    assert preview.checksum_verified is True
    assert preview.signature_verified is True
    assert preview.schema_verified is True
    assert preview.overwrite_allowed is True


def test_old_persisted_preview_shape_remains_readable() -> None:
    preview = ImportPreviewResponse.model_validate(
        {
            "operation_id": 7,
            "status": "READY",
            "source_delivery_id": "DELIVERY-20260914-LEGACY1",
            "bundle_sha256": "a" * 64,
            "bundle_size_bytes": 100,
            "signing_key_fingerprint": "b" * 64,
            "verified_at": "2026-09-14T05:30:00Z",
            "artifacts": [],
        }
    )

    assert preview.source_harbor is None
    assert preview.checksum_verified is False
    assert preview.signature_verified is False
    assert preview.schema_verified is False
    assert preview.overwrite_allowed is False
