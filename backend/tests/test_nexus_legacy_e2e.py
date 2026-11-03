"""Live integration against an older Nexus 3 Docker Registry v2 (not an OCI repository).

Starts only in the dedicated opt-in GitHub Actions workflow.
"""

from __future__ import annotations

import base64
import asyncio
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.db.base import Base
from app.db.session import create_db_engine, create_session_factory
from app.services.harbor_profiles import HarborProfileService
from app.services.nexus_service import (
    ImageReference,
    NexusService,
    NexusServiceError,
    TargetState,
)
from integration.registry_smoke import _write_oci_image_fixture

pytestmark = pytest.mark.skipif(
    os.environ.get("HTP_NEXUS_LEGACY_E2E") != "1",
    reason="Requires disposable Nexus 3 Docker v2 registry in dedicated GitHub Actions workflow",
)

REGISTRY_URL = "http://127.0.0.1:8082"


@pytest.fixture(scope="session")
def admin_password() -> str:
    file = Path(os.environ["HTP_NEXUS_E2E_PASSWORD_FILE"])
    assert file.is_file()
    return file.read_text(encoding="utf-8").strip()


def make_settings(tmp_path: Path, password: str, *, url: str = REGISTRY_URL) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'profiles.db'}",
        harbor_managed_secret_file=tmp_path / "secrets" / "harbor-password",
        nexus_url=url,
        nexus_user="admin",
        nexus_password=password,
        nexus_verify_tls=False,
        skopeo_binary="skopeo",
        skopeo_payload_root=tmp_path / "payload",
        skopeo_temp_root=tmp_path / "tmp",
        skopeo_timeout_seconds=120,
    )


def seeded_schema2_image(service: NexusService, layout: Path, password: str) -> str:
    """Seed Docker schema2 explicitly: older Nexus versions cannot store OCI manifests."""
    _write_oci_image_fixture(layout)
    auth_file = layout.parent / "seed-auth.json"
    credentials = base64.b64encode(f"admin:{password}".encode()).decode()
    auth_file.write_text(
        json.dumps({"auths": {"127.0.0.1:8082": {"auth": credentials}}}),
        encoding="utf-8",
    )
    auth_file.chmod(0o600)
    subprocess.run(
        (
            "skopeo",
            "copy",
            "--format", "v2s2",
            "--dest-authfile", str(auth_file),
            "--dest-tls-verify=false",
            f"oci:{layout}",
            "docker://127.0.0.1:8082/htp-e2e/source:1.0.0",
        ),
        check=True, capture_output=True, text=True, timeout=120,
    )
    source = ImageReference("htp-e2e/source", "1.0.0")
    return asyncio.run(service.inspect_image(source)).digest


@pytest.mark.asyncio
async def test_docker_schema2_round_trip(admin_password: str, tmp_path: Path) -> None:
    """Exercise NexusService against actual Docker schema2 manifests, not OCI registry."""
    settings = make_settings(tmp_path, admin_password)
    service = NexusService(MagicMock(), settings)
    layout = settings.skopeo_payload_root / "seed"
    expected = await asyncio.to_thread(
        seeded_schema2_image, service, layout, admin_password
    )
    source = ImageReference("htp-e2e/source", "1.0.0")
    target = ImageReference("htp-e2e/copied", "1.0.0")

    source_status = await service.inspect_target(source, expected_digest=expected)
    assert source_status.state == TargetState.SAME_DIGEST

    exported = await service.export_image(source, settings.skopeo_payload_root / "exported")
    assert exported.source_digest == expected
    assert exported.payload_digest == expected
    imported = await service.import_image(
        exported.payload_path, target, expected_digest=expected
    )
    assert imported.verified
    assert imported.target_digest == expected
    target_status = await service.inspect_target(target, expected_digest=expected)
    assert target_status.state == TargetState.SAME_DIGEST


@pytest.mark.asyncio
async def test_wrong_credentials_are_rejected(admin_password: str, tmp_path: Path) -> None:
    settings = make_settings(tmp_path, "deliberately-wrong-password")
    service = NexusService(MagicMock(), settings)
    with pytest.raises(NexusServiceError) as error:
        await service.inspect_image(ImageReference("htp-e2e/private", "1.0.0"))
    assert error.value.code == "nexus_auth_failed"


@pytest.mark.asyncio
async def test_unavailable_registry(admin_password: str, tmp_path: Path) -> None:
    settings = make_settings(tmp_path, admin_password, url="http://127.0.0.1:59979")
    service = NexusService(MagicMock(), settings)
    with pytest.raises(NexusServiceError) as error:
        await service.inspect_image(ImageReference("htp-e2e/missing", "1.0.0"))
    assert error.value.code == "nexus_registry_connection_failed"


@pytest.mark.asyncio
async def test_payload_digest_mismatch_before_network(admin_password: str, tmp_path: Path) -> None:
    settings = make_settings(tmp_path, admin_password)
    service = NexusService(MagicMock(), settings)
    layout = settings.skopeo_payload_root / "local"
    _write_oci_image_fixture(layout)
    with pytest.raises(NexusServiceError) as error:
        await service.import_image(
            layout,
            ImageReference("htp-e2e/invalid", "1.0.0"),
            expected_digest="sha256:" + "0" * 64,
        )
    assert error.value.code == "skopeo_payload_digest_mismatch"


def test_payload_path_isolation(admin_password: str, tmp_path: Path) -> None:
    settings = make_settings(tmp_path, admin_password)
    service = NexusService(MagicMock(), settings)
    settings.skopeo_payload_root.mkdir(parents=True)
    (settings.skopeo_payload_root / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(NexusServiceError) as error:
        service._validate_export_path(settings.skopeo_payload_root / "escape" / "payload")
    assert error.value.code == "nexus_payload_path_outside_root"


@pytest.mark.asyncio
async def test_named_profile_uses_profile_secret(admin_password: str, tmp_path: Path) -> None:
    """Selected Nexus profile must not silently inherit invalid global credentials."""
    settings = make_settings(tmp_path, "wrong-global-secret")
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        profiles = HarborProfileService(session, settings)
        profile = profiles.create(
            name="Legacy E2E Nexus", url=REGISTRY_URL, username="admin",
            enabled=True, verify_tls=False, registry_type="nexus",
        )
        profiles.rotate_credential(profile.id, admin_password)
        service = NexusService(session, settings, nexus_profile_id=profile.id)
        effective = service._resolve_nexus()
        assert effective.password == admin_password
        assert effective.url == REGISTRY_URL
        assert (await service.inspect_target(
            ImageReference("htp-e2e/not-present", "1.0.0")
        )).state == TargetState.ABSENT


def test_docker_v2_not_helm_oci(admin_password: str, tmp_path: Path) -> None:
    """A Docker v2 integration test does not claim Helm OCI compatibility."""
    service = NexusService(MagicMock(), make_settings(tmp_path, admin_password))
    assert service._registry_host(service._resolve_nexus()) == "127.0.0.1:8082"
