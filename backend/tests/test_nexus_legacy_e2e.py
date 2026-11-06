"""Live integration against an older Nexus 3 Docker Registry v2 (not an OCI repository).

Starts only in the dedicated opt-in GitHub Actions workflow.
"""

from __future__ import annotations

import asyncio
import base64
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
from app.services.harbor_settings import EffectiveHarborSettings
from app.services.nexus_discovery import NexusDiscoveryService
from app.services.nexus_service import (
    ImageReference,
    NexusService,
    NexusServiceError,
    TargetState,
)
from app.services.registry_client import _NexusServiceWrapper, create_registry_client
from app.services.registry_probe import probe_docker_v2
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
async def test_real_empty_hosted_docker_v2_connection_requires_no_image(
    admin_password: str, tls_certificate: Path,
) -> None:
    """A fresh Nexus hosted connector must pass without busybox or any tag.

    The disposable setup starts the HTTPS Docker source connector on 8443;
    this test runs before schema2 fixture publication in this module.
    """
    profile = EffectiveHarborSettings(
        url="https://127.0.0.1:8443",
        username="admin",
        password=admin_password,
        verify_tls=True,
        ca_file=tls_certificate,
    )
    result = await probe_docker_v2(
        profile, connect_timeout=10, read_timeout=20,
    )
    assert result.ok, result.code
    assert result.code == "registry_connection_ok"

    rejected = EffectiveHarborSettings(
        url=profile.url,
        username="admin",
        password="deliberately-wrong-password",
        verify_tls=True,
        ca_file=tls_certificate,
    )
    wrong = await probe_docker_v2(
        rejected, connect_timeout=10, read_timeout=20,
    )
    assert not wrong.ok
    assert wrong.code == "registry_auth_failed"
    assert "deliberately-wrong-password" not in wrong.message

    unknown_ca = EffectiveHarborSettings(
        url=profile.url,
        username="admin",
        password=admin_password,
        verify_tls=True,
        ca_file=None,
    )
    tls = await probe_docker_v2(
        unknown_ca, connect_timeout=10, read_timeout=20,
    )
    assert not tls.ok and tls.code == "registry_tls_failed"


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
async def test_two_named_docker_connector_profiles_are_isolated(
    admin_password: str, tmp_path: Path,
) -> None:
    """Two Nexus hosted repos on separate connectors; no global auth fallback.

    The same account can be scoped to more than one repository. Portal must
    preserve the connector URL and registry identity of each selected profile.
    """
    settings = make_settings(tmp_path, "intentionally-wrong-global-password")
    seed = NexusService(
        MagicMock(), make_settings(tmp_path / "seed", admin_password),
    )
    expected = await asyncio.to_thread(
        seeded_schema2_image,
        seed,
        settings.skopeo_payload_root / "multi-seed",
        admin_password,
    )
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        profiles = HarborProfileService(session, settings)
        first = profiles.create(
            name="Docker hosted source", url="http://127.0.0.1:8082",
            username="admin", enabled=True, verify_tls=False,
            registry_type="nexus",
            nexus_search_url="https://127.0.0.1:8444",
            nexus_search_repository="htp-e2e",
        )
        second = profiles.create(
            name="Docker hosted target", url="http://127.0.0.1:8083",
            username="admin", enabled=True, verify_tls=False,
            registry_type="nexus",
            nexus_search_url="https://127.0.0.1:8444",
            nexus_search_repository="htp-e2e-target",
        )
        profiles.rotate_credential(first.id, admin_password)
        profiles.rotate_credential(second.id, admin_password)
        session.commit()
        assert profiles.get(first.id).nexus_search_repository == "htp-e2e"
        assert profiles.get(second.id).nexus_search_repository == "htp-e2e-target"

        source = NexusService(session, settings, nexus_profile_id=first.id)
        target = NexusService(session, settings, nexus_profile_id=second.id)
        image = ImageReference("htp-e2e/source", "1.0.0")
        assert (await source.inspect_image(image)).digest == expected
        assert (await target.inspect_target(
            image, expected_digest=expected,
        )).state == TargetState.ABSENT

        exported = await source.export_image(
            image, settings.skopeo_payload_root / "multi-exported",
        )
        assert exported.source_digest == expected
        imported = await target.import_image(
            exported.payload_path, image, expected_digest=expected,
        )
        assert imported.verified
        assert imported.target_digest == expected
        assert (await target.inspect_image(image)).digest == expected


@pytest.mark.asyncio
async def test_real_nexus_inventory_and_docker_tag_browse_isolated_from_harbor(
    admin_password: str, tls_certificate: Path, tmp_path: Path,
) -> None:
    """Discover two Nexus hosted repositories, then read a seeded Docker tag.

    Inventory is a Nexus REST API view; tag browsing is authorized on the
    selected Docker v2 connector and does not imply write access.
    """
    settings = make_settings(tmp_path, "invalid-global-password")
    seed = NexusService(
        MagicMock(), make_settings(tmp_path / "seed", admin_password),
    )
    await asyncio.to_thread(
        seeded_schema2_image,
        seed,
        settings.skopeo_payload_root / "browse-seed",
        admin_password,
    )
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    with sessions() as session:
        profiles = HarborProfileService(session, settings)
        profile = profiles.create(
            name="Nexus SOURCE inventory",
            url="https://127.0.0.1:8443",
            username="admin",
            enabled=True,
            verify_tls=True,
            registry_type="nexus",
            nexus_search_url="https://127.0.0.1:8444",
            nexus_search_repository="htp-e2e",
        )
        profiles.rotate_credential(profile.id, admin_password)
        profiles.install_ca(profile.id, tls_certificate.read_text(encoding="utf-8"))
        session.commit()

        discovery = NexusDiscoveryService(session, settings, profile.id)
        visible = await discovery.inventory()
        assert visible.available, visible.message
        repositories = {(item.name, item.format, item.kind) for item in visible.items}
        assert ("htp-e2e", "docker", "hosted") in repositories
        assert ("htp-e2e-target", "docker", "hosted") in repositories
        assert ("htp-helm-source", "helm", "hosted") in repositories
        assert all(item.format in {"docker", "helm"} for item in visible.items)

        # Docker v2 tags/list is separate from REST inventory/search.
        discovery.search_repository = None
        tags = await discovery.tags("htp-e2e/source")
        assert tags.available, tags.message
        assert "1.0.0" in tags.items
        invalid_repo = await discovery.tags("htp-e2e/does-not-exist")
        assert "1.0.0" not in invalid_repo.items


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
    outside = tmp_path / "outside"
    outside.mkdir()
    (settings.skopeo_payload_root / "escape").symlink_to(outside, target_is_directory=True)
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


@pytest.fixture(scope="session")
def tls_certificate() -> Path:
    raw = os.environ.get("HTP_NEXUS_TLS_CA_FILE")
    if not raw:
        pytest.skip("HTTPS reverse proxy is not configured")
    certificate = Path(raw)
    assert certificate.is_file()
    return certificate


@pytest.mark.asyncio
async def test_tls_proxy_rejects_unknown_ca(
    admin_password: str, tls_certificate: Path, tmp_path: Path
) -> None:
    """An HTTPS Nexus endpoint with an unknown CA must fail closed."""
    settings = make_settings(
        tmp_path, admin_password, url="https://127.0.0.1:8443"
    )
    settings.nexus_verify_tls = True
    settings.nexus_ca_file = None
    service = NexusService(MagicMock(), settings)
    with pytest.raises(NexusServiceError) as error:
        await service.inspect_image(ImageReference("htp-e2e/any", "1.0.0"))
    assert error.value.code == "nexus_tls_failed"


@pytest.mark.asyncio
async def test_tls_proxy_with_custom_ca_and_named_profile(
    admin_password: str, tls_certificate: Path, tmp_path: Path
) -> None:
    """HTTPS through nginx succeeds only with selected profile CA and credentials."""
    # Seed with a direct Docker v2 connector before using the independent TLS path.
    seed_settings = make_settings(tmp_path / "seed", admin_password)
    seed = NexusService(MagicMock(), seed_settings)
    fixture_root = seed_settings.skopeo_payload_root / "seed"
    expected = await asyncio.to_thread(
        seeded_schema2_image, seed, fixture_root, admin_password
    )
    settings = make_settings(tmp_path, "wrong-global-password")
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        profiles = HarborProfileService(session, settings)
        profile = profiles.create(
            name="Nexus behind TLS proxy",
            url="https://127.0.0.1:8443",
            username="admin",
            enabled=True,
            verify_tls=True,
            registry_type="nexus",
        )
        profiles.rotate_credential(profile.id, admin_password)
        profiles.install_ca(profile.id, tls_certificate.read_text(encoding="utf-8"))
        service = NexusService(session, settings, nexus_profile_id=profile.id)
        resolved = service._resolve_nexus()
        assert resolved.verify_tls is True
        assert resolved.ca_file is not None
        assert resolved.ca_file.is_file()
        assert resolved.password == admin_password
        observed = await service.inspect_image(ImageReference("htp-e2e/source", "1.0.0"))
        assert observed.digest == expected


@pytest.mark.asyncio
async def test_real_registry_client_adapter_round_trip(
    admin_password: str, tmp_path: Path
) -> None:
    """Exercise the actual Portal adapter, not only NexusService in isolation."""
    settings = make_settings(tmp_path, "deliberately-invalid-global-password")
    seed_settings = make_settings(tmp_path / "seed", admin_password)
    seed_service = NexusService(MagicMock(), seed_settings)
    expected_digest = await asyncio.to_thread(
        seeded_schema2_image,
        seed_service,
        seed_settings.skopeo_payload_root / "seed-image",
        admin_password,
    )

    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    with sessions() as session:
        profiles = HarborProfileService(session, settings)
        target = profiles.create(
            name="Nexus E2E via Portal Adapter",
            url=REGISTRY_URL,
            username="admin",
            verify_tls=False,
            enabled=True,
            registry_type="nexus",
        )
        profiles.rotate_credential(target.id, admin_password)
        selected = profiles.operation_snapshot(target.id)
        assert selected.registry_type == "nexus"
        client = create_registry_client(
            selected.registry_type, session, settings, profile_id=selected.id
        )
        assert isinstance(client, _NexusServiceWrapper)

        source = ImageReference("htp-e2e/source", "1.0.0")
        destination = ImageReference("htp-e2e/via-adapter", "1.0.0")
        exported = await client.export_image(
            source, settings.skopeo_payload_root / "via-adapter"
        )
        assert exported.source_digest == expected_digest
        assert exported.payload_digest == expected_digest
        assert exported.payload_path.is_dir()

        imported = await client.import_image(
            exported.payload_path, destination, expected_digest=expected_digest
        )
        assert imported.verified is True
        assert imported.target_digest == expected_digest
        inspected = await client.inspect_target(
            destination, expected_digest=expected_digest
        )
        assert inspected.state == TargetState.SAME_DIGEST
