from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from dataclasses import dataclass

from app.services.nexus_service import (
    NexusService,
    ImageReference,
    ImageInspection,
    ExportResult,
    ImportResult,
    TargetInspection,
    TargetState,
    NexusPhase,
    NexusServiceError,
    HelmChartReference,
    HelmChartPullResult,
    HelmChartPushResult,
)
from app.services.registry_client import (
    RegistryClient,
    create_registry_client,
    RegistryClientFactory,
    _HarborClientWrapper,
    _NexusServiceWrapper,
)
from app.services.harbor_profiles import HarborProfile
from app.config import Settings
from sqlalchemy.orm import Session


class TestImageReference:
    def test_valid_tag_reference(self):
        ref = ImageReference(repository="myproject/myimage", reference="latest")
        assert ref.repository == "myproject/myimage"
        assert ref.reference == "latest"

    def test_valid_digest_reference(self):
        ref = ImageReference(repository="myproject/myimage", reference="sha256:abc123def4567890abcdef1234567890abcdef1234567890abcdef1234567890")
        assert ref.repository == "myproject/myimage"
        assert ref.reference.startswith("sha256:")

    def test_invalid_repository(self):
        with pytest.raises(ValueError, match="repository must be a normalized"):
            ImageReference(repository="INVALID", reference="latest")

    def test_invalid_reference(self):
        with pytest.raises(ValueError, match="reference must be an OCI tag or sha256 digest"):
            ImageReference(repository="myproject/myimage", reference="invalid@ref")


class TestNexusService:
    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.skopeo_binary = "skopeo"
        settings.helm_binary = "helm"
        settings.skopeo_timeout_seconds = 300.0
        settings.skopeo_output_limit_bytes = 65536
        settings.skopeo_payload_root = Path("/app/data")
        settings.skopeo_temp_root = Path("/app/data/tmp")
        settings.nexus_url = "https://nexus.example.com"
        settings.nexus_user = "admin"
        settings.nexus_password = "password"
        settings.nexus_verify_tls = True
        settings.nexus_ca_file = None
        return settings

    @pytest.fixture
    def mock_session(self):
        return MagicMock()

    @pytest.fixture
    def nexus_service(self, mock_session, mock_settings):
        return NexusService(mock_session, mock_settings)

    def test_init(self, mock_session, mock_settings):
        service = NexusService(mock_session, mock_settings)
        assert service.settings is not None
        assert service.nexus_profile_id is None
        assert service.payload_root == Path("/app/data")

    def test_docker_transport(self, nexus_service):
        nexus = MagicMock()
        nexus.url = "https://nexus.example.com"
        image = ImageReference(repository="myproject/myimage", reference="latest")
        result = nexus_service._docker_transport(image, nexus)
        assert result == "docker://nexus.example.com/myproject/myimage:latest"

    def test_docker_transport_digest(self, nexus_service):
        nexus = MagicMock()
        nexus.url = "https://nexus.example.com"
        image = ImageReference(repository="myproject/myimage", reference="sha256:abc123def4567890abcdef1234567890abcdef1234567890abcdef1234567890")
        result = nexus_service._docker_transport(image, nexus)
        assert result == "docker://nexus.example.com/myproject/myimage@sha256:abc123def4567890abcdef1234567890abcdef1234567890abcdef1234567890"

    def test_oci_transport(self, nexus_service):
        path = Path("/app/data/payload")
        result = nexus_service._oci_transport(path)
        assert result == "oci:/app/data/payload"

    def test_oci_transport_invalid(self, nexus_service):
        path = Path("/app/data:payload")
        with pytest.raises(Exception, match="неподдерживаемый символ"):
            nexus_service._oci_transport(path)

    def test_registry_host(self, nexus_service):
        nexus = MagicMock()
        nexus.url = "https://nexus.example.com:8443"
        result = nexus_service._registry_host(nexus)
        assert result == "nexus.example.com:8443"

    def test_registry_host_invalid(self, nexus_service):
        nexus = MagicMock()
        nexus.url = "invalid-url"
        with pytest.raises(Exception, match="некорректен"):
            nexus_service._registry_host(nexus)

    def test_validate_digest(self):
        NexusService._validate_digest("sha256:abc123def4567890abcdef1234567890abcdef1234567890abcdef1234567890")
        with pytest.raises(ValueError, match="sha256 digest"):
            NexusService._validate_digest("invalid")

    def test_compute_sha256(self, tmp_path):
        file = tmp_path / "test.txt"
        file.write_bytes(b"hello world")
        digest = NexusService._compute_sha256(file)
        assert digest.startswith("sha256:")
        assert len(digest) == 71


class TestHelmChartReference:
    def test_valid_reference(self):
        ref = HelmChartReference(repository="myproject", name="mychart", version="1.0.0")
        assert ref.repository == "myproject"
        assert ref.name == "mychart"
        assert ref.version == "1.0.0"


class TestRegistryClientFactory:
    def test_create_harbor_client(self):
        with patch("app.services.registry_client._HarborClientWrapper") as mock_wrapper:
            mock_wrapper.return_value = MagicMock()
            client = create_registry_client("harbor", MagicMock(), MagicMock())
            assert mock_wrapper.called

    def test_create_nexus_client(self):
        with patch("app.services.registry_client._NexusServiceWrapper") as mock_wrapper:
            mock_wrapper.return_value = MagicMock()
            client = create_registry_client("nexus", MagicMock(), MagicMock())
            assert mock_wrapper.called

    def test_invalid_registry_type(self):
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
            mock_profile_class.return_value = mock_profile_service
            mock_resolved = MagicMock()
            mock_resolved.url = "https://harbor.example.com"
            mock_resolved.username = "admin"
            mock_resolved.password = MagicMock()
            mock_resolved.password.get_secret_value.return_value = "password"
            mock_resolved.verify_tls = True
            mock_resolved.ca_file = None
            mock_profile_service.resolve_profile.return_value = mock_resolved
            
            wrapper = _HarborClientWrapper(mock_session, mock_settings, "profile-1")
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