from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from datetime import UTC, datetime

from app.services.registry_client import (
    RegistryClient,
    create_registry_client,
    RegistryClientFactory,
    _HarborClientWrapper,
    _NexusServiceWrapper,
)
from app.services.nexus_service import TargetState
from app.services.harbor_profiles import HarborProfile
from app.config import Settings
from sqlalchemy.orm import Session
from app.config import PortalContour
from app.services.operation_manager import OperationManager


class TestRegistryClientProtocol:
    def test_registry_client_has_required_methods(self):
        """Verify RegistryClient protocol has all required methods"""
        from app.services.registry_client import RegistryClient
        assert 'inspect_image' in dir(RegistryClient)
        assert 'export_image' in dir(RegistryClient)
        assert 'import_image' in dir(RegistryClient)
        assert 'inspect_target' in dir(RegistryClient)

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
        mock_session = MagicMock()
        mock_settings = MagicMock()
        mock_settings.harbor_connect_timeout_seconds = 5.0
        mock_settings.harbor_read_timeout_seconds = 20.0
        
        with patch("app.services.harbor_settings.HarborSettingsService") as mock_profile_class:
            mock_profile_service = MagicMock()
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


class TestRegistryClientProtocol:
    def test_registry_client_protocol_has_required_methods(self):
        from app.services.registry_client import RegistryClient
        assert 'inspect_image' in dir(RegistryClient)
        assert 'export_image' in dir(RegistryClient)
        assert 'import_image' in dir(RegistryClient)
        assert 'inspect_target' in dir(RegistryClient)


class TestCreateRegistryClient:
    def test_create_registry_client_harbor(self):
        with patch("app.services.registry_client._HarborClientWrapper") as mock_wrapper:
            mock_wrapper.return_value = MagicMock()
            client = create_registry_client("harbor", MagicMock(), MagicMock())
            assert mock_wrapper.called

    def test_create_registry_client_nexus(self):
        with patch("app.services.registry_client._NexusServiceWrapper") as mock_wrapper:
            mock_wrapper.return_value = MagicMock()
            client = create_registry_client("nexus", MagicMock(), MagicMock())
            assert mock_wrapper.called

    def test_create_registry_client_invalid(self):
        with pytest.raises(ValueError, match="Unknown registry type"):
            create_registry_client("invalid", MagicMock(), MagicMock())


class TestHarborProfileRegistryType:
    def test_default_registry_type_harbor(self):
        from app.services.harbor_profiles import HarborProfile
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
        from app.services.harbor_profiles import HarborProfile
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])