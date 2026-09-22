#!/usr/bin/env python3
"""Real Skopeo/Helm integration smoke against a disposable local OCI registry.

The harness runs inside the production backend image. Its Docker network is
internal-only, so the application flow cannot reach a public registry or the
internet while the assertions execute.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from app.config import Settings
from app.services.harbor_settings import EffectiveHarborSettings
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmOciService,
    HelmTargetState,
)
from app.services.skopeo_service import ImageReference, SkopeoService, TargetState

_REGISTRY_URL = os.environ.get(
    "HTP_INTEGRATION_REGISTRY_URL",
    "http://registry:5000",
).rstrip("/")
_IMAGE_VERSION = "1.0.0"
_CHART_NAME = "fixture-chart"
_CHART_VERSION = "1.2.3"
_OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
_DOCKER_MANIFEST_MEDIA_TYPE = "application/vnd.docker.distribution.manifest.v2+json"


class _DummySession:
    """Constructor-only stand-in; service metadata access is replaced below."""


class _StaticHarborSettings:
    def __init__(self, resolved: EffectiveHarborSettings) -> None:
        self._resolved = resolved

    def resolve(self, profile_id: str | None = None) -> EffectiveHarborSettings:
        del profile_id
        return self._resolved


def _assert_local_registry_url() -> None:
    parsed = urlsplit(_REGISTRY_URL)
    if parsed.scheme != "http" or parsed.hostname not in {"registry", "localhost", "127.0.0.1"}:
        raise RuntimeError(
            "HTP_INTEGRATION_REGISTRY_URL must point to the disposable local HTTP registry"
        )
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
        raise RuntimeError("Integration registry URL must not contain path/query/userinfo")


def _wait_for_registry(timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{_REGISTRY_URL}/v2/", timeout=2) as response:
                if response.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"Disposable registry did not become ready: {last_error}")


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_blob(layout: Path, payload: bytes) -> tuple[str, int]:
    digest_hex = hashlib.sha256(payload).hexdigest()
    path = layout / "blobs" / "sha256" / digest_hex
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return f"sha256:{digest_hex}", len(payload)


def _fixture_layer() -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        payload = b"harbor-transfer-portal local-registry integration fixture\n"
        info = tarfile.TarInfo("fixture.txt")
        info.size = len(payload)
        info.mode = 0o644
        info.mtime = 0
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


def _write_oci_image_fixture(layout: Path) -> str:
    layout.mkdir(parents=True, exist_ok=True)
    layer = _fixture_layer()
    layer_digest, layer_size = _write_blob(layout, layer)
    config = _canonical_json(
        {
            "architecture": "amd64",
            "config": {},
            "created": "2026-01-01T00:00:00Z",
            "os": "linux",
            "rootfs": {"diff_ids": [layer_digest], "type": "layers"},
        }
    )
    config_digest, config_size = _write_blob(layout, config)
    manifest = _canonical_json(
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
            "mediaType": _OCI_MANIFEST_MEDIA_TYPE,
            "schemaVersion": 2,
        }
    )
    manifest_digest, manifest_size = _write_blob(layout, manifest)
    index = _canonical_json(
        {
            "manifests": [
                {
                    "annotations": {"org.opencontainers.image.ref.name": "image"},
                    "digest": manifest_digest,
                    "mediaType": _OCI_MANIFEST_MEDIA_TYPE,
                    "size": manifest_size,
                }
            ],
            "schemaVersion": 2,
        }
    )
    (layout / "index.json").write_bytes(index)
    (layout / "oci-layout").write_text(
        '{"imageLayoutVersion":"1.0.0"}\n',
        encoding="utf-8",
    )
    return manifest_digest


def _registry_manifest_digest(repository: str, reference: str) -> str | None:
    url = f"{_REGISTRY_URL}/v2/{repository}/manifests/{quote(reference, safe='')}"
    request = Request(
        url,
        method="HEAD",
        headers={
            "Accept": f"{_OCI_MANIFEST_MEDIA_TYPE}, {_DOCKER_MANIFEST_MEDIA_TYPE}",
        },
    )
    try:
        with urlopen(request, timeout=5) as response:
            digest = response.headers.get("Docker-Content-Digest")
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(
            f"Registry manifest lookup failed for {repository}:{reference}: HTTP {exc.code}"
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(
            f"Registry manifest lookup failed for {repository}:{reference}: {exc}"
        ) from exc
    if digest is None or not digest.startswith("sha256:") or len(digest) != 71:
        raise RuntimeError(
            f"Registry returned invalid Docker-Content-Digest for {repository}:{reference}"
        )
    return digest


def _settings(root: Path) -> Settings:
    payload_root = root / "oci"
    helm_workspace = root / "helm-workspace"
    payload_root.mkdir(parents=True, exist_ok=True)
    helm_workspace.mkdir(parents=True, exist_ok=True)
    return Settings(
        _env_file=None,
        harbor_url=_REGISTRY_URL,
        harbor_verify_tls=False,
        skopeo_payload_root=payload_root,
        skopeo_temp_root=root / "skopeo-tmp",
        skopeo_timeout_seconds=30,
        skopeo_output_limit_bytes=64 * 1024,
        helm_workspace_root=helm_workspace,
        helm_temp_root=root / "helm-tmp",
        helm_timeout_seconds=30,
        helm_output_limit_bytes=64 * 1024,
    )


def _effective_harbor() -> EffectiveHarborSettings:
    return EffectiveHarborSettings(
        url=_REGISTRY_URL,
        username=None,
        password=None,
        verify_tls=False,
        ca_file=None,
    )


def _skopeo_service(settings: Settings) -> SkopeoService:
    service = SkopeoService(_DummySession(), settings)  # type: ignore[arg-type]
    service.harbor_settings = _StaticHarborSettings(_effective_harbor())  # type: ignore[assignment]
    return service


def _helm_service(settings: Settings) -> HelmOciService:
    service = HelmOciService(
        _DummySession(),  # type: ignore[arg-type]
        settings,
        digest_resolver=lambda chart: _registry_manifest_digest(
            chart.harbor_repository,
            chart.version,
        ),
    )
    service.harbor_settings = _StaticHarborSettings(_effective_harbor())  # type: ignore[assignment]
    return service


def _package_chart(settings: Settings, root: Path) -> Path:
    chart_root = root / "chart-src" / _CHART_NAME
    chart_root.mkdir(parents=True, exist_ok=True)
    (chart_root / "Chart.yaml").write_text(
        "\n".join(
            (
                "apiVersion: v2",
                f"name: {_CHART_NAME}",
                f"version: {_CHART_VERSION}",
                "description: local-registry integration fixture",
                "type: application",
                "",
            )
        ),
        encoding="utf-8",
    )
    subprocess.run(
        (
            settings.helm_binary,
            "package",
            str(chart_root),
            "--destination",
            str(settings.helm_workspace_root),
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    package = settings.helm_workspace_root / f"{_CHART_NAME}-{_CHART_VERSION}.tgz"
    if not package.is_file():
        raise RuntimeError("helm package did not create expected chart archive")
    return package


async def _exercise_skopeo(settings: Settings) -> None:
    service = _skopeo_service(settings)
    fixture = settings.skopeo_payload_root / "fixture"
    expected_digest = _write_oci_image_fixture(fixture)
    source = ImageReference("integration/source-image", _IMAGE_VERSION)
    copied = ImageReference("integration/copied-image", _IMAGE_VERSION)

    preflight = await service.inspect_target(source, expected_digest=expected_digest)
    if preflight.state is not TargetState.ABSENT:
        raise AssertionError(f"source fixture unexpectedly exists: {preflight}")

    seeded = await service.import_image(fixture, source, expected_digest=expected_digest)
    if not seeded.verified or seeded.target_digest != expected_digest:
        raise AssertionError(f"Skopeo seed digest mismatch: {seeded}")

    source_state = await service.inspect_target(source, expected_digest=expected_digest)
    if source_state.state is not TargetState.SAME_DIGEST:
        raise AssertionError(f"Skopeo source inspect mismatch: {source_state}")

    exported = await service.export_image(
        source,
        settings.skopeo_payload_root / "exported",
    )
    if exported.source_digest != expected_digest or exported.payload_digest != expected_digest:
        raise AssertionError(f"Skopeo export did not preserve digest: {exported}")

    imported = await service.import_image(
        exported.payload_path,
        copied,
        expected_digest=expected_digest,
    )
    if not imported.verified or imported.target_digest != expected_digest:
        raise AssertionError(f"Skopeo copied image digest mismatch: {imported}")

    copied_state = await service.inspect_target(copied, expected_digest=expected_digest)
    if copied_state.state is not TargetState.SAME_DIGEST:
        raise AssertionError(f"Skopeo copied target inspect mismatch: {copied_state}")

    print(f"Skopeo integration OK: {expected_digest}")


async def _exercise_helm(settings: Settings, root: Path) -> None:
    service = _helm_service(settings)
    chart = HelmChartReference("integration/charts", _CHART_NAME, _CHART_VERSION)
    package = _package_chart(settings, root)
    original_sha256 = hashlib.sha256(package.read_bytes()).hexdigest()

    preflight = await service.inspect_target(chart)
    if preflight.state is not HelmTargetState.ABSENT:
        raise AssertionError(f"chart fixture unexpectedly exists: {preflight}")

    pushed = await service.push_chart(package, chart)
    if not pushed.target_digest.startswith("sha256:"):
        raise AssertionError(f"Helm push returned invalid target digest: {pushed}")

    visible = await service.inspect_target(chart, expected_digest=pushed.target_digest)
    if visible.state is not HelmTargetState.SAME_DIGEST:
        raise AssertionError(f"Helm target inspect mismatch: {visible}")

    pulled = await service.pull_chart(
        chart,
        settings.helm_workspace_root / "pulled",
    )
    if pulled.source_digest != pushed.target_digest:
        raise AssertionError(
            f"Helm pull source digest mismatch: {pulled.source_digest} != {pushed.target_digest}"
        )
    if pulled.package.name != _CHART_NAME or pulled.package.version != _CHART_VERSION:
        raise AssertionError(f"Helm pulled package metadata mismatch: {pulled.package}")
    if pulled.package.sha256 != original_sha256:
        raise AssertionError(
            f"Helm chart content changed after registry round-trip: "
            f"{pulled.package.sha256} != {original_sha256}"
        )

    print(f"Helm integration OK: {pushed.target_digest}")


def main() -> int:
    _assert_local_registry_url()
    _wait_for_registry()
    print(
        subprocess.run(
            ("skopeo", "--version"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    print(
        subprocess.run(
            ("helm", "version", "--short"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )

    with tempfile.TemporaryDirectory(prefix="htp-registry-integration-") as temp_name:
        root = Path(temp_name)
        settings = _settings(root)
        asyncio.run(_exercise_skopeo(settings))
        asyncio.run(_exercise_helm(settings, root))

    print("Local OCI registry integration passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
