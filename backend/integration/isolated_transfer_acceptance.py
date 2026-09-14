#!/usr/bin/env python3
"""Isolated SOURCE -> physical bundle -> TARGET acceptance driver.

This harness deliberately uses the production Skopeo/Helm, bundle, export and
import services. A metadata-only Harbor client stand-in is used on SOURCE because
the disposable fixture is a distribution registry, not a Harbor API server.
OCI mutations and verification always go through the production adapters.
"""

from __future__ import annotations

import asyncio
import copy
import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import PortalContour, Settings
from app.db.base import Base
from app.db.session import create_db_engine, create_session_factory
from app.domain.artifacts import ArtifactKind
from app.domain.bundle import ArtifactStatus, OperationStatus
from app.domain.imports import ImportPreviewState
from app.schemas.exports import ExportArtifactSelection
from app.services.bundle_package_service import BundlePackageService
from app.services.export_orchestrator import ExportOrchestrator
from app.services.harbor_client import HarborArtifact
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmOciService,
    HelmTargetState,
)
from app.services.import_helm_service import ImportHelmOciService
from app.services.import_orchestrator import ImportOrchestrationError, ImportOrchestrator
from app.services.operation_manager import OperationManager
from app.services.report_service import iter_operation_csv
from app.services.skopeo_service import ImageReference, SkopeoService, TargetState

REGISTRY_URL = os.environ.get("HTP_ACCEPTANCE_REGISTRY_URL", "").rstrip("/")
TRANSFER_DIR = Path(os.environ.get("HTP_ACCEPTANCE_TRANSFER_DIR", "/transfer"))
WORK_ROOT = Path(os.environ.get("HTP_ACCEPTANCE_WORK_ROOT", "/tmp/htp-isolated-acceptance"))
IMAGE_REPOSITORY = "team/images/app"
IMAGE_TAG = "1.0.0"
CHART_REPOSITORY = "team/charts"
CHART_NAME = "fixture-chart"
CHART_VERSION = "1.2.3"
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
DOCKER_MANIFEST_MEDIA_TYPE = "application/vnd.docker.distribution.manifest.v2+json"
HELM_CONTENT_MEDIA_TYPE = "application/vnd.cncf.helm.chart.content.v1.tar+gzip"


class MetadataHarborClient:
    def __init__(self, artifacts: dict[tuple[str, str, str], HarborArtifact]) -> None:
        self.artifacts = artifacts

    def get_artifact(self, project: str, repository: str, reference: str) -> HarborArtifact:
        return self.artifacts[(project, repository, reference)]

    def close(self) -> None:
        return None


def fail(message: str) -> None:
    raise AssertionError(message)


def assert_local_registry_url() -> None:
    parsed = urlsplit(REGISTRY_URL)
    if parsed.scheme != "http" or not parsed.hostname:
        fail("acceptance registry must be an explicit local HTTP fixture")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
        fail("acceptance registry URL must not contain path/query/userinfo")
    if parsed.hostname not in {
        "source-registry",
        "target-registry",
        "localhost",
        "127.0.0.1",
    }:
        fail("acceptance registry hostname is not an allowed disposable fixture")


def wait_for_registry(timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{REGISTRY_URL}/v2/", timeout=2) as response:
                if response.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"disposable registry did not become ready: {last_error}")


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def write_blob(layout: Path, payload: bytes) -> tuple[str, int]:
    digest_hex = hashlib.sha256(payload).hexdigest()
    path = layout / "blobs" / "sha256" / digest_hex
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return f"sha256:{digest_hex}", len(payload)


def fixture_layer(marker: bytes) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        info = tarfile.TarInfo("fixture.txt")
        info.size = len(marker)
        info.mode = 0o644
        info.mtime = 0
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        archive.addfile(info, io.BytesIO(marker))
    return stream.getvalue()


def write_oci_image_fixture(layout: Path, marker: bytes) -> str:
    if layout.exists():
        shutil.rmtree(layout)
    layout.mkdir(parents=True)
    layer_digest, layer_size = write_blob(layout, fixture_layer(marker))
    config = canonical_json(
        {
            "architecture": "amd64",
            "config": {},
            "created": "2026-01-01T00:00:00Z",
            "os": "linux",
            "rootfs": {"diff_ids": [layer_digest], "type": "layers"},
        }
    )
    config_digest, config_size = write_blob(layout, config)
    manifest = canonical_json(
        {
            "config": {
                "digest": config_digest,
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "size": config_size,
            },
            "layers": [
                {
                    "digest": layer_digest,
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "size": layer_size,
                }
            ],
            "mediaType": OCI_MANIFEST_MEDIA_TYPE,
            "schemaVersion": 2,
        }
    )
    manifest_digest, manifest_size = write_blob(layout, manifest)
    (layout / "index.json").write_bytes(
        canonical_json(
            {
                "manifests": [
                    {
                        "annotations": {"org.opencontainers.image.ref.name": "image"},
                        "digest": manifest_digest,
                        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
                        "size": manifest_size,
                    }
                ],
                "schemaVersion": 2,
            }
        )
    )
    (layout / "oci-layout").write_text(
        '{"imageLayoutVersion":"1.0.0"}\n',
        encoding="utf-8",
    )
    return manifest_digest


def registry_manifest_digest(repository: str, reference: str) -> str | None:
    accept = ", ".join(
        (
            OCI_MANIFEST_MEDIA_TYPE,
            DOCKER_MANIFEST_MEDIA_TYPE,
            HELM_CONTENT_MEDIA_TYPE,
        )
    )
    request = Request(
        f"{REGISTRY_URL}/v2/{repository}/manifests/{quote(reference, safe='')}",
        method="HEAD",
        headers={"Accept": accept},
    )
    try:
        with urlopen(request, timeout=5) as response:
            digest = response.headers.get("Docker-Content-Digest")
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"registry manifest lookup failed: HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"registry manifest lookup failed: {exc}") from exc
    if digest is None or not digest.startswith("sha256:") or len(digest) != 71:
        raise RuntimeError("registry returned invalid Docker-Content-Digest")
    return digest


def write_keys(root: Path) -> tuple[Path, Path, Path]:
    private = Ed25519PrivateKey.generate()
    private_path = root / "keys" / "source-private.pem"
    trusted_dir = root / "keys" / "trusted"
    public_path = trusted_dir / "source.pem"
    private_path.parent.mkdir(parents=True, exist_ok=True)
    trusted_dir.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(private_path, 0o600)
    public_path.write_bytes(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    os.chmod(public_path, 0o644)
    return private_path, trusted_dir, public_path


def settings_for(
    root: Path,
    contour: PortalContour,
    *,
    private_key: Path | None = None,
    trusted_dir: Path | None = None,
) -> Settings:
    data = root / "data"
    return Settings(
        _env_file=None,
        portal_contour=contour,
        harbor_url=REGISTRY_URL,
        harbor_verify_tls=False,
        database_url=f"sqlite:///{root / 'portal.db'}",
        operation_workspace_root=data / "tmp" / "operations",
        operation_disk_reserve_bytes=0,
        skopeo_payload_root=data,
        skopeo_temp_root=data / "tmp" / "skopeo",
        helm_workspace_root=data / "packages",
        helm_temp_root=data / "tmp" / "helm",
        bundle_payload_root=data,
        bundle_temp_root=data / "tmp" / "bundles",
        bundle_outgoing_root=data / "outgoing",
        bundle_extract_root=data / "verified",
        bundle_signing_private_key_file=(
            private_key or data / "keys" / "unused-private.pem"
        ),
        bundle_trusted_public_keys_dir=trusted_dir or data / "keys" / "trusted",
        import_discovery_root=data / "incoming",
        import_staging_root=data / "staged",
        import_receipt_root=data / "receipts" / "imports",
        import_max_upload_bytes=128 * 1024 * 1024,
        bundle_max_archive_bytes=128 * 1024 * 1024,
        bundle_max_extracted_bytes=256 * 1024 * 1024,
        bundle_max_member_count=4096,
        bundle_max_compression_ratio=1000,
    )


def environment(settings: Settings):  # type: ignore[no-untyped-def]
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    manager = OperationManager(factory, settings)
    return factory, manager


def helm_factory(settings: Settings):  # type: ignore[no-untyped-def]
    service_type = (
        ImportHelmOciService
        if settings.portal_contour is PortalContour.TARGET
        else HelmOciService
    )

    def factory(session):  # type: ignore[no-untyped-def]
        return service_type(
            session,
            settings,
            digest_resolver=lambda chart: registry_manifest_digest(
                chart.harbor_repository,
                chart.version,
            ),
        )

    return factory


def package_chart(settings: Settings) -> Path:
    chart_root = settings.helm_workspace_root / "seed-src" / CHART_NAME
    chart_root.mkdir(parents=True, exist_ok=True)
    (chart_root / "Chart.yaml").write_text(
        "\n".join(
            (
                "apiVersion: v2",
                f"name: {CHART_NAME}",
                f"version: {CHART_VERSION}",
                "description: isolated transfer acceptance fixture",
                "type: application",
                "",
            )
        ),
        encoding="utf-8",
    )
    destination = settings.helm_workspace_root / "seed-packages"
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        (
            settings.helm_binary,
            "package",
            str(chart_root),
            "--destination",
            str(destination),
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    package = destination / f"{CHART_NAME}-{CHART_VERSION}.tgz"
    if not package.is_file():
        fail("helm package did not create expected fixture")
    return package


def transfer_bundle() -> tuple[Path, Path, Path]:
    archives = sorted(TRANSFER_DIR.glob("*.htp.tar.gz"))
    if len(archives) != 1:
        fail(f"expected exactly one physical bundle, got {len(archives)}")
    archive = archives[0]
    sidecar = archive.with_name(archive.name + ".sha256")
    public_key = TRANSFER_DIR / "source-public.pem"
    if not sidecar.is_file() or not public_key.is_file():
        fail("physical transfer is missing sidecar or SOURCE public key")
    return archive, sidecar, public_key


def stage_incoming(settings: Settings, archive: Path, sidecar: Path) -> None:
    incoming = settings.import_discovery_root
    incoming.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive, incoming / archive.name)
    shutil.copy2(sidecar, incoming / sidecar.name)


async def discover_one(manager: OperationManager, orchestrator: ImportOrchestrator) -> int:
    ready = await orchestrator.discover_ready(
        actor_user_id=None,  # type: ignore[arg-type]
        actor_username="acceptance-operator",
    )
    if len(ready) != 1:
        fail(f"expected exactly one discovered bundle, got {len(ready)}")
    operation_id = ready[0].operation_id
    await manager.wait(operation_id)
    return operation_id


def tamper_signature(source: Path, destination: Path) -> None:
    with (
        tarfile.open(source, "r:gz") as archive_in,
        tarfile.open(destination, "w:gz") as archive_out,
    ):
        changed = False
        for member in archive_in.getmembers():
            info = copy.copy(member)
            if member.isfile():
                handle = archive_in.extractfile(member)
                if handle is None:
                    fail(f"could not read archive member: {member.name}")
                payload = handle.read()
                if member.name == "manifest.sig":
                    payload = b"0" * len(payload)
                    changed = True
                info.size = len(payload)
                archive_out.addfile(info, io.BytesIO(payload))
            else:
                archive_out.addfile(info)
        if not changed:
            fail("bundle does not contain manifest.sig")
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_name(destination.name + ".sha256").write_text(
        f"{digest}  {destination.name}\n",
        encoding="utf-8",
    )


async def source_phase() -> None:
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    WORK_ROOT.mkdir(parents=True)
    TRANSFER_DIR.mkdir(parents=True, exist_ok=True)
    private_key, trusted_dir, public_key = write_keys(WORK_ROOT)
    settings = settings_for(
        WORK_ROOT,
        PortalContour.SOURCE,
        private_key=private_key,
        trusted_dir=trusted_dir,
    )
    factory, manager = environment(settings)
    await manager.startup()
    try:
        with factory() as session:
            skopeo = SkopeoService(session, settings)
            image_layout = settings.skopeo_payload_root / "seed" / "image"
            image_digest = write_oci_image_fixture(
                image_layout,
                b"isolated-source-image-v1\n",
            )
            seeded_image = await skopeo.import_image(
                image_layout,
                ImageReference(IMAGE_REPOSITORY, IMAGE_TAG),
                expected_digest=image_digest,
            )
            if not seeded_image.verified or seeded_image.target_digest != image_digest:
                fail("SOURCE image seed digest mismatch")

            helm = helm_factory(settings)(session)
            chart_package = package_chart(settings)
            chart_ref = HelmChartReference(CHART_REPOSITORY, CHART_NAME, CHART_VERSION)
            seeded_chart = await helm.push_chart(chart_package, chart_ref)
            chart_digest = seeded_chart.target_digest

        metadata = MetadataHarborClient(
            {
                ("team", "images/app", IMAGE_TAG): HarborArtifact(
                    digest=image_digest,
                    type="IMAGE",
                    size=image_layout.stat().st_size if image_layout.is_file() else None,
                ),
                ("team", "charts/fixture-chart", CHART_VERSION): HarborArtifact(
                    digest=chart_digest,
                    type="CHART",
                    size=chart_package.stat().st_size,
                ),
            }
        )
        orchestrator = ExportOrchestrator(
            factory,
            settings,
            manager,
            harbor_client_factory=lambda: metadata,  # type: ignore[arg-type]
            skopeo_factory=lambda session: SkopeoService(session, settings),
            helm_factory=helm_factory(settings),
        )
        selections = (
            ExportArtifactSelection(
                kind=ArtifactKind.CONTAINER_IMAGE,
                project="team",
                repository="images/app",
                reference=IMAGE_TAG,
                digest=image_digest,
            ),
            ExportArtifactSelection(
                kind=ArtifactKind.HELM_CHART,
                project="team",
                repository="charts/fixture-chart",
                reference=CHART_VERSION,
                digest=chart_digest,
            ),
        )
        started = await orchestrator.start_export(
            selections,
            actor_user_id=None,  # type: ignore[arg-type]
            actor_username="source-operator",
            comment="isolated acceptance",
        )
        await manager.wait(started.operation_id)
        operation = manager.get_operation(started.operation_id)
        if operation is None or operation.status is not OperationStatus.COMPLETED:
            fail(f"SOURCE export did not complete: {operation}")
        metadata_result = orchestrator.bundle_metadata(started.operation_id)
        sidecar = metadata_result.archive_path.with_name(
            metadata_result.archive_path.name + ".sha256"
        )
        verified = BundlePackageService(settings).verify_bundle(
            metadata_result.archive_path,
            sidecar_path=sidecar,
        )
        if len(verified.manifest.artifacts) != 2:
            fail("SOURCE bundle does not contain both acceptance artifacts")

        transfer_archive = TRANSFER_DIR / metadata_result.archive_path.name
        transfer_sidecar = TRANSFER_DIR / sidecar.name
        transfer_public_key = TRANSFER_DIR / "source-public.pem"
        shutil.copy2(metadata_result.archive_path, transfer_archive)
        shutil.copy2(sidecar, transfer_sidecar)
        shutil.copy2(public_key, transfer_public_key)
        # These are physical-media staging copies, not the protected portal originals.
        # Bundle/signature integrity is cryptographic; none of these three files is secret.
        for path in (transfer_archive, transfer_sidecar, transfer_public_key):
            os.chmod(path, 0o644)

        print(
            f"SOURCE acceptance export OK: {started.delivery_id}; "
            f"image={image_digest}; chart={chart_digest}"
        )
    finally:
        await manager.shutdown()


async def target_phase() -> None:
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    WORK_ROOT.mkdir(parents=True)
    archive, sidecar, transferred_public_key = transfer_bundle()
    trusted_dir = WORK_ROOT / "keys" / "trusted"
    trusted_dir.mkdir(parents=True)
    shutil.copy2(transferred_public_key, trusted_dir / "source.pem")
    settings = settings_for(WORK_ROOT, PortalContour.TARGET, trusted_dir=trusted_dir)
    factory, manager = environment(settings)
    await manager.startup()
    try:
        package_service = BundlePackageService(settings)
        verified_transfer = package_service.verify_bundle(archive, sidecar_path=sidecar)
        image_descriptor = next(
            item
            for item in verified_transfer.manifest.artifacts
            if item.type == "container-image"
        )
        chart_descriptor = next(
            item
            for item in verified_transfer.manifest.artifacts
            if item.type == "helm-chart"
        )

        orchestrator = ImportOrchestrator(
            factory,
            settings,
            manager,
            skopeo_factory=lambda session: SkopeoService(session, settings),
            helm_factory=helm_factory(settings),
        )

        stage_incoming(settings, archive, sidecar)
        operation_id = await discover_one(manager, orchestrator)
        preview = orchestrator.preview(operation_id)
        if [item.classification for item in preview.artifacts] != [
            ImportPreviewState.NEW,
            ImportPreviewState.NEW,
        ]:
            fail(f"initial TARGET preview is not NEW/NEW: {preview.artifacts}")
        await orchestrator.start_import(
            operation_id,
            actor_username="target-operator",
            overwrite_conflicts=False,
        )
        await manager.wait(operation_id)
        operation = manager.get_operation(operation_id)
        if operation is None or operation.status is not OperationStatus.COMPLETED:
            fail(f"TARGET import did not complete: {operation}")
        if any(item.status is not ArtifactStatus.VERIFIED for item in operation.artifacts):
            fail("TARGET import artifacts are not all VERIFIED")

        receipt = orchestrator.receipt(operation_id)
        if (
            receipt.result != "COMPLETED"
            or receipt.source_delivery_id != verified_transfer.manifest.delivery_id
        ):
            fail("TARGET immutable receipt identity/result mismatch")
        receipt_path = settings.import_receipt_root / f"import-{operation_id}.json"
        if not receipt_path.is_file() or (receipt_path.stat().st_mode & 0o777) != 0o440:
            fail("TARGET immutable receipt file is missing or has wrong mode")
        report_text = b"".join(iter_operation_csv(operation)).decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(report_text)))
        if len(rows) != 2 or {row["artifact_result"] for row in rows} != {"VERIFIED"}:
            fail("TARGET CSV report does not contain verified artifact outcomes")
        if verified_transfer.manifest.delivery_id not in report_text:
            fail("TARGET CSV report is missing source delivery id")

        with factory() as session:
            skopeo = SkopeoService(session, settings)
            image_state = await skopeo.inspect_target(
                ImageReference(image_descriptor.repository, image_descriptor.reference),
                expected_digest=image_descriptor.source_digest,
            )
            if image_state.state is not TargetState.SAME_DIGEST:
                fail(f"TARGET image digest mismatch: {image_state}")
            helm = helm_factory(settings)(session)
            chart_state = await helm.inspect_target(
                HelmChartReference(
                    chart_descriptor.repository,
                    chart_descriptor.name,
                    chart_descriptor.version,
                ),
                expected_digest=chart_descriptor.source_digest,
            )
            if chart_state.state is not HelmTargetState.SAME_DIGEST:
                fail(f"TARGET Helm digest mismatch: {chart_state}")

        stage_incoming(settings, archive, sidecar)
        replay_id = await discover_one(manager, orchestrator)
        replay_preview = orchestrator.preview(replay_id)
        if any(
            item.classification is not ImportPreviewState.SAME
            for item in replay_preview.artifacts
        ):
            fail("replay preview is not idempotent SAME")
        await orchestrator.start_import(
            replay_id,
            actor_username="target-operator",
            overwrite_conflicts=False,
        )
        await manager.wait(replay_id)
        replay = manager.get_operation(replay_id)
        if replay is None or replay.status is not OperationStatus.COMPLETED:
            fail("replay import did not complete")
        if any(item.status is not ArtifactStatus.SKIPPED for item in replay.artifacts):
            fail("replay did not SKIP identical artifacts")

        with factory() as session:
            skopeo = SkopeoService(session, settings)
            conflict_layout = settings.skopeo_payload_root / "conflict" / "image"
            conflict_digest = write_oci_image_fixture(
                conflict_layout,
                b"isolated-target-conflict-v2\n",
            )
            if conflict_digest == image_descriptor.source_digest:
                fail("conflict fixture unexpectedly matches source digest")
            conflict_import = await skopeo.import_image(
                conflict_layout,
                ImageReference(image_descriptor.repository, image_descriptor.reference),
                expected_digest=conflict_digest,
            )
            if conflict_import.target_digest != conflict_digest:
                fail("failed to seed conflicting TARGET image")

        stage_incoming(settings, archive, sidecar)
        conflict_id = await discover_one(manager, orchestrator)
        conflict_preview = orchestrator.preview(conflict_id)
        if conflict_preview.artifacts[0].classification is not ImportPreviewState.CONFLICT:
            fail("conflict fixture was not classified as CONFLICT")
        try:
            await orchestrator.start_import(
                conflict_id,
                actor_username="target-operator",
                overwrite_conflicts=False,
            )
        except ImportOrchestrationError as exc:
            if exc.code != "import_conflict_blocked":
                raise
        else:
            fail("default conflict policy unexpectedly allowed overwrite")

        with factory() as session:
            skopeo = SkopeoService(session, settings)
            still_conflict = await skopeo.inspect_target(
                ImageReference(image_descriptor.repository, image_descriptor.reference),
                expected_digest=conflict_digest,
            )
            if still_conflict.state is not TargetState.SAME_DIGEST:
                fail("blocked conflict mutated TARGET image")

        with tempfile.TemporaryDirectory(prefix="htp-tamper-") as temp_name:
            tampered = Path(temp_name) / "tampered.htp.tar.gz"
            tamper_signature(archive, tampered)
            tampered_sidecar = tampered.with_name(tampered.name + ".sha256")
            stage_incoming(settings, tampered, tampered_sidecar)
            tampered_id = await discover_one(manager, orchestrator)
            tampered_operation = manager.get_operation(tampered_id)
            if (
                tampered_operation is None
                or tampered_operation.status is not OperationStatus.REJECTED
            ):
                fail(f"tampered signed bundle was not REJECTED: {tampered_operation}")

        with factory() as session:
            skopeo = SkopeoService(session, settings)
            unchanged = await skopeo.inspect_target(
                ImageReference(image_descriptor.repository, image_descriptor.reference),
                expected_digest=conflict_digest,
            )
            if unchanged.state is not TargetState.SAME_DIGEST:
                fail("tampered bundle mutated TARGET registry")

        print(
            f"TARGET acceptance import OK: {verified_transfer.manifest.delivery_id}; "
            "digest verification, receipt/report, replay, conflict and tamper assertions passed"
        )
    finally:
        await manager.shutdown()


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"source", "target"}:
        print("usage: isolated_transfer_acceptance.py source|target", file=sys.stderr)
        return 2
    assert_local_registry_url()
    wait_for_registry()
    phase = sys.argv[1]
    asyncio.run(source_phase() if phase == "source" else target_phase())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
