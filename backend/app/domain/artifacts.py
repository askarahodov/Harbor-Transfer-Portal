from enum import StrEnum
from typing import Any


class ArtifactKind(StrEnum):
    CONTAINER_IMAGE = "container-image"
    HELM_CHART = "helm-chart"
    UNKNOWN_OCI = "unknown-oci"


def classify_artifact_kind(
    artifact_type: str | None,
    media_type: str | None,
    extra_attrs: dict[str, Any] | None = None,
) -> ArtifactKind:
    normalized_type = (artifact_type or "").upper()
    haystack = " ".join(filter(None, [artifact_type, media_type])).casefold()
    annotations = (extra_attrs or {}).get("annotations")
    if isinstance(annotations, dict):
        haystack += " " + " ".join(str(value).casefold() for value in annotations.values())

    if normalized_type in {"CHART", "HELM", "HELM_CHART"} or "helm" in haystack:
        return ArtifactKind.HELM_CHART
    if normalized_type == "IMAGE" or "image" in haystack or "container" in haystack:
        return ArtifactKind.CONTAINER_IMAGE
    return ArtifactKind.UNKNOWN_OCI
