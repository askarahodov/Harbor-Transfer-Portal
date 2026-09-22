from __future__ import annotations

import asyncio
from typing import cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.domain.bundle import ContainerImageArtifact, HelmChartArtifact
from app.domain.imports import ImportPreviewState
from app.services.helm_oci_service import (
    HelmOciService,
    HelmServiceError,
    HelmTargetInspection,
    HelmTargetState,
)
from app.services.import_artifact_classifier import ImportArtifactClassifier
from app.services.skopeo_service import (
    SkopeoService,
    SkopeoServiceError,
    TargetInspection,
    TargetState,
)

DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
PAYLOAD_SHA = "c" * 64


class FakeSkopeo:
    def __init__(self, result: TargetInspection | Exception) -> None:
        self.result = result

    async def inspect_target(self, *_args, **_kwargs) -> TargetInspection:  # type: ignore[no-untyped-def]
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeHelm:
    def __init__(self, result: HelmTargetInspection | Exception) -> None:
        self.result = result

    async def inspect_target(self, *_args, **_kwargs) -> HelmTargetInspection:  # type: ignore[no-untyped-def]
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def image() -> ContainerImageArtifact:
    return ContainerImageArtifact(
        repository="team/app",
        reference="1.0.0",
        source_digest=DIGEST,
        payload_path="images/1",
        payload_sha256=PAYLOAD_SHA,
        payload_size=123,
    )


def chart() -> HelmChartArtifact:
    return HelmChartArtifact(
        repository="team/charts",
        name="sample",
        version="1.2.3",
        source_digest=DIGEST,
        payload_path="charts/1.tgz",
        payload_sha256=PAYLOAD_SHA,
        payload_size=456,
    )


def classifier(
    skopeo_result: TargetInspection | Exception,
    helm_result: HelmTargetInspection | Exception,
) -> ImportArtifactClassifier:
    factory = sessionmaker(
        bind=create_engine("sqlite+pysqlite:///:memory:"),
        class_=Session,
        expire_on_commit=False,
    )
    return ImportArtifactClassifier(
        factory,
        lambda _session: cast(SkopeoService, FakeSkopeo(skopeo_result)),
        lambda _session: cast(HelmOciService, FakeHelm(helm_result)),
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (TargetState.ABSENT, ImportPreviewState.NEW),
        (TargetState.SAME_DIGEST, ImportPreviewState.SAME),
        (TargetState.CONFLICTING_DIGEST, ImportPreviewState.CONFLICT),
        (TargetState.PRESENT, ImportPreviewState.UNKNOWN),
    ],
)
def test_classifies_container_target_state(
    state: TargetState,
    expected: ImportPreviewState,
) -> None:
    service = classifier(
        TargetInspection(state, OTHER_DIGEST if state is not TargetState.ABSENT else None),
        HelmTargetInspection(HelmTargetState.ABSENT, None),
    )

    [result] = asyncio.run(service.classify([image()]))

    assert result.index == 0
    assert result.classification is expected
    assert result.repository == "team/app"
    assert result.reference == "1.0.0"
    assert result.expected_digest == DIGEST
    assert result.payload_size == 123


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (HelmTargetState.ABSENT, ImportPreviewState.NEW),
        (HelmTargetState.SAME_DIGEST, ImportPreviewState.SAME),
        (HelmTargetState.CONFLICTING_DIGEST, ImportPreviewState.CONFLICT),
        (HelmTargetState.PRESENT, ImportPreviewState.UNKNOWN),
    ],
)
def test_classifies_helm_target_state(
    state: HelmTargetState,
    expected: ImportPreviewState,
) -> None:
    service = classifier(
        TargetInspection(TargetState.ABSENT, None),
        HelmTargetInspection(state, OTHER_DIGEST if state is not HelmTargetState.ABSENT else None),
    )

    [result] = asyncio.run(service.classify([chart()]))

    assert result.index == 0
    assert result.classification is expected
    assert result.repository == "team/charts"
    assert result.name == "sample"
    assert result.version == "1.2.3"
    assert result.expected_digest == DIGEST
    assert result.payload_size == 456


def test_inspection_errors_are_fail_closed_per_artifact_and_do_not_abort_manifest() -> None:
    service = classifier(
        SkopeoServiceError("skopeo_inspect_failed", "image inspection failed"),
        HelmServiceError("helm_inspect_failed", "chart inspection failed"),
    )

    results = asyncio.run(service.classify([image(), chart()]))

    assert [item.index for item in results] == [0, 1]
    assert [item.classification for item in results] == [
        ImportPreviewState.ERROR,
        ImportPreviewState.ERROR,
    ]
    assert [item.error_code for item in results] == [
        "skopeo_inspect_failed",
        "helm_inspect_failed",
    ]
    assert [item.message for item in results] == [
        "image inspection failed",
        "chart inspection failed",
    ]
