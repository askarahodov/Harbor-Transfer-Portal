from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.db.base import Base
from app.db.session import create_db_engine, create_session_factory
from app.services.harbor_profiles import HarborProfile, HarborProfileService
from app.services.registry_client import (
    RegistryClient,
    _HarborClientWrapper,
    _NexusServiceWrapper,
    create_registry_client,
)


class TestRegistryClientProtocol:
    def test_registry_client_has_required_methods(self):
        """Verify RegistryClient protocol has all required methods"""
        assert "inspect_image" in dir(RegistryClient)
        assert "export_image" in dir(RegistryClient)
        assert "import_image" in dir(RegistryClient)
        assert "inspect_target" in dir(RegistryClient)

    def test_create_registry_client_harbor(self):
        client = create_registry_client("harbor", MagicMock(), MagicMock())
        assert isinstance(client, _HarborClientWrapper)

    def test_create_registry_client_nexus(self):
        client = create_registry_client("nexus", MagicMock(), MagicMock())
        assert isinstance(client, _NexusServiceWrapper)

    def test_create_registry_client_invalid(self):
        with pytest.raises(ValueError, match="Unknown registry type"):
            create_registry_client("invalid", MagicMock(), MagicMock())


class TestHarborClientWrapper:
    @pytest.mark.asyncio
    async def test_wraps_harbor_client(self):
        mock_settings = MagicMock()
        mock_settings.harbor_connect_timeout_seconds = 5.0
        mock_settings.harbor_read_timeout_seconds = 20.0

        with patch("app.services.harbor_settings.HarborSettingsService") as mock_profile_class:
            mock_resolved = MagicMock()
            mock_resolved.url = "https://harbor.example.com"
            mock_resolved.username = "admin"
            mock_resolved.password = MagicMock()
            mock_resolved.password.get_secret_value.return_value = "password"
            mock_resolved.verify_tls = True
            mock_resolved.ca_file = None
            mock_profile_class.return_value.resolve_profile.return_value = mock_resolved

            wrapper = _HarborClientWrapper(MagicMock(), MagicMock(), "profile-1")
            wrapper.client = AsyncMock()

            mock_image = MagicMock()
            mock_image.repository = "test/repo"
            mock_image.reference = "latest"

            wrapper.client.inspect_image.return_value = MagicMock(
                digest="sha256:abc",
                media_type="application/vnd.docker.distribution.manifest.v2+json",
                architecture="amd64",
                os="linux",
            )

            result = await wrapper.inspect_image(
                type("ImageReference", (), {"repository": "test/repo", "reference": "latest"})()
            )
            assert result.digest == "sha256:abc"


class TestNexusServiceWrapper:
    @pytest.mark.asyncio
    async def test_wraps_nexus_service(self):
        mock_settings = MagicMock()
        mock_settings.skopeo_temp_root = Path("/tmp")

        with patch("app.services.registry_client.NexusService") as mock_nexus_class:
            mock_service = AsyncMock()
            mock_nexus_class.return_value = mock_service

            wrapper = _NexusServiceWrapper(MagicMock(), mock_settings, "profile-1")
            wrapper.service = AsyncMock()

            mock_image = MagicMock()
            mock_image.repository = "test/repo"
            mock_image.reference = "latest"

            wrapper.service.inspect_image.return_value = MagicMock(
                digest="sha256:abc",
                media_type="application/vnd.docker.distribution.manifest.v2+json",
                architecture="amd64",
                os="linux",
            )

            result = await wrapper.inspect_image(
                type("ImageReference", (), {"repository": "test/repo", "reference": "latest"})()
            )
            assert result.digest == "sha256:abc"


class TestRegistryClientProtocolSecondary:
    def test_registry_client_protocol_has_required_methods(self):
        assert "inspect_image" in dir(RegistryClient)
        assert "export_image" in dir(RegistryClient)
        assert "import_image" in dir(RegistryClient)
        assert "inspect_target" in dir(RegistryClient)


class TestCreateRegistryClient:
    def test_create_registry_client_harbor(self):
        with patch("app.services.registry_client._HarborClientWrapper") as mock_wrapper:
            mock_wrapper.return_value = MagicMock()
            client = create_registry_client("harbor", MagicMock(), MagicMock())
            assert client is mock_wrapper.return_value

    def test_create_registry_client_nexus(self):
        with patch("app.services.registry_client._NexusServiceWrapper") as mock_wrapper:
            mock_wrapper.return_value = MagicMock()
            client = create_registry_client("nexus", MagicMock(), MagicMock())
            assert client is mock_wrapper.return_value

    def test_create_registry_client_invalid(self):
        with pytest.raises(ValueError, match="Unknown registry type"):
            create_registry_client("invalid", MagicMock(), MagicMock())


class TestHarborProfileRegistryType:
    def test_default_registry_type_harbor(self):
        profile = HarborProfile(
            id="test",
            name="test",
            url="https://harbor.example.com",
            username=None,
            verify_tls=True,
            enabled=True,
            is_default=False,
        )
        assert profile.registry_type == "harbor"

    def test_explicit_nexus_registry_type(self):
        profile = HarborProfile(
            id="test",
            name="test",
            url="https://nexus.example.com",
            username=None,
            verify_tls=True,
            enabled=True,
            is_default=False,
            registry_type="nexus",
        )
        assert profile.registry_type == "nexus"


def test_default_harbor_profile_keeps_harbor_transport_when_nexus_is_configured(
    tmp_path: Path,
) -> None:
    """Adding global Nexus credentials must never switch the default Harbor adapter."""
    database_url = f"sqlite:///{tmp_path / 'registry-default.db'}"
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        harbor_url="https://harbor.example.test",
        nexus_url="http://127.0.0.1:8082",
        nexus_user="nexus-admin",
        nexus_password="unused-nexus-secret",
        harbor_managed_secret_file=tmp_path / "secrets" / "harbor-password",
    )
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)

    with sessions() as session:
        selected = HarborProfileService(session, settings).operation_snapshot(None)
        assert selected.id == "default"
        assert selected.registry_type == "harbor"
        client = create_registry_client(
            selected.registry_type, session, settings, profile_id=selected.id
        )
        assert isinstance(client, _HarborClientWrapper)
        assert client.client.harbor_profile_id == "default"
        assert client.client._resolve_harbor().url == "https://harbor.example.test"


def test_named_nexus_profile_does_not_inherit_global_or_harbor_credentials(
    tmp_path: Path,
) -> None:
    """Profile-selected client must use its own stored endpoint and credential."""
    database_url = f"sqlite:///{tmp_path / 'registry-profiles.db'}"
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        harbor_url="https://harbor.example.test",
        nexus_url="http://127.0.0.1:8089",
        nexus_user="wrong-global",
        nexus_password="wrong-global-secret",
        harbor_managed_secret_file=tmp_path / "secrets" / "harbor-password",
    )
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)

    with sessions() as session:
        profiles = HarborProfileService(session, settings)
        target = profiles.create(
            name="Legacy Docker Registry",
            url="http://127.0.0.1:8082",
            username="registry-robot",
            verify_tls=False,
            enabled=True,
            registry_type="nexus",
        )
        profiles.rotate_credential(target.id, "profile-specific-secret")
        selected = profiles.operation_snapshot(target.id)
        client = create_registry_client(
            selected.registry_type, session, settings, profile_id=selected.id
        )
        assert isinstance(client, _NexusServiceWrapper)
        assert client.service.nexus_profile_id == target.id
        resolved = client.service._resolve_nexus()
        assert resolved.url == "http://127.0.0.1:8082"
        assert resolved.username == "registry-robot"
        assert resolved.password == "profile-specific-secret"
        assert resolved.verify_tls is False

        default_profile = profiles.operation_snapshot(None)
        assert default_profile.registry_type == "harbor"
        default_client = create_registry_client(
            default_profile.registry_type, session, settings, profile_id=default_profile.id
        )
        assert isinstance(default_client, _HarborClientWrapper)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
