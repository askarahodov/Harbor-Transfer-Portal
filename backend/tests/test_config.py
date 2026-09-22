import pytest
from pydantic import ValidationError

from app.config import BrowserScheme, PortalContour, Settings


def test_invalid_portal_contour_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORTAL_CONTOUR", "INVALID")

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert "PORTAL_CONTOUR" in str(exc_info.value).upper()


def test_supported_contours_are_source_and_target() -> None:
    assert {item.value for item in PortalContour} == {"SOURCE", "TARGET"}


def test_invalid_browser_scheme_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORTAL_BROWSER_SCHEME", "ftp")

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert "PORTAL_BROWSER_SCHEME" in str(exc_info.value).upper()


def test_supported_browser_schemes_are_http_and_https() -> None:
    assert {item.value for item in BrowserScheme} == {"http", "https"}


def test_plain_http_requires_loopback_bind() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            portal_http_bind="0.0.0.0",
            portal_browser_scheme=BrowserScheme.HTTP,
        )

    assert "PORTAL_BROWSER_SCHEME=https" in str(exc_info.value)


def test_https_allows_explicit_non_loopback_bind() -> None:
    settings = Settings(
        _env_file=None,
        portal_http_bind="10.20.30.40",
        portal_browser_scheme=BrowserScheme.HTTPS,
    )

    assert settings.portal_http_bind == "10.20.30.40"
    assert settings.portal_browser_scheme is BrowserScheme.HTTPS


def test_transfer_storage_retention_defaults_are_bounded() -> None:
    settings = Settings(_env_file=None)

    assert settings.export_bundle_retention_seconds == 7 * 24 * 60 * 60
    assert settings.import_bundle_retention_seconds == 7 * 24 * 60 * 60
    assert settings.storage_cleanup_interval_seconds == 60 * 60


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("export_bundle_retention_seconds", 3599),
        ("import_bundle_retention_seconds", 3599),
        ("storage_cleanup_interval_seconds", 59),
    ],
)
def test_transfer_storage_retention_rejects_unbounded_low_values(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})
