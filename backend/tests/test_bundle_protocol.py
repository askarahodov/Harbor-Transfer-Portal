import tarfile
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain.bundle import (
    BundleManifest,
    BundleSource,
    ContainerImageArtifact,
    OperationStatus,
    OperationType,
)
from app.domain.operations import IllegalOperationTransition, validate_transition
from app.domain.protocol import canonical_manifest_bytes, validate_archive_members


def make_manifest() -> BundleManifest:
    return BundleManifest(
        schema_version="1.0",
        delivery_id="DELIVERY-20260911-ABC123",
        created_at=datetime(2026, 9, 11, 6, 0, tzinfo=UTC),
        created_by="operator",
        source=BundleSource(contour="SOURCE", harbor="harbor.source.local"),
        artifacts=[
            ContainerImageArtifact(
                repository="project/app",
                reference="1.0.0",
                source_digest="sha256:" + "a" * 64,
                payload_path="images/app",
                payload_sha256="b" * 64,
                payload_size=123,
            )
        ],
    )


def _required_members() -> list[tarfile.TarInfo]:
    return [
        tarfile.TarInfo("manifest.json"),
        tarfile.TarInfo("manifest.sig"),
        tarfile.TarInfo("checksums.sha256"),
    ]


def test_manifest_round_trip_and_canonical_bytes_are_deterministic() -> None:
    manifest = make_manifest()
    restored = BundleManifest.model_validate_json(manifest.model_dump_json())
    assert restored == manifest
    assert canonical_manifest_bytes(restored) == canonical_manifest_bytes(manifest)


def test_payload_path_rejects_traversal() -> None:
    with pytest.raises(ValidationError):
        ContainerImageArtifact(
            repository="project/app",
            reference="1.0.0",
            source_digest="sha256:" + "a" * 64,
            payload_path="../escape",
            payload_sha256="b" * 64,
            payload_size=123,
        )


def test_operation_state_machine_rejects_illegal_transition() -> None:
    validate_transition(
        OperationType.EXPORT,
        OperationStatus.CREATED,
        OperationStatus.VALIDATING,
    )
    with pytest.raises(IllegalOperationTransition):
        validate_transition(
            OperationType.EXPORT,
            OperationStatus.CREATED,
            OperationStatus.COMPLETED,
        )


def test_archive_validation_rejects_links_and_traversal() -> None:
    required = _required_members()
    validate_archive_members(required)

    link = tarfile.TarInfo("images/link")
    link.type = tarfile.SYMTYPE
    with pytest.raises(ValueError):
        validate_archive_members([*required, link])

    with pytest.raises(ValueError):
        validate_archive_members([*required, tarfile.TarInfo("../escape")])


@pytest.mark.parametrize(
    "name",
    [
        "./manifest.json",
        "images//app/index.json",
        "images/./app/index.json",
    ],
)
def test_archive_validation_rejects_noncanonical_path_aliases(name: str) -> None:
    with pytest.raises(ValueError, match="canonical"):
        validate_archive_members([*_required_members(), tarfile.TarInfo(name)])


def test_archive_validation_rejects_file_directory_alias_collision() -> None:
    directory = tarfile.TarInfo("images/app/")
    directory.type = tarfile.DIRTYPE
    file_member = tarfile.TarInfo("images/app")

    with pytest.raises(ValueError, match="duplicate archive member"):
        validate_archive_members([*_required_members(), directory, file_member])


def test_archive_validation_rejects_members_outside_payload_layout() -> None:
    unexpected = tarfile.TarInfo("keys/unexpected.pem")
    with pytest.raises(ValueError, match="outside payload layout"):
        validate_archive_members([*_required_members(), unexpected])


def test_archive_validation_rejects_empty_payload_directories() -> None:
    empty_directory = tarfile.TarInfo("images/empty/")
    empty_directory.type = tarfile.DIRTYPE
    with pytest.raises(ValueError, match="empty or undeclared"):
        validate_archive_members([*_required_members(), empty_directory])


def test_nexus_source_has_explicit_origin_and_no_harbor_alias() -> None:
    legacy = make_manifest()
    legacy_bytes = canonical_manifest_bytes(legacy)
    assert b'"registry_type"' not in legacy_bytes
    assert b'"registry"' not in legacy_bytes
    assert b'"harbor":"harbor.source.local"' in legacy_bytes

    nexus = legacy.model_copy(deep=True)
    nexus.schema_version = "1.1"
    nexus.source = BundleSource(
        contour="SOURCE",
        registry_type="nexus",
        registry="https://docker-nexus.internal",
    )
    payload = canonical_manifest_bytes(nexus)
    assert b'"registry_type":"nexus"' in payload
    assert b'"registry":"https://docker-nexus.internal"' in payload
    assert b'"harbor"' not in payload
    assert BundleManifest.model_validate_json(payload) == nexus
    assert canonical_manifest_bytes(BundleManifest.model_validate_json(payload)) == payload


def test_nexus_source_rejects_misleading_harbor_and_missing_origin() -> None:
    with pytest.raises(ValidationError):
        BundleSource(contour="SOURCE", registry_type="nexus", harbor="nexus.example")
    with pytest.raises(ValidationError):
        BundleSource(contour="SOURCE", registry_type="nexus")
    with pytest.raises(ValidationError):
        BundleSource(contour="SOURCE", registry_type="harbor", harbor="harbor", registry="other")
