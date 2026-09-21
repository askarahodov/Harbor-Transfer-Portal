#!/usr/bin/env python3
"""Isolated SOURCE -> physical bundle -> TARGET acceptance driver.

The harness uses production Skopeo/Helm, bundle, export, destination-plan and
import services. Disposable fixtures are plain distribution registries, so Harbor
project/access metadata is represented by a deterministic local validator while
all OCI mutations and digest/content verification use the production adapters.
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
from app.db.models import Operation as OperationModel
from app.db.session import create_db_engine, create_session_factory
from app.domain.artifacts import ArtifactKind
from app.domain.bundle import ArtifactStatus, OperationStatus
from app.domain.imports import ImportPreviewState
from app.schemas.exports import ExportArtifactSelection
from app.schemas.imports import (
    ImportArtifactDestinationOverride,
    ImportDestinationPlanRequest,
)
from app.services.bundle_package_service import BundlePackageService
from app.services.export_orchestrator import ExportOrchestrator
from app.services.harbor_client import HarborArtifact
from app.services.harbor_destination_validator import DestinationCapability
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmOciService,
    HelmTargetState,
)
from app.services.import_destination_plan import ImportDestinationPlanOrchestrator
from app.services.import_helm_service import ImportHelmOciService
from app.services.import_orchestrator import ImportOrchestrationError
from app.services.key_management import KeyManagementService
from app.services.operation_manager import OperationManager
from app.services.report_service import iter_operation_csv
from app.services.skopeo_service import ImageReference, SkopeoService, TargetState
from app.services.source_trust_package import SourceTrustPackageService

REGISTRY_URL = os.environ.get("HTP_ACCEPTANCE_REGISTRY_URL", "").rstrip("/")
TRANSFER_DIR = Path(os.environ.get("HTP_ACCEPTANCE_TRANSFER_DIR", "/transfer"))
WORK_ROOT = Path(os.environ.get("HTP_ACCEPTANCE_WORK_ROOT", "/tmp/htp-isolated-acceptance"))

IMAGE_REPOSITORY = "team/images/app"
IMAGE_TAG = "1.0.0"
SECOND_IMAGE_REPOSITORY = "team/images/worker"
SECOND_IMAGE_TAG = "2.0.0"
CHART_REPOSITORY = "team/charts"
CHART_NAME = "fixture-chart"
CHART_VERSION = "1.2.3"

IMAGE_TARGET_PROJECT = "docker-prod"
IMAGE_OVERRIDE_PROJECT = "docker-special"
HELM_TARGET_PROJECT = "helm-prod"
ALT_IMAGE_TARGET_PROJECT = "docker-alt"
ALT_IMAGE_OVERRIDE_PROJECT = "docker-alt-special"
ALT_HELM_TARGET_PROJECT = "helm-alt"
TARGET_ACTOR = "acceptance-operator"

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


class AcceptanceDestinationValidator:
    """Harbor project/access stand-in for a plain disposable distribution registry."""

    def __init__(self) -> None:
        registry_host = urlsplit(REGISTRY_URL).netloc
        if not registry_host:
            raise ValueError("acceptance TARGET registry host is empty")
        self.registry_host = registry_host
        self.missing_projects: set[str] = set()
        self.denied_repositories: set[str] = set()

    async def validate(self, project: str, repository: str) -> DestinationCapability:
        if project in self.missing_projects:
            return DestinationCapability(
                False,
                False,
                "import_destination_project_missing",
                f"acceptance project '{project}' is missing",
            )
        if repository in self.denied_repositories:
            return DestinationCapability(
                True,
                False,
                "import_destination_write_forbidden",
                f"acceptance repository '{repository}' denies push",
            )
        return DestinationCapability(True, True)

    def reset(self) -> None:
        self.missing_projects.clear()
        self.denied_repositories.clear()


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
            private_key or data / "keys" / "source-signing-private.pem"
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
    archives = sorted(
        path
        for path in TRANSFER_DIR.glob("*.htp.tar.gz")
        if not path.name.endswith(".htp-trust.tar.gz")
    )
    if len(archives) != 1:
        fail(f"expected exactly one physical bundle, got {len(archives)}")
    archive = archives[0]
    sidecar = archive.with_name(archive.name + ".sha256")
    trust_packages = sorted(TRANSFER_DIR.glob("*.htp-trust.tar.gz"))
    if len(trust_packages) != 1:
        fail(f"expected exactly one SOURCE trust package, got {len(trust_packages)}")
    trust_package = trust_packages[0]
    if not sidecar.is_file():
        fail("physical transfer is missing bundle sidecar")
    return archive, sidecar, trust_package


def stage_incoming(settings: Settings, archive: Path, sidecar: Path) -> None:
    incoming = settings.import_discovery_root
    incoming.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive, incoming / archive.name)
    shutil.copy2(sidecar, incoming / sidecar.name)


async def discover_one(
    manager: OperationManager,
    orchestrator: ImportDestinationPlanOrchestrator,
) -> int:
    ready = await orchestrator.discover_ready(
        actor_user_id=None,  # type: ignore[arg-type]
        actor_username=TARGET_ACTOR,
    )
    if len(ready) != 1:
        fail(f"expected exactly one discovered bundle, got {len(ready)}")
    operation_id = ready[0].operation_id
    await manager.wait(operation_id)
    return operation_id


def mapping_for_preview(
    preview,  # type: ignore[no-untyped-def]
    *,
    image_project: str,
    image_override_project: str,
    helm_project: str,
) -> ImportDestinationPlanRequest:
    second = next(
        item for item in preview.artifacts if item.repository == SECOND_IMAGE_REPOSITORY
    )
    return ImportDestinationPlanRequest(
        container_image_project=image_project,
        helm_chart_project=helm_project,
        artifact_overrides=[
            ImportArtifactDestinationOverride(
                index=second.index,
                target_project=image_override_project,
            )
        ],
    )


def assert_no_sensitive_text(label: str, value: object) -> None:
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )
    lowered = text.lower()
    for marker in (
        "password",
        "credential",
        "private_key",
        "private-key",
        "-----begin private key-----",
    ):
        if marker in lowered:
            fail(f"{label} contains sensitive marker: {marker}")


def assert_bundle_has_no_private_material(archive: Path) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            lowered_name = member.name.lower()
            if (
                "private" in lowered_name
                or "credential" in lowered_name
                or "password" in lowered_name
            ):
                fail(f"bundle contains sensitive-looking member name: {member.name}")
            if not member.isfile():
                continue
            handle = bundle.extractfile(member)
            if handle is None:
                fail(f"could not inspect archive member: {member.name}")
            if b"-----BEGIN PRIVATE KEY-----" in handle.read():
                fail(f"bundle contains private key material in {member.name}")


def planned_by_source(plan):  # type: ignore[no-untyped-def]
    return {item.source_repository: item for item in plan.artifacts}


def receipt_by_source(receipt):  # type: ignore[no-untyped-def]
    return {item.repository: item for item in receipt.artifacts}


def target_coordinates(
    *,
    image_project: str,
    image_override_project: str,
    helm_project: str,
) -> dict[str, tuple[str, str]]:
    return {
        IMAGE_REPOSITORY: (f"{image_project}/images/app", IMAGE_TAG),
        SECOND_IMAGE_REPOSITORY: (
            f"{image_override_project}/images/worker",
            SECOND_IMAGE_TAG,
        ),
        CHART_REPOSITORY: (
            f"{helm_project}/charts/{CHART_NAME}",
            CHART_VERSION,
        ),
    }


def assert_registry_absent(coordinates: dict[str, tuple[str, str]]) -> None:
    for source_repository, (repository, reference) in coordinates.items():
        if registry_manifest_digest(repository, reference) is not None:
            fail(
                "TARGET mutated before import for "
                f"{source_repository} -> {repository}:{reference}"
            )


def assert_plan_destinations(
    plan,  # type: ignore[no-untyped-def]
    *,
    image_project: str,
    image_override_project: str,
    helm_project: str,
    expected_state: ImportPreviewState,
) -> None:
    if len(plan.artifacts) != 3 or not plan.valid:
        fail(f"destination plan is not a valid three-artifact plan: {plan}")
    if any(item.classification is not expected_state for item in plan.artifacts):
        fail(f"destination plan state is not uniformly {expected_state}: {plan.artifacts}")

    host = urlsplit(REGISTRY_URL).netloc
    by_source = planned_by_source(plan)
    expected = {
        IMAGE_REPOSITORY: (
            f"{image_project}/images/app",
            f"{host}/{image_project}/images/app:{IMAGE_TAG}",
        ),
        SECOND_IMAGE_REPOSITORY: (
            f"{image_override_project}/images/worker",
            f"{host}/{image_override_project}/images/worker:{SECOND_IMAGE_TAG}",
        ),
        CHART_REPOSITORY: (
            f"{helm_project}/charts",
            f"oci://{host}/{helm_project}/charts/{CHART_NAME}:{CHART_VERSION}",
        ),
    }
    for source_repository, (target_repository, final_reference) in expected.items():
        item = by_source.get(source_repository)
        if item is None:
            fail(f"destination plan is missing source artifact {source_repository}")
        if item.target_repository != target_repository or item.final_reference != final_reference:
            fail(
                f"wrong mapped target for {source_repository}: "
                f"{item.target_repository} / {item.final_reference}"
            )


def assert_receipt_destinations(receipt, plan) -> None:  # type: ignore[no-untyped-def]
    if (
        receipt.destination_plan_id != plan.plan_id
        or receipt.destination_plan_hash != plan.plan_hash
    ):
        fail("receipt is not bound to the executed destination plan")
    planned = planned_by_source(plan)
    actual = receipt_by_source(receipt)
    if set(actual) != set(planned):
        fail("receipt source artifact set does not match destination plan")
    for source_repository, planned_item in planned.items():
        item = actual[source_repository]
        if (
            item.artifact_type != planned_item.artifact_type
            or item.target_repository != planned_item.target_repository
            or item.final_reference != planned_item.final_reference
        ):
            fail(f"receipt destination mismatch for {source_repository}")


def assert_import_policy_has_no_secrets(factory, operation_id: int) -> None:  # type: ignore[no-untyped-def]
    with factory() as session:
        operation = session.get(OperationModel, operation_id)
        if operation is None or operation.import_policy_json is None:
            fail("destination mapping policy was not persisted")
        assert_no_sensitive_text("destination mapping policy", operation.import_policy_json)


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
    settings = settings_for(WORK_ROOT, PortalContour.SOURCE)
    generated_identity = KeyManagementService(settings).generate_signing_private_key()
    trust_package = SourceTrustPackageService(settings).build()
    if trust_package.fingerprint != generated_identity.fingerprint:
        fail("SOURCE trust package fingerprint does not match generated identity")
    factory, manager = environment(settings)
    await manager.startup()
    try:
        with factory() as session:
            skopeo = SkopeoService(session, settings)
            image_layout = settings.skopeo_payload_root / "seed" / "image-app"
            image_digest = write_oci_image_fixture(
                image_layout,
                b"isolated-source-image-app-v1\n",
            )
            seeded_image = await skopeo.import_image(
                image_layout,
                ImageReference(IMAGE_REPOSITORY, IMAGE_TAG),
                expected_digest=image_digest,
            )
            if not seeded_image.verified or seeded_image.target_digest != image_digest:
                fail("SOURCE primary image seed digest mismatch")

            second_layout = settings.skopeo_payload_root / "seed" / "image-worker"
            second_digest = write_oci_image_fixture(
                second_layout,
                b"isolated-source-image-worker-v2\n",
            )
            seeded_second = await skopeo.import_image(
                second_layout,
                ImageReference(SECOND_IMAGE_REPOSITORY, SECOND_IMAGE_TAG),
                expected_digest=second_digest,
            )
            if not seeded_second.verified or seeded_second.target_digest != second_digest:
                fail("SOURCE second image seed digest mismatch")

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
                    size=None,
                ),
                ("team", "images/worker", SECOND_IMAGE_TAG): HarborArtifact(
                    digest=second_digest,
                    type="IMAGE",
                    size=None,
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
                kind=ArtifactKind.CONTAINER_IMAGE,
                project="team",
                repository="images/worker",
                reference=SECOND_IMAGE_TAG,
                digest=second_digest,
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
            comment="isolated mixed destination acceptance",
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
        if len(verified.manifest.artifacts) != 3:
            fail("SOURCE bundle does not contain two images and one Helm chart")
        if [item.type for item in verified.manifest.artifacts].count("container-image") != 2:
            fail("SOURCE bundle does not contain exactly two image descriptors")
        if [item.type for item in verified.manifest.artifacts].count("helm-chart") != 1:
            fail("SOURCE bundle does not contain exactly one Helm descriptor")
        assert_bundle_has_no_private_material(metadata_result.archive_path)

        transfer_archive = TRANSFER_DIR / metadata_result.archive_path.name
        transfer_sidecar = TRANSFER_DIR / sidecar.name
        transfer_trust_package = TRANSFER_DIR / trust_package.filename
        shutil.copy2(metadata_result.archive_path, transfer_archive)
        shutil.copy2(sidecar, transfer_sidecar)
        transfer_trust_package.write_bytes(trust_package.payload)
        for path in (transfer_archive, transfer_sidecar, transfer_trust_package):
            os.chmod(path, 0o644)

        print(
            f"SOURCE acceptance export OK: {started.delivery_id}; "
            f"images={image_digest},{second_digest}; chart={chart_digest}"
        )
    finally:
        await manager.shutdown()


async def execute_planned_import(
    orchestrator: ImportDestinationPlanOrchestrator,
    manager: OperationManager,
    operation_id: int,
    plan,
) -> None:  # type: ignore[no-untyped-def]
    await orchestrator.start_import(
        operation_id,
        actor_username=TARGET_ACTOR,
        overwrite_conflicts=False,
        destination_plan_id=plan.plan_id,
    )
    await manager.wait(operation_id)
    operation = manager.get_operation(operation_id)
    if operation is None or operation.status is not OperationStatus.COMPLETED:
        fail(f"TARGET import did not complete: {operation}")


async def verify_target_content(
    factory,  # type: ignore[no-untyped-def]
    settings: Settings,
    plan,
    descriptors: dict[str, object],
) -> None:  # type: ignore[no-untyped-def]
    planned = planned_by_source(plan)
    with factory() as session:
        skopeo = SkopeoService(session, settings)
        for source_repository in (IMAGE_REPOSITORY, SECOND_IMAGE_REPOSITORY):
            descriptor = descriptors[source_repository]
            item = planned[source_repository]
            state = await skopeo.inspect_target(
                ImageReference(item.target_repository, item.reference),
                expected_digest=descriptor.source_digest,
            )
            if state.state is not TargetState.SAME_DIGEST:
                fail(f"TARGET image digest mismatch for {source_repository}: {state}")

        chart_descriptor = descriptors[CHART_REPOSITORY]
        chart_item = planned[CHART_REPOSITORY]
        helm = helm_factory(settings)(session)
        chart_ref = HelmChartReference(
            chart_item.target_repository,
            chart_item.name,
            chart_item.version,
        )
        chart_state = await helm.inspect_target(
            chart_ref,
            expected_digest=chart_descriptor.source_digest,
        )
        if chart_state.state is not HelmTargetState.SAME_DIGEST:
            fail(f"TARGET Helm digest mismatch: {chart_state}")
        pull_root = settings.helm_workspace_root / "acceptance-verify" / plan.plan_id[:12]
        pulled = await helm.pull_chart(chart_ref, pull_root)
        if pulled.package.name != CHART_NAME or pulled.package.version != CHART_VERSION:
            fail("TARGET Helm package identity mismatch")
        if pulled.package.sha256 != chart_descriptor.payload_sha256:
            fail("TARGET Helm package content differs from signed bundle payload")


async def target_phase() -> None:
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    WORK_ROOT.mkdir(parents=True)
    archive, sidecar, transferred_trust_package = transfer_bundle()
    settings = settings_for(WORK_ROOT, PortalContour.TARGET)
    trust_mutation = SourceTrustPackageService(settings).import_package(
        transferred_trust_package.read_bytes()
    )
    if trust_mutation.action != "added":
        fail(f"fresh TARGET trust bootstrap was not create-only: {trust_mutation.action}")
    factory, manager = environment(settings)
    await manager.startup()
    try:
        package_service = BundlePackageService(settings)
        verified_transfer = package_service.verify_bundle(
            archive,
            sidecar_path=sidecar,
            extract_to=settings.bundle_extract_root / "acceptance-inspection",
        )
        if len(verified_transfer.manifest.artifacts) != 3:
            fail("physical bundle is not the expected mixed three-artifact bundle")
        assert_bundle_has_no_private_material(archive)
        descriptors = {
            item.repository: item for item in verified_transfer.manifest.artifacts
        }
        if set(descriptors) != {
            IMAGE_REPOSITORY,
            SECOND_IMAGE_REPOSITORY,
            CHART_REPOSITORY,
        }:
            fail(f"unexpected mixed bundle source repositories: {set(descriptors)}")

        validator = AcceptanceDestinationValidator()
        orchestrator = ImportDestinationPlanOrchestrator(
            factory,
            settings,
            manager,
            skopeo_factory=lambda session: SkopeoService(session, settings),
            helm_factory=helm_factory(settings),
            destination_validator_factory=lambda _session: validator,
        )

        # Initial mixed import: two images use the image default except for a
        # per-artifact override; Helm uses a separate project.
        stage_incoming(settings, archive, sidecar)
        operation_id = await discover_one(manager, orchestrator)
        preview = orchestrator.preview(operation_id)
        mapping = mapping_for_preview(
            preview,
            image_project=IMAGE_TARGET_PROJECT,
            image_override_project=IMAGE_OVERRIDE_PROJECT,
            helm_project=HELM_TARGET_PROJECT,
        )
        assert_no_sensitive_text("destination mapping request", mapping.model_dump(mode="json"))
        plan = await orchestrator.build_destination_plan(
            operation_id,
            mapping,
            actor_username=TARGET_ACTOR,
        )
        assert_plan_destinations(
            plan,
            image_project=IMAGE_TARGET_PROJECT,
            image_override_project=IMAGE_OVERRIDE_PROJECT,
            helm_project=HELM_TARGET_PROJECT,
            expected_state=ImportPreviewState.NEW,
        )
        initial_coordinates = target_coordinates(
            image_project=IMAGE_TARGET_PROJECT,
            image_override_project=IMAGE_OVERRIDE_PROJECT,
            helm_project=HELM_TARGET_PROJECT,
        )
        assert_registry_absent(initial_coordinates)
        assert_import_policy_has_no_secrets(factory, operation_id)

        await execute_planned_import(orchestrator, manager, operation_id, plan)
        operation = manager.get_operation(operation_id)
        if operation is None or any(
            item.status is not ArtifactStatus.VERIFIED for item in operation.artifacts
        ):
            fail("TARGET mixed import artifacts are not all VERIFIED")

        receipt = orchestrator.receipt(operation_id)
        if (
            receipt.result != "COMPLETED"
            or receipt.source_delivery_id != verified_transfer.manifest.delivery_id
        ):
            fail("TARGET immutable receipt identity/result mismatch")
        assert_receipt_destinations(receipt, plan)
        assert_no_sensitive_text("import receipt", receipt.model_dump(mode="json"))
        receipt_path = settings.import_receipt_root / f"import-{operation_id}.json"
        if not receipt_path.is_file() or (receipt_path.stat().st_mode & 0o777) != 0o440:
            fail("TARGET immutable receipt file is missing or has wrong mode")

        report_text = b"".join(iter_operation_csv(operation)).decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(report_text)))
        if len(rows) != 3 or {row["artifact_result"] for row in rows} != {"VERIFIED"}:
            fail("TARGET CSV report does not contain all verified mixed artifact outcomes")
        if verified_transfer.manifest.delivery_id not in report_text:
            fail("TARGET CSV report is missing source delivery id")

        await verify_target_content(factory, settings, plan, descriptors)
        # Mapping must not accidentally mutate SOURCE paths in the TARGET registry.
        if registry_manifest_digest(IMAGE_REPOSITORY, IMAGE_TAG) is not None:
            fail("mapped image import leaked into original SOURCE repository")
        if registry_manifest_digest(SECOND_IMAGE_REPOSITORY, SECOND_IMAGE_TAG) is not None:
            fail("mapped second image import leaked into original SOURCE repository")
        if registry_manifest_digest(f"{CHART_REPOSITORY}/{CHART_NAME}", CHART_VERSION) is not None:
            fail("mapped Helm import leaked into original SOURCE repository")

        # Replay the same physical bundle with the same mapping. The resolved plan
        # identity remains stable and all three actual TARGET references are SAME.
        stage_incoming(settings, archive, sidecar)
        replay_id = await discover_one(manager, orchestrator)
        replay_preview = orchestrator.preview(replay_id)
        replay_mapping = mapping_for_preview(
            replay_preview,
            image_project=IMAGE_TARGET_PROJECT,
            image_override_project=IMAGE_OVERRIDE_PROJECT,
            helm_project=HELM_TARGET_PROJECT,
        )
        replay_plan = await orchestrator.build_destination_plan(
            replay_id,
            replay_mapping,
            actor_username=TARGET_ACTOR,
        )
        assert_plan_destinations(
            replay_plan,
            image_project=IMAGE_TARGET_PROJECT,
            image_override_project=IMAGE_OVERRIDE_PROJECT,
            helm_project=HELM_TARGET_PROJECT,
            expected_state=ImportPreviewState.SAME,
        )
        if replay_plan.plan_id != plan.plan_id:
            fail("same bundle + same resolved mapping did not keep stable plan_id")
        await execute_planned_import(orchestrator, manager, replay_id, replay_plan)
        replay = manager.get_operation(replay_id)
        if replay is None or any(
            item.status is not ArtifactStatus.SKIPPED for item in replay.artifacts
        ):
            fail("replay did not SKIP all identical mapped artifacts")

        # The same bundle with another mapping must create a distinct plan and
        # mutate only the alternate final references.
        stage_incoming(settings, archive, sidecar)
        alternate_id = await discover_one(manager, orchestrator)
        alternate_preview = orchestrator.preview(alternate_id)
        alternate_mapping = mapping_for_preview(
            alternate_preview,
            image_project=ALT_IMAGE_TARGET_PROJECT,
            image_override_project=ALT_IMAGE_OVERRIDE_PROJECT,
            helm_project=ALT_HELM_TARGET_PROJECT,
        )
        alternate_plan = await orchestrator.build_destination_plan(
            alternate_id,
            alternate_mapping,
            actor_username=TARGET_ACTOR,
        )
        assert_plan_destinations(
            alternate_plan,
            image_project=ALT_IMAGE_TARGET_PROJECT,
            image_override_project=ALT_IMAGE_OVERRIDE_PROJECT,
            helm_project=ALT_HELM_TARGET_PROJECT,
            expected_state=ImportPreviewState.NEW,
        )
        if alternate_plan.plan_id == plan.plan_id:
            fail("different resolved mapping reused the previous destination plan id")
        alternate_coordinates = target_coordinates(
            image_project=ALT_IMAGE_TARGET_PROJECT,
            image_override_project=ALT_IMAGE_OVERRIDE_PROJECT,
            helm_project=ALT_HELM_TARGET_PROJECT,
        )
        assert_registry_absent(alternate_coordinates)
        await execute_planned_import(orchestrator, manager, alternate_id, alternate_plan)
        alternate_receipt = orchestrator.receipt(alternate_id)
        assert_receipt_destinations(alternate_receipt, alternate_plan)
        await verify_target_content(factory, settings, alternate_plan, descriptors)

        # Missing project / denied write access must fail during planning, before
        # any registry mutation. Use fresh destination paths to prove absence.
        validator.missing_projects.add("missing-images")
        validator.denied_repositories.add(f"readonly-charts/charts/{CHART_NAME}")
        stage_incoming(settings, archive, sidecar)
        denied_id = await discover_one(manager, orchestrator)
        denied_preview = orchestrator.preview(denied_id)
        denied_mapping = mapping_for_preview(
            denied_preview,
            image_project="missing-images",
            image_override_project="missing-images",
            helm_project="readonly-charts",
        )
        denied_plan = await orchestrator.build_destination_plan(
            denied_id,
            denied_mapping,
            actor_username=TARGET_ACTOR,
        )
        if denied_plan.valid:
            fail("missing/no-write destination plan unexpectedly became valid")
        error_codes = {item.error_code for item in denied_plan.artifacts}
        if error_codes != {
            "import_destination_project_missing",
            "import_destination_write_forbidden",
        }:
            fail(f"unexpected destination access errors: {error_codes}")
        denied_coordinates = target_coordinates(
            image_project="missing-images",
            image_override_project="missing-images",
            helm_project="readonly-charts",
        )
        assert_registry_absent(denied_coordinates)
        try:
            await orchestrator.start_import(
                denied_id,
                actor_username=TARGET_ACTOR,
                overwrite_conflicts=False,
                destination_plan_id=denied_plan.plan_id,
            )
        except ImportOrchestrationError as exc:
            if exc.code != "import_destination_plan_invalid":
                raise
        else:
            fail("invalid destination plan unexpectedly reached import execution")
        assert_registry_absent(denied_coordinates)
        validator.reset()

        # Seed a conflicting digest at one resolved image destination. Planning
        # must classify the exact final reference as CONFLICT and default deny
        # must leave that digest untouched.
        primary_target_repository = f"{IMAGE_TARGET_PROJECT}/images/app"
        with factory() as session:
            skopeo = SkopeoService(session, settings)
            conflict_layout = settings.skopeo_payload_root / "conflict" / "image"
            conflict_digest = write_oci_image_fixture(
                conflict_layout,
                b"isolated-target-conflict-v3\n",
            )
            if conflict_digest == descriptors[IMAGE_REPOSITORY].source_digest:
                fail("conflict fixture unexpectedly matches source digest")
            conflict_import = await skopeo.import_image(
                conflict_layout,
                ImageReference(primary_target_repository, IMAGE_TAG),
                expected_digest=conflict_digest,
            )
            if conflict_import.target_digest != conflict_digest:
                fail("failed to seed conflicting mapped TARGET image")

        stage_incoming(settings, archive, sidecar)
        conflict_id = await discover_one(manager, orchestrator)
        conflict_preview = orchestrator.preview(conflict_id)
        conflict_mapping = mapping_for_preview(
            conflict_preview,
            image_project=IMAGE_TARGET_PROJECT,
            image_override_project=IMAGE_OVERRIDE_PROJECT,
            helm_project=HELM_TARGET_PROJECT,
        )
        conflict_plan = await orchestrator.build_destination_plan(
            conflict_id,
            conflict_mapping,
            actor_username=TARGET_ACTOR,
        )
        conflict_by_source = planned_by_source(conflict_plan)
        if conflict_by_source[IMAGE_REPOSITORY].classification is not ImportPreviewState.CONFLICT:
            fail("mapped conflict fixture was not classified as CONFLICT")
        if any(
            item.classification is not ImportPreviewState.SAME
            for source, item in conflict_by_source.items()
            if source != IMAGE_REPOSITORY
        ):
            fail("non-conflicting mapped artifacts are not SAME during conflict scenario")
        try:
            await orchestrator.start_import(
                conflict_id,
                actor_username=TARGET_ACTOR,
                overwrite_conflicts=False,
                destination_plan_id=conflict_plan.plan_id,
            )
        except ImportOrchestrationError as exc:
            if exc.code != "import_conflict_blocked":
                raise
        else:
            fail("default conflict policy unexpectedly allowed mapped overwrite")
        if registry_manifest_digest(primary_target_repository, IMAGE_TAG) != conflict_digest:
            fail("blocked mapped conflict mutated TARGET image")

        # Signed-bundle tamper remains fail-closed even after destination mapping.
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

        if registry_manifest_digest(primary_target_repository, IMAGE_TAG) != conflict_digest:
            fail("tampered bundle mutated TARGET registry")

        print(
            f"TARGET mixed acceptance OK: {verified_transfer.manifest.delivery_id}; "
            "mapped refs, digests/content, replay, alternate mapping, access fail-closed, "
            "conflict policy, receipt provenance and tamper assertions passed"
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
