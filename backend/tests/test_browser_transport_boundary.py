from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def test_compose_publishes_only_frontend_on_portable_bridge_network() -> None:
    for relative_path in ("compose.yaml", "deploy/offline/compose.yaml"):
        compose = _read(relative_path)
        assert "network_mode: host" not in compose
        assert "- --host\n      - 0.0.0.0" in compose
        assert "- --port\n      - \"8000\"" in compose
        assert compose.count("    ports:\n") == 1
        assert '"${PORTAL_HTTP_BIND:-127.0.0.1}:${PORTAL_HTTP_PORT:-8080}:8080"' in compose


def test_frontend_listener_and_api_proxy_use_container_boundary() -> None:
    nginx = _read("frontend/nginx.conf")

    assert "listen 0.0.0.0:8080;" in nginx
    assert "proxy_pass http://backend:8000;" in nginx
    assert "proxy_pass http://127.0.0.1:8000;" not in nginx


def test_frontend_proxy_drops_untrusted_forwarding_headers() -> None:
    nginx = _read("frontend/nginx.conf")

    assert "proxy_set_header X-Forwarded-For $remote_addr;" in nginx
    assert "$proxy_add_x_forwarded_for" not in nginx
    assert 'proxy_set_header X-Forwarded-Proto "";' in nginx
    assert "proxy_set_header X-Forwarded-Proto $scheme;" not in nginx


def test_example_environment_declares_safe_browser_transport_defaults() -> None:
    environment = _read(".env.example")

    assert "PORTAL_HTTP_BIND=127.0.0.1" in environment
    assert "PORTAL_BROWSER_SCHEME=http" in environment
