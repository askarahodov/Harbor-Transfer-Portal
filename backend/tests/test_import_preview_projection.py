from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.domain.bundle import BundleManifest, BundleSource, ContainerImageArtifact, OperationStatus
from app.schemas.imports import ImportPreviewResponse
from app.services.bundle_package_service import BundleVerificationResult
from app.services.import_orchestrator import ImportOrchestrator
from app.services.import_preview_projection import ImportPreviewProjectionOrchestrator


def _verified_result() -> BundleVerificationResult:
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
    return BundleVerificationResult(
        manifest=manifest,
        archive_sha256="c" * 64,
        archive_size=456,
        signing_key_fingerprint="d" * 64,
    )


def test_projection_uses_authoritative_base_preview_worker() -> None:
    assert "_preview_worker" not in ImportPreviewProjectionOrchestrator.__dict__
    assert (
        ImportPreviewProjectionOrchestrator._preview_worker
        is ImportOrchestrator._preview_worker
    )


def test_persist_preview_enriches_verified_manifest_and_policy(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    verified = _verified_result()
    orchestrator = object.__new__(ImportPreviewProjectionOrchestrator)
    orchestrator.settings = SimpleNamespace(import_allow_overwrite=True)
    orchestrator._verified_preview = verified

    operation = SimpleNamespace(
        bundle_filename="bundle.htp.tar.gz",
        import_intake_mode="incoming",
    )
    monkeypatch.setattr(orchestrator, "_get_import_operation", lambda _operation_id: operation)

    persisted: list[ImportPreviewResponse] = []

    def persist(_self, _operation_id, preview):  # type: ignore[no-untyped-def]
        persisted.append(preview)

    monkeypatch.setattr(ImportOrchestrator, "_persist_preview", persist)

    preview = ImportPreviewResponse(
        operation_id=41,
        status=OperationStatus.READY,
        source_delivery_id=verified.manifest.delivery_id,
        bundle_sha256=verified.archive_sha256,
        bundle_size_bytes=verified.archive_size,
        signing_key_fingerprint=verified.signing_key_fingerprint,
        verified_at=datetime.now(UTC),
        artifacts=[],
    )

    orchestrator._persist_preview(41, preview)

    assert len(persisted) == 1
    enriched = persisted[0]
    assert enriched.source_harbor == "harbor.source.local"
    assert enriched.source_portal_version == "0.1.0"
    assert enriched.source_created_at == verified.manifest.created_at
    assert enriched.source_created_by == "source-operator"
    assert enriched.source_comment == "offline delivery"
    assert enriched.bundle_filename == "bundle.htp.tar.gz"
    assert enriched.intake_mode == "incoming"
    assert enriched.checksum_verified is True
    assert enriched.signature_verified is True
    assert enriched.schema_verified is True
    assert enriched.overwrite_allowed is True
    assert orchestrator._verified_preview is None


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
