import os
import stat
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import PortalContour, Settings
from app.services.key_management import KeyManagementService
from app.services.key_material import ed25519_public_key_fingerprint


def _private_pem(key: Ed25519PrivateKey) -> str:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def _service(tmp_path: Path) -> KeyManagementService:
    return KeyManagementService(
        Settings(
            _env_file=None,
            portal_contour=PortalContour.SOURCE,
            bundle_signing_private_key_file=tmp_path / "keys" / "signing.pem",
            bundle_trusted_public_keys_dir=tmp_path / "keys" / "trusted",
        )
    )


def test_rotation_does_not_require_post_commit_chmod(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    first = Ed25519PrivateKey.generate()
    second = Ed25519PrivateKey.generate()
    service.install_signing_private_key(_private_pem(first))

    signing_path = service.settings.bundle_signing_private_key_file.absolute()
    real_chmod = os.chmod

    def fail_if_final_path(path: os.PathLike[str] | str, mode: int) -> None:
        if Path(path) == signing_path:
            raise OSError("simulated post-commit chmod failure")
        real_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", fail_if_final_path)

    mutation = service.install_signing_private_key(_private_pem(second))

    expected = ed25519_public_key_fingerprint(second.public_key())
    assert mutation.action == "rotated"
    assert mutation.fingerprint == expected
    assert service.signing_status().fingerprint == expected
    assert stat.S_IMODE(signing_path.stat().st_mode) == 0o600


def test_directory_sync_failure_after_rename_is_not_false_rotation_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    first = Ed25519PrivateKey.generate()
    second = Ed25519PrivateKey.generate()
    service.install_signing_private_key(_private_pem(first))

    def fail_directory_sync(_path: Path) -> None:
        raise OSError("simulated directory fsync failure after rename")

    monkeypatch.setattr(service, "_fsync_directory", fail_directory_sync)

    mutation = service.install_signing_private_key(_private_pem(second))

    expected = ed25519_public_key_fingerprint(second.public_key())
    assert mutation.action == "rotated"
    assert mutation.fingerprint == expected
    assert service.signing_status().fingerprint == expected
