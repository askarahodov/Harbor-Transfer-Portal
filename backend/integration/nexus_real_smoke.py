#!/usr/bin/env python3
"""Real image + Helm OCI transfers against a disposable Nexus Docker hosted registry.

Requires a Nexus instance bootstrapped by deploy/smoke-nexus-e2e.sh.
Neither production credentials nor an external registry are used.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.config import Settings
from app.services.nexus_service import (
    HelmChartReference,
    ImageReference,
    NexusService,
    NexusServiceError,
    TargetState,
)
from registry_smoke import _write_oci_image_fixture

REGISTRY = "http://nexus:5000"
VERSION = "1.2.3"


def _build_settings(root: Path, password: str) -> Settings:
    return Settings(
        _env_file=None,
        nexus_url=REGISTRY,
        nexus_user="admin",
        nexus_password=password,
        nexus_verify_tls=False,
        skopeo_payload_root=root / "payloads",
        skopeo_temp_root=root / "temp",
        skopeo_timeout_seconds=120,
        skopeo_output_limit_bytes=65536,
        helm_binary="helm",
        skopeo_binary="skopeo",
    )


async def _image_roundtrip(service: NexusService, root: Path) -> None:
    payload = root / "payloads" / "seed"
    expected = _write_oci_image_fixture(payload)
    source = ImageReference("integration/source-image", VERSION)
    target = ImageReference("integration/copied-image", VERSION)

    absent = await service.inspect_target(source, expected_digest=expected)
    assert absent.state is TargetState.ABSENT, absent

    seeded = await service.import_image(payload, source, expected_digest=expected)
    assert seeded.verified and seeded.target_digest == expected, seeded

    observed = await service.inspect_image(source)
    assert observed.digest == expected, observed

    exported = await service.export_image(source, root / "payloads" / "export")
    assert exported.payload_digest == expected
    assert exported.source_digest == expected

    imported = await service.import_image(exported.payload_path, target, expected_digest=expected)
    assert imported.verified and imported.target_digest == expected, imported

    verified = await service.inspect_target(target, expected_digest=expected)
    assert verified.state is TargetState.SAME_DIGEST, verified

    conflicting = await service.inspect_target(target, expected_digest="sha256:" + "0" * 64)
    assert conflicting.state is TargetState.CONFLICTING_DIGEST, conflicting

    try:
        service._validate_export_path(root / "outside-payloads")
    except NexusServiceError as exc:
        assert exc.code == "nexus_payload_path_outside_root", exc
    else:
        raise AssertionError("Nexus accepted a payload path outside its security boundary")

    print("REAL NEXUS image: export/import, SHA-256, conflict and path boundary PASS")


def _chart_package(root: Path) -> Path:
    chart_dir = root / "chart-source" / "smoke-chart"
    chart_dir.mkdir(parents=True)
    (chart_dir / "Chart.yaml").write_text(
        "apiVersion: v2\nname: smoke-chart\nversion: 1.2.3\ndescription: Nexus E2E\ntype: application\n",
        encoding="utf-8",
    )
    package_dir = root / "chart-packages"
    package_dir.mkdir()
    subprocess.run(
        ["helm", "package", str(chart_dir), "--destination", str(package_dir)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    package = package_dir / "smoke-chart-1.2.3.tgz"
    assert package.is_file()
    return package


def _manifest_digest(password: str, repository: str, version: str) -> str:
    registry = urlsplit(REGISTRY)
    assert registry.hostname == "nexus" and registry.port == 5000
    basic = base64.b64encode(f"admin:{password}".encode()).decode("ascii")
    request = Request(
        f"{REGISTRY}/v2/{repository}/manifests/{version}",
        method="HEAD",
        headers={
            "Authorization": f"Basic {basic}",
            "Accept": (
                "application/vnd.oci.image.manifest.v1+json,"
                "application/vnd.docker.distribution.manifest.v2+json"
            ),
        },
    )
    with urlopen(request, timeout=20) as response:
        digest = response.headers.get("Docker-Content-Digest")
    assert digest is not None and digest.startswith("sha256:"), digest
    return digest


async def _chart_roundtrip(service: NexusService, root: Path, password: str) -> None:
    chart = HelmChartReference(repository="charts", name="smoke-chart", version=VERSION)
    package = _chart_package(root)
    expected_package_digest = "sha256:" + hashlib.sha256(package.read_bytes()).hexdigest()

    try:
        await service.push_chart(
            package, chart, expected_digest="sha256:" + "0" * 64,
        )
    except NexusServiceError as exc:
        assert exc.code == "helm_digest_mismatch", exc
    else:
        raise AssertionError("Nexus accepted a corrupted chart expectation")

    published = await service.push_chart(package, chart, expected_digest=expected_package_digest)
    assert published.target_digest.startswith("sha256:"), published
    remote_digest = _manifest_digest(password, "charts/smoke-chart", VERSION)
    assert remote_digest == published.target_digest, (remote_digest, published.target_digest)

    pulled = await service.pull_chart(chart, root / "pulled-chart")
    assert pulled.package.is_file()
    assert pulled.source_digest == expected_package_digest
    assert pulled.target_digest == expected_package_digest
    assert pulled.package.read_bytes() == package.read_bytes()
    print("REAL NEXUS Helm: push/pull, remote OCI digest, chart SHA-256 PASS")


async def _reject_invalid_credentials(root: Path) -> None:
    settings = _build_settings(root, "invalid-nexus-e2e-password")
    bad = NexusService(None, settings)
    try:
        await bad.inspect_image(ImageReference("integration/source-image", VERSION))
    except NexusServiceError:
        print("REAL NEXUS authentication: rejected incorrect password PASS")
    else:
        raise AssertionError("Nexus unexpectedly allowed invalid credentials")


async def _run(password: str) -> None:
    with tempfile.TemporaryDirectory(prefix="htp-real-nexus-") as directory:
        root = Path(directory)
        service = NexusService(None, _build_settings(root, password))
        await _image_roundtrip(service, root)
        await _chart_roundtrip(service, root, password)
        await _reject_invalid_credentials(root)


def main() -> None:
    password = os.environ.get("NEXUS_E2E_PASSWORD", "")
    if not password:
        raise SystemExit("NEXUS_E2E_PASSWORD must be supplied at runtime")
    asyncio.run(_run(password))
    print("Real Nexus E2E passed")


if __name__ == "__main__":
    main()
