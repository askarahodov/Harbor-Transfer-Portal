from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def test_compose_browser_listener_is_loopback_only_by_default() -> None:
    expected = "${PORTAL_HTTP_BIND:-127.0.0.1}:${PORTAL_HTTP_PORT:-8080}:80"

    for relative_path in ("compose.yaml", "deploy/offline/compose.yaml"):
        assert expected in _read(relative_path)


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
