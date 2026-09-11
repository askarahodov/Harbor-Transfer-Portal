import pytest
from pydantic import ValidationError

from app.config import PortalContour, Settings


def test_invalid_portal_contour_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORTAL_CONTOUR", "INVALID")

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert "PORTAL_CONTOUR" in str(exc_info.value).upper()


def test_supported_contours_are_source_and_target() -> None:
    assert {item.value for item in PortalContour} == {"SOURCE", "TARGET"}
