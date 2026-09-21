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
        bundle_pending_signing_private_key_file=(
            root / "keys" / "source-signing-pending-private.pem"
        ),
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

    with pytest.raises(TrustPackageError) as required:
        importer.import_package(first.payload)
    assert required.value.code == "trust_package_expected_fingerprint_required"
    assert KeyManagementService(target).list_trusted_keys() == ()

    with pytest.raises(TrustPackageError) as mismatch:
        importer.import_package(
            first.payload,
            expected_fingerprint="sha256:" + "0" * 64,
        )
    assert mismatch.value.code == "trust_package_expected_fingerprint_mismatch"
    assert KeyManagementService(target).list_trusted_keys() == ()

    added = importer.import_package(
        first.payload,
        expected_fingerprint=generated.fingerprint,
    )
    assert added.mutation.action == "added"
    assert added.mutation.fingerprint == generated.fingerprint
    assert added.verification == "out_of_band"
    assert added.endorsing_fingerprint is None

    duplicate = importer.import_package(first.payload)
    assert duplicate.mutation.action == "unchanged"
    assert duplicate.verification == "existing"

    KeyManagementService(target).set_trusted_key_enabled(generated.fingerprint, False)
    enabled = importer.import_package(first.payload)
    assert enabled.mutation.action == "enabled"
    assert enabled.verification == "existing"
    states = KeyManagementService(target).list_trusted_keys()
    assert [(item.fingerprint, item.enabled) for item in states] == [
        (generated.fingerprint, True)
    ]


def test_pending_rotation_package_is_chained_to_active_trusted_source_key(
    tmp_path: Path,
) -> None:
    source = _settings(tmp_path, PortalContour.SOURCE)
    target = _settings(tmp_path, PortalContour.TARGET)
    source_keys = KeyManagementService(source)
    active = source_keys.generate_signing_private_key()

    active_package = SourceTrustPackageService(source).build()
    bootstrap = SourceTrustPackageService(target).import_package(
        active_package.payload,
        expected_fingerprint=active.fingerprint,
    )
    assert bootstrap.verification == "out_of_band"

    pending = source_keys.prepare_pending_signing_key()
    rotation_package = SourceTrustPackageService(source).build_pending()
    assert rotation_package.fingerprint == pending.fingerprint
    assert rotation_package.endorsing_fingerprint == active.fingerprint
    assert b"PRIVATE KEY" not in rotation_package.payload

    with tarfile.open(
        fileobj=io.BytesIO(rotation_package.payload),
        mode="r:gz",
    ) as archive:
        names = [member.name for member in archive.getmembers()]
    assert names == [
        "source-signing-public.pem",
        "identity.json",
        "fingerprint.sha256",
        "endorsement.json",
        "endorsement.sig",
    ]

    chained = SourceTrustPackageService(target).import_package(
        rotation_package.payload,
    )
    assert chained.mutation.action == "added"
    assert chained.mutation.fingerprint == pending.fingerprint
    assert chained.verification == "chained"
    assert chained.endorsing_fingerprint == active.fingerprint

    states = KeyManagementService(target).list_trusted_keys()
    assert [(item.fingerprint, item.enabled) for item in states] == sorted(
        [
            (active.fingerprint, True),
            (pending.fingerprint, True),
        ]
    )


def test_rotation_package_rejects_unknown_disabled_and_tampered_endorser(
    tmp_path: Path,
) -> None:
    source = _settings(tmp_path, PortalContour.SOURCE)
    source_keys = KeyManagementService(source)
    active = source_keys.generate_signing_private_key()
    source_keys.prepare_pending_signing_key()
    rotation = SourceTrustPackageService(source).build_pending()

    unknown_target = _settings(tmp_path / "unknown", PortalContour.TARGET)
    with pytest.raises(TrustPackageError) as unknown:
        SourceTrustPackageService(unknown_target).import_package(rotation.payload)
    assert unknown.value.code == "trust_package_endorser_untrusted"

    disabled_target = _settings(tmp_path / "disabled", PortalContour.TARGET)
    disabled_importer = SourceTrustPackageService(disabled_target)
    disabled_importer.import_package(
        SourceTrustPackageService(source).build().payload,
        expected_fingerprint=active.fingerprint,
    )
    KeyManagementService(disabled_target).set_trusted_key_enabled(
        active.fingerprint,
        False,
    )
    with pytest.raises(TrustPackageError) as disabled:
        disabled_importer.import_package(rotation.payload)
    assert disabled.value.code == "trust_package_endorser_untrusted"

    tampered_target = _settings(tmp_path / "tampered", PortalContour.TARGET)
    tampered_importer = SourceTrustPackageService(tampered_target)
    tampered_importer.import_package(
        SourceTrustPackageService(source).build().payload,
        expected_fingerprint=active.fingerprint,
    )
    tampered_payload = _rewrite(
        rotation.payload,
        "endorsement.sig",
        b"\x00" * 64,
    )
    with pytest.raises(TrustPackageError) as tampered:
        tampered_importer.import_package(tampered_payload)
    assert tampered.value.code == "trust_package_endorsement_invalid"


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
