from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

from app.config import Settings
from app.services.nexus_service import (
    HelmChartReference,
    ImageReference,
    NexusService,
)
from app.services.registry_client import create_registry_client

pytestmark = pytest.mark.skipif(
    os.environ.get("HTP_RUN_NEXUS_E2E") != "1",
    reason="Requires a configured Nexus test registry and Docker daemon",
)


@pytest.fixture(scope="session")
def nexus_container():
    """Start a Nexus container for integration tests."""
    container = DockerContainer("sonatype/nexus3:latest")
    container.with_exposed_ports(8081, 8082, 8083)
    container.with_env("INSTALL4J_ADD_VM_PARAMS", "-Xms2g -Xms2g -XX:MaxDirectMemorySize=3g")
    container.with_volume_mapping(
        tempfile.mkdtemp(),
        "/nexus-data",
        "rw",
    )
    container.start()

    # Wait for Nexus to be ready (can take 15-20 minutes)
    wait_for_logs(container, "Started Sonatype Nexus", timeout=1800)

    # Get the mapped ports
    ui_port = container.get_exposed_port(8081)
    docker_port = container.get_exposed_port(8082)

    yield {
        "container": container,
        "ui_url": f"http://localhost:{ui_port}",
        "docker_url": f"http://localhost:{docker_port}",
        "host": "localhost",
        "docker_port": docker_port,
    }

    container.stop()


@pytest.fixture(scope="function")
def settings_with_nexus(nexus_container):
    """Create settings configured for the test Nexus instance."""
    settings = Settings(
        nexus_url=f"http://localhost:{nexus_container['docker_port']}",
        nexus_user="admin",
        nexus_password="admin123",
        nexus_verify_tls=False,
        nexus_ca_file=None,
        skopeo_binary="skopeo",
        helm_binary="helm",
        skopeo_timeout_seconds=120.0,
        skopeo_output_limit_bytes=65536,
        skopeo_payload_root=Path("/tmp/nexus-test-payload"),
        skopeo_temp_root=Path("/tmp/nexus-test-tmp"),
    )
    return settings


@pytest.fixture
def session_factory():
    """Create a temporary SQLite database for tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Import and create tables
    from app.db.models import Base

    Base.metadata.create_all(engine)

    yield sessionmaker(bind=engine, class_=Session)


class TestNexusE2E:
    """End-to-end tests for Nexus integration."""

    @pytest.mark.asyncio
    async def test_nexus_service_inspect_image(self, settings_with_nexus, nexus_container):
        """Test inspecting an image in Nexus."""
        # This test requires a real Nexus with some images
        # For now, we test that the service can be created
        service = NexusService(
            session=MagicMock(),
            settings=settings_with_nexus,
            nexus_profile_id=None,
        )

        # Test docker transport URL construction
        image = ImageReference(repository="library/busybox", reference="latest")
        nexus_settings = service._resolve_nexus()
        transport = service._docker_transport(image, nexus_settings)
        assert "docker://" in transport
        assert "library/busybox" in transport

    @pytest.mark.asyncio
    async def test_helm_chart_reference(self):
        """Test HelmChartReference dataclass."""
        ref = HelmChartReference(
            repository="myproject",
            name="mychart",
            version="1.0.0",
        )
        assert ref.repository == "myproject"
        assert ref.name == "mychart"
        assert ref.version == "1.0.0"

    def test_registry_client_factory_creates_nexus(self, settings_with_nexus):
        """Test that factory creates Nexus client."""

        mock_session = MagicMock()
        client = create_registry_client(
            "nexus",
            mock_session,
            settings_with_nexus,
            profile_id="test-profile",
        )
        from app.services.registry_client import _NexusServiceWrapper

        assert isinstance(client, _NexusServiceWrapper)


class TestNexusE2EIntegration:
    """Full integration tests requiring real Nexus."""

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_full_export_import_cycle(
        self, settings_with_nexus, nexus_container, session_factory
    ):
        """
        Test full export/import cycle with Nexus:
        1. Push an image to Nexus
        2. Export it via Portal
        3. Import it back to Nexus
        3. Verify content matches
        """
        # This is a placeholder for the full E2E test
        # Would require:
        # 1. Push test image to Nexus (docker push)
        # 2. Run export via ExportOrchestrator
        # 3. Run import via ImportOrchestrator
        # 4. Verify imported image matches original
        pytest.skip("Full E2E test requires skopeo/helm in container")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "not slow"])
