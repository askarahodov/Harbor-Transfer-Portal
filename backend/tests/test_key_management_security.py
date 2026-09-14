from pathlib import Path

import pytest

from app.config import PortalContour, Settings
from app.services.key_management import KeyManagementError, KeyManagementService


def test_oversized_managed_key_is_rejected_before_read_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trust_dir = tmp_path / "trusted"
    trust_dir.mkdir()
    oversized = trust_dir / "oversized.pem"
    oversized.write_bytes(b"x" * (16 * 1024 + 1))

    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == oversized:
            raise AssertionError("oversized key file must be rejected from stat before read_bytes")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    service = KeyManagementService(
        Settings(
            portal_contour=PortalContour.TARGET,
            bundle_trusted_public_keys_dir=trust_dir,
        )
    )

    with pytest.raises(KeyManagementError) as exc_info:
        service.list_trusted_keys()

    assert exc_info.value.code == "key_material_size_invalid"
