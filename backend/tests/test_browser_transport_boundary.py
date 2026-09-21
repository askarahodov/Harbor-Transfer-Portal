from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def test_compose_uses_host_network_with_loopback_backend() -> None:
    for relative_path in ("compose.yaml", "deploy/offline/compose.yaml"):
        compose = _read(relative_path)
        assert compose.count("network_mode: host") == 2
        assert "- --host\n      - 127.0.0.1" in compose
        assert "- --port\n      - \"8000\"" in compose
        assert "PORTAL_HTTP_BIND: ${PORTAL_HTTP_BIND:-127.0.0.1}" in compose
        assert "PORTAL_HTTP_PORT: ${PORTAL_HTTP_PORT:-8080}" in compose
        assert "${PORTAL_HTTP_BIND:-127.0.0.1}:${PORTAL_HTTP_PORT:-8080}:80" not in compose


def test_frontend_listener_and_api_proxy_stay_on_configured_host_boundary() -> None:
    nginx = _read("frontend/nginx.conf")

    assert "listen ${PORTAL_HTTP_BIND}:${PORTAL_HTTP_PORT};" in nginx
    assert "proxy_pass http://127.0.0.1:8000;" in nginx


def test_frontend_proxy_streams_browser_bundle_without_smaller_body_limit() -> None:
    nginx = _read("frontend/nginx.conf")

    assert "client_max_body_size 0;" in nginx
    assert "proxy_request_buffering off;" in nginx
    assert "client_body_timeout 300s;" in nginx
    assert "proxy_send_timeout 300s;" in nginx


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
