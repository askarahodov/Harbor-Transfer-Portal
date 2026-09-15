from app.api.harbor import _artifact_response
from app.services.harbor_client import HarborArtifact, HarborTag


def test_null_tags_from_harbor_are_normalized() -> None:
    artifact = HarborArtifact.model_validate(
        {
            "digest": "sha256:" + "a" * 64,
            "type": "IMAGE",
            "tags": None,
        }
    )

    assert artifact.tags == []


def test_helm_exact_version_survives_harbor_metadata_normalization() -> None:
    artifact = HarborArtifact.model_validate(
        {
            "digest": "sha256:" + "b" * 64,
            "media_type": "application/vnd.cncf.helm.config.v1+json",
            "manifest_media_type": "application/vnd.oci.image.manifest.v1+json",
            "tags": [{"name": "1.2.3"}],
        }
    )

    response = _artifact_response("team", "charts/app", artifact)

    assert artifact.type == "CHART"
    assert response.kind.value == "helm-chart"
    assert response.references == ["1.2.3"]


def test_image_keeps_all_exact_tags_in_deterministic_order() -> None:
    artifact = HarborArtifact.model_validate(
        {
            "digest": "sha256:" + "c" * 64,
            "media_type": "application/vnd.docker.container.image.v1+json",
            "tags": [
                {"name": "latest"},
                {"name": "1.10.0"},
                {"name": "1.2.0"},
            ],
        }
    )

    response = _artifact_response("team", "apps/api", artifact)

    assert artifact.type == "IMAGE"
    assert response.kind.value == "container-image"
    assert response.references == ["1.10.0", "1.2.0", "latest"]


def test_harbor_artifact_type_and_annotations_can_recover_helm_kind() -> None:
    artifact = HarborArtifact.model_validate(
        {
            "digest": "sha256:" + "d" * 64,
            "artifact_type": "application/vnd.example.release",
            "annotations": {
                "org.opencontainers.image.description": "Helm chart release metadata"
            },
            "tags": [HarborTag(name="2026.09").model_dump()],
        }
    )

    response = _artifact_response("team", "charts/release", artifact)

    assert artifact.type == "CHART"
    assert response.kind.value == "helm-chart"
    assert response.references == ["2026.09"]
