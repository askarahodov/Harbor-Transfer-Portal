"""Regression tests for Nexus OCI boundary and Helm CLI contracts."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.nexus_service import (
    CommandResult,
    HelmChartReference,
    NexusService,
    NexusServiceError,
    _SecurityContext,
)


@pytest.fixture
def service(tmp_path: Path) -> NexusService:
    settings = MagicMock()
    settings.skopeo_payload_root = tmp_path / "payloads"
    settings.skopeo_temp_root = tmp_path / "tmp"
    settings.skopeo_timeout_seconds = 30
    settings.skopeo_output_limit_bytes = 65536
    settings.helm_binary = "helm"
    settings.skopeo_binary = "skopeo"
    obj = NexusService(MagicMock(), settings)
    obj._resolve_nexus = MagicMock(return_value=SimpleNamespace(url="https://nexus.test:5000", verify_tls=True))
    obj._security_context = MagicMock(return_value=nullcontext(_SecurityContext(tmp_path, None, None, ())))
    obj._run_checked = AsyncMock(return_value=CommandResult(0, "", ""))
    return obj


def test_export_rejects_path_outside_payload_root(service: NexusService, tmp_path: Path):
    with pytest.raises(NexusServiceError, match="разрешённого data root"):
        service._validate_export_path(tmp_path / "outside")


def test_import_rejects_path_outside_payload_root(service: NexusService, tmp_path: Path):
    with pytest.raises(NexusServiceError, match="разрешённого data root"):
        service._validate_import_path(tmp_path / "outside")


def test_export_rejects_symlink_escape(service: NexusService, tmp_path: Path):
    service.payload_root.mkdir()
    (service.payload_root / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(NexusServiceError, match="разрешённого data root"):
        service._validate_export_path(service.payload_root / "escape" / "artifact")


@pytest.mark.asyncio
async def test_pull_uses_oci_reference_and_explicit_version(service: NexusService, tmp_path: Path):
    chart = HelmChartReference("team", "sample", "1.2.3")
    destination = tmp_path / "charts"
    destination.mkdir()
    (destination / "unrelated.tgz").write_bytes(b"old")

    async def run(argv, security):
        (destination / "sample-1.2.3.tgz").write_bytes(b"new")
        return CommandResult(0, "", "")

    service._run_checked = AsyncMock(side_effect=run)
    result = await service.pull_chart(chart, destination)
    assert result.package.name == "sample-1.2.3.tgz"
    args = service._run_checked.await_args.args[0]
    assert args[:4] == ("helm", "pull", "oci://nexus.test:5000/team/sample", "--version")
    assert args[4] == "1.2.3"


@pytest.mark.asyncio
async def test_push_uses_repository_and_remote_digest(service: NexusService, tmp_path: Path):
    package = tmp_path / "sample-1.2.3.tgz"
    package.write_bytes(b"chart")
    digest = service._compute_sha256(package)
    published_digest = "sha256:" + "a" * 64
    service._run_checked.return_value = CommandResult(0, "Pushed: oci://nexus.test:5000/team/sample:1.2.3\\nDigest: " + published_digest, "")
    result = await service.push_chart(package, HelmChartReference("team", "sample", "1.2.3"), expected_digest=digest)
    assert result.target_digest == published_digest
    assert service._run_checked.await_args.args[0][:4] == (
        "helm", "push", str(package), "oci://nexus.test:5000/team"
    )


@pytest.mark.asyncio
async def test_push_rejects_missing_remote_digest(service: NexusService, tmp_path: Path):
    package = tmp_path / "sample.tgz"
    package.write_bytes(b"chart")
    with pytest.raises(NexusServiceError, match="не вернул digest"):
        await service.push_chart(
            package, HelmChartReference("team", "sample", "1.2.3"),
            expected_digest=service._compute_sha256(package),
        )


@pytest.mark.asyncio
async def test_push_refuses_force_overwrite(service: NexusService, tmp_path: Path):
    package = tmp_path / "sample.tgz"
    package.write_bytes(b"chart")
    with pytest.raises(NexusServiceError, match="не поддерживает"):
        await service.push_chart(
            package, HelmChartReference("team", "sample", "1.2.3"),
            expected_digest=service._compute_sha256(package), allow_existing=True,
        )
    service._run_checked.assert_not_awaited()
