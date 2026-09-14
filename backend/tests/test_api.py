from fastapi.testclient import TestClient

from app import __version__
from app.config import PortalContour, Settings
from app.main import create_app


def make_client(**overrides: object) -> TestClient:
    settings = Settings(portal_contour=PortalContour.SOURCE, **overrides)
    return TestClient(create_app(settings))


def test_health_response_is_stable_and_contains_no_secrets() -> None:
    secret = "must-not-leak"
    client = make_client(
        harbor_url="https://harbor.local",
        harbor_user="robot$portal",
        harbor_password=secret,
    )

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "Harbor Transfer Portal",
        "version": __version__,
        "contour": "SOURCE",
    }
    assert __version__ == "1.0.0"
    assert secret not in response.text
    assert "robot$portal" not in response.text
    assert "harbor.local" not in response.text


def test_ready_checks_only_local_configuration() -> None:
    response = make_client().get("/api/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"configuration": "ok"},
    }


def test_not_found_uses_stable_error_shape() -> None:
    response = make_client().get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "http_404", "message": "Not Found"}
    }
