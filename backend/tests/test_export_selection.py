from __future__ import annotations

import pytest

from app.domain.artifacts import ArtifactKind
from app.schemas.exports import ExportArtifactSelection
from app.services.export_selection import (
    ExportSelectionResolutionError,
    resolve_export_selection,
)
from app.services.harbor_client import HarborArtifact, HarborClientError

DIGEST = "sha256:" + "a" * 64
CHANGED_DIGEST = "sha256:" + "b" * 64


class FakeLookup:
    def __init__(
        self,
        artifact: HarborArtifact | None = None,
        error: HarborClientError | None = None,
    ) -> None:
        self.artifact = artifact
        self.error = error
        self.calls = 0

    def get_artifact(self, project: str, repository: str, reference: str) -> HarborArtifact:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert (project, repository, reference) == ("team", "app", "1.0.0")
        assert self.artifact is not None
        return self.artifact


def selection(kind: ArtifactKind = ArtifactKind.CONTAINER_IMAGE) -> ExportArtifactSelection:
    return ExportArtifactSelection(
        kind=kind,
        project="team",
        repository="app",
        reference="1.0.0",
        digest=DIGEST,
    )


def test_resolves_supported_artifact_with_one_harbor_lookup() -> None:
    lookup = FakeLookup(HarborArtifact(digest=DIGEST, type="IMAGE", size=123))

    resolved = resolve_export_selection(lookup, selection())

    assert lookup.calls == 1
    assert resolved.kind is ArtifactKind.CONTAINER_IMAGE
    assert resolved.project == "team"
    assert resolved.repository == "app"
    assert resolved.reference == "1.0.0"
    assert resolved.digest == DIGEST
    assert resolved.size_bytes == 123


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (HarborClientError("not_found", "missing"), "export_source_not_found"),
        (HarborClientError("unauthorized", "denied"), "harbor_auth_failed"),
        (HarborClientError("rate_limited", "slow down"), "harbor_rate_limited"),
        (HarborClientError("unexpected", "boom"), "harbor_error"),
    ],
)
def test_preserves_export_harbor_error_contract(
    error: HarborClientError,
    expected_code: str,
) -> None:
    lookup = FakeLookup(error=error)

    with pytest.raises(ExportSelectionResolutionError) as captured:
        resolve_export_selection(lookup, selection())

    assert lookup.calls == 1
    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    ("artifact", "expected_code"),
    [
        (
            HarborArtifact(
                digest=DIGEST,
                type="SBOM",
                media_type="application/vnd.example.opaque",
            ),
            "export_artifact_unsupported",
        ),
        (
            HarborArtifact(digest=DIGEST, type="CHART"),
            "export_artifact_kind_changed",
        ),
        (
            HarborArtifact(digest=CHANGED_DIGEST, type="IMAGE"),
            "export_source_changed",
        ),
    ],
)
def test_rejects_source_identity_drift(
    artifact: HarborArtifact,
    expected_code: str,
) -> None:
    lookup = FakeLookup(artifact)

    with pytest.raises(ExportSelectionResolutionError) as captured:
        resolve_export_selection(lookup, selection())

    assert lookup.calls == 1
    assert captured.value.code == expected_code
