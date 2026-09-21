from __future__ import annotations

import gzip
import io
import json
import tarfile
from pathlib import Path

import pytest

from app.config import PortalContour, Settings
from app.services.key_management import KeyManagementService
from app.services.source_trust_package import SourceTrustPackageService, TrustPackageError


def _settings(tmp_path: Path, contour: PortalContour) -> Settings:
    root = tmp_path / contour.value.lower()
    return Settings(
        _env_file=None,
        portal_contour=contour,
        bundle_signing_private_key_file=root / "keys" / "source-signing-private.pem",
        bundle_trusted_public_keys_dir=root / "keys" / "trusted-source",
        bundle_trust_package_max_bytes=131_072,
    )


def _rewrite(payload: bytes, name: str, replacement: bytes) -> bytes:
    entries: list[tuple[str, bytes]] = []
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            stream = archive.extractfile(member)
            assert stream is not None
            content = stream.read()
            entries.append((member.name, replacement if member.name == name else content))

    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for entry_name, content in entries:
                info = tarfile.TarInfo(entry_name)
                info.size = len(content)
                info.mode = 0o600
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = 0
                archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def test_trust_package_is_deterministic_idempotent_and_reenables_existing_identity(
    tmp_path: Path,
) -> None:
    source = _settings(tmp_path, PortalContour.SOURCE)
    target = _settings(tmp_path, PortalContour.TARGET)
    generated = KeyManagementService(source).generate_signing_private_key()

    builder = SourceTrustPackageService(source)
    first = builder.build()
    second = builder.build()
    assert first.payload == second.payload
    assert first.fingerprint == generated.fingerprint

    importer = SourceTrustPackageService(target)
    added = importer.import_package(first.payload)
    assert added.action == "added"
    assert added.fingerprint == generated.fingerprint

    duplicate = importer.import_package(first.payload)
    assert duplicate.action == "unchanged"

    KeyManagementService(target).set_trusted_key_enabled(generated.fingerprint, False)
    enabled = importer.import_package(first.payload)
    assert enabled.action == "enabled"
    states = KeyManagementService(target).list_trusted_keys()
    assert [(item.fingerprint, item.enabled) for item in states] == [
        (generated.fingerprint, True)
    ]


def test_trust_package_bounds_highly_compressible_pax_headers_before_parse(
    tmp_path: Path,
) -> None:
    target = _settings(tmp_path, PortalContour.TARGET)
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(
            fileobj=compressed,
            mode="w",
            format=tarfile.PAX_FORMAT,
        ) as archive:
            info = tarfile.TarInfo("source-signing-public.pem")
            info.size = 1
            info.pax_headers = {"comment": "A" * 200_000}
            archive.addfile(info, io.BytesIO(b"x"))

    payload = output.getvalue()
    assert len(payload) < target.bundle_trust_package_max_bytes

    with pytest.raises(TrustPackageError) as exc:
        SourceTrustPackageService(target).import_package(payload)
    assert exc.value.code == "trust_package_decompressed_size_invalid"
    assert KeyManagementService(target).list_trusted_keys() == ()


def test_trust_package_rejects_fingerprint_tamper(tmp_path: Path) -> None:
    source = _settings(tmp_path, PortalContour.SOURCE)
    target = _settings(tmp_path, PortalContour.TARGET)
    KeyManagementService(source).generate_signing_private_key()
    package = SourceTrustPackageService(source).build()

    identity = {
        "algorithm": "Ed25519",
        "fingerprint": "sha256:" + "0" * 64,
        "kind": "harbor-transfer-portal-source-identity",
        "schema_version": "1.0",
    }
    tampered = _rewrite(
        package.payload,
        "identity.json",
        (
            json.dumps(identity, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8"),
    )

    with pytest.raises(TrustPackageError) as exc:
        SourceTrustPackageService(target).import_package(tampered)
    assert exc.value.code == "trust_package_fingerprint_mismatch"
    assert KeyManagementService(target).list_trusted_keys() == ()


def test_trust_package_rejects_private_key_substitution(tmp_path: Path) -> None:
    source = _settings(tmp_path, PortalContour.SOURCE)
    target = _settings(tmp_path, PortalContour.TARGET)
    KeyManagementService(source).generate_signing_private_key()
    package = SourceTrustPackageService(source).build()
    private_pem = source.bundle_signing_private_key_file.read_bytes()

    tampered = _rewrite(
        package.payload,
        "source-signing-public.pem",
        private_pem,
    )
    with pytest.raises(TrustPackageError) as exc:
        SourceTrustPackageService(target).import_package(tampered)
    assert exc.value.code == "trust_package_public_key_invalid"
    assert KeyManagementService(target).list_trusted_keys() == ()
