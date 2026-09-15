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
    attrs = extra_attrs or {}
    signals = [artifact_type, media_type]

    for key in ("artifact_type", "manifest_media_type"):
        value = attrs.get(key)
        if isinstance(value, str):
            signals.append(value)

    annotations = attrs.get("annotations")
    if isinstance(annotations, dict):
        signals.extend(str(value) for value in annotations.values())

    haystack = " ".join(value for value in signals if value).casefold()

    if normalized_type in {"CHART", "HELM", "HELM_CHART"} or "helm" in haystack:
        return ArtifactKind.HELM_CHART
    if normalized_type == "IMAGE" or "image" in haystack or "container" in haystack:
        return ArtifactKind.CONTAINER_IMAGE
    return ArtifactKind.UNKNOWN_OCI
