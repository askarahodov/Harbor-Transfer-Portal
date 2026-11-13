from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.bundle import BundleManifest, BundleSource, ContainerImageArtifact, OperationStatus
from app.schemas.imports import ImportPreviewResponse
from app.services.bundle_package_service import BundleVerificationResult
from app.services.import_orchestrator import ImportOrchestrator
from app.services.import_preview_projection import (
    ImportPreviewProjectionOrchestrator,
    _CapturingBundlePackageService,
    _preview_capture,
)


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


def test_persist_preview_enriches_verified_manifest_and_policy(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    verified = _verified_result()
    orchestrator = object.__new__(ImportPreviewProjectionOrchestrator)
    orchestrator.settings = SimpleNamespace(import_allow_overwrite=True)

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

    token = _preview_capture.set([verified])
    try:
        orchestrator._persist_preview(41, preview)
        assert _preview_capture.get() == []
    finally:
        _preview_capture.reset(token)

    assert len(persisted) == 1
    enriched = persisted[0]
    assert enriched.source_harbor == "harbor.source.local"
    assert enriched.source_registry_type is None
    assert enriched.source_registry is None
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


@pytest.mark.asyncio
async def test_parallel_preview_workers_keep_verified_results_isolated(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    first = _verified_result()
    second = replace(
        first,
        archive_sha256="e" * 64,
        manifest=first.manifest.model_copy(
            update={"source": first.manifest.source.model_copy(update={"harbor": "other.local"})}
        ),
    )
    results = {1: first, 2: second}
    orchestrator = object.__new__(ImportPreviewProjectionOrchestrator)
    orchestrator.settings = SimpleNamespace(import_allow_overwrite=False)
    orchestrator._bundle_paths = lambda operation_id: (Path(str(operation_id)), None)
    orchestrator._get_import_operation = lambda operation_id: SimpleNamespace(
        bundle_sha256=results[operation_id].archive_sha256,
        bundle_filename=f"{operation_id}.htp.tar.gz",
        import_intake_mode="incoming",
    )
    orchestrator._assert_operation_profile = lambda _operation: SimpleNamespace(
        id="profile", registry_type="harbor"
    )
    orchestrator.persistence = SimpleNamespace(persist_verified_signer=lambda *_args: None)

    class Package:
        def verify_bundle(self, archive, **_kwargs):  # type: ignore[no-untyped-def]
            return results[int(archive.name)]

    orchestrator.package_factory = lambda: _CapturingBundlePackageService(
        Package(), orchestrator._capture_verified_preview
    )
    both_classifying = asyncio.Event()
    entered = 0

    async def classify(_artifacts, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal entered
        entered += 1
        if entered == 2:
            both_classifying.set()
        await asyncio.wait_for(both_classifying.wait(), timeout=5)
        return []

    orchestrator.artifact_classifier = SimpleNamespace(classify=classify)
    persisted = {}
    monkeypatch.setattr(
        ImportOrchestrator,
        "_persist_preview",
        lambda _self, operation_id, preview: persisted.update({operation_id: preview}),
    )
    context = SimpleNamespace(transition=lambda _status: None)
    await asyncio.gather(
        orchestrator._preview_worker(context, 1),
        orchestrator._preview_worker(context, 2),
    )
    assert persisted[1].source_harbor == "harbor.source.local"
    assert persisted[2].source_harbor == "other.local"
    assert _preview_capture.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_preview_capture_is_reset_on_failure_or_cancellation(monkeypatch, error) -> None:  # type: ignore[no-untyped-def]
    orchestrator = object.__new__(ImportPreviewProjectionOrchestrator)

    async def fail(_self, _context, _operation_id):  # type: ignore[no-untyped-def]
        orchestrator._capture_verified_preview(_verified_result())
        raise error()

    monkeypatch.setattr(ImportOrchestrator, "_preview_worker", fail)
    with pytest.raises(error):
        await orchestrator._preview_worker(SimpleNamespace(), 1)
    assert _preview_capture.get() is None
    orchestrator._capture_verified_preview(_verified_result())
    assert _preview_capture.get() is None


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
    assert preview.source_registry_type is None
    assert preview.source_registry is None
    assert preview.checksum_verified is False
    assert preview.signature_verified is False
    assert preview.schema_verified is False
    assert preview.overwrite_allowed is False


def test_verified_nexus_source_provenance_is_shown_not_mislabeled_harbor(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    verified = _verified_result()
    nexus_source = BundleSource(
        contour="SOURCE",
        registry_type="nexus",
        registry="https://docker-nexus.example.test",
        portal_version="0.1.0",
    )
    verified = BundleVerificationResult(
        manifest=BundleManifest.model_validate({
            **verified.manifest.model_dump(),
            "schema_version": "1.1",
            "source": nexus_source.model_dump(),
        }),
        archive_sha256=verified.archive_sha256,
        archive_size=verified.archive_size,
        signing_key_fingerprint=verified.signing_key_fingerprint,
    )
    orchestrator = object.__new__(ImportPreviewProjectionOrchestrator)
    orchestrator.settings = SimpleNamespace(import_allow_overwrite=False)
    monkeypatch.setattr(
        orchestrator, "_get_import_operation",
        lambda _id: SimpleNamespace(
            bundle_filename="nexus.htp.tar.gz",
            import_intake_mode="incoming",
        ),
    )
    persisted: list[ImportPreviewResponse] = []
    monkeypatch.setattr(
        ImportOrchestrator, "_persist_preview",
        lambda _self, _id, preview: persisted.append(preview),
    )
    preview = ImportPreviewResponse(
        operation_id=42,
        status=OperationStatus.READY,
        source_delivery_id=verified.manifest.delivery_id,
        bundle_sha256=verified.archive_sha256,
        bundle_size_bytes=verified.archive_size,
        signing_key_fingerprint=verified.signing_key_fingerprint,
        verified_at=datetime.now(UTC),
        artifacts=[],
    )
    token = _preview_capture.set([verified])
    try:
        orchestrator._persist_preview(42, preview)
    finally:
        _preview_capture.reset(token)
    [enriched] = persisted
    assert enriched.source_harbor is None
    assert enriched.source_registry_type == "nexus"
    assert enriched.source_registry == "https://docker-nexus.example.test"
    assert enriched.checksum_verified is True
    assert enriched.signature_verified is True
    assert enriched.schema_verified is True
    assert enriched.overwrite_allowed is False
