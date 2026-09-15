from pathlib import Path

import pytest

from app.db.base import Base
from app.db.session import create_db_engine, create_session_factory
from app.schemas.imports import ImportArtifactDestinationOverride, ImportDestinationPlanRequest
from app.services.destination_mapping_policy import (
    DestinationMappingPolicyError,
    DestinationMappingPolicyService,
)


def _factory(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'mapping-policy.db'}")
    Base.metadata.create_all(engine)
    return create_session_factory(engine)


def test_destination_mapping_policy_persists_and_increments_revision(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        service = DestinationMappingPolicyService(session)
        initial = service.resolve()
        assert initial.revision == 0
        assert initial.container_image_project is None
        assert initial.helm_chart_project is None
        assert initial.project_mappings == {}

        first = service.update(
            {
                "container_image_project": "images-default",
                "helm_chart_project": "helm-default",
                "project_mappings": {"source-a": "target-a"},
            }
        )
        session.commit()
        assert first.snapshot.revision == 1
        assert set(first.changed_fields) == {
            "container_image_project",
            "helm_chart_project",
            "project_mappings",
        }

    with factory() as session:
        persisted = DestinationMappingPolicyService(session).resolve()
        assert persisted.revision == 1
        assert persisted.container_image_project == "images-default"
        assert persisted.helm_chart_project == "helm-default"
        assert persisted.project_mappings == {"source-a": "target-a"}

        no_op = DestinationMappingPolicyService(session).update(
            {"container_image_project": "images-default"}
        )
        assert no_op.changed_fields == ()
        assert no_op.snapshot.revision == 1

        second = DestinationMappingPolicyService(session).update(
            {"container_image_project": "images-v2"}
        )
        session.commit()
        assert second.snapshot.revision == 2
        assert second.before == {"container_image_project": "images-default"}
        assert second.after == {"container_image_project": "images-v2"}


def test_resolve_request_uses_policy_as_fallback_and_request_as_override(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        service = DestinationMappingPolicyService(session)
        service.update(
            {
                "container_image_project": "images-default",
                "helm_chart_project": "helm-default",
                "project_mappings": {
                    "source-a": "policy-a",
                    "source-b": "policy-b",
                },
            }
        )
        session.commit()

        effective = service.resolve_request(
            ImportDestinationPlanRequest(
                mapping_policy_revision=999,
                container_image_project="images-explicit",
                project_mappings={"source-b": "request-b", "source-c": "request-c"},
                artifact_overrides=[
                    ImportArtifactDestinationOverride(index=3, target_project="artifact-target")
                ],
            )
        )

    assert effective.mapping_policy_revision == 1
    assert effective.container_image_project == "images-explicit"
    assert effective.helm_chart_project == "helm-default"
    assert effective.project_mappings == {
        "source-a": "policy-a",
        "source-b": "request-b",
        "source-c": "request-c",
    }
    assert effective.artifact_overrides[0].target_project == "artifact-target"


def test_destination_mapping_policy_can_clear_defaults(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        service = DestinationMappingPolicyService(session)
        service.update(
            {
                "container_image_project": "images-default",
                "helm_chart_project": "helm-default",
                "project_mappings": {"source": "target"},
            }
        )
        session.commit()
        cleared = service.update(
            {
                "container_image_project": None,
                "helm_chart_project": None,
                "project_mappings": {},
            }
        )
        session.commit()

    assert cleared.snapshot.revision == 2
    assert cleared.snapshot.container_image_project is None
    assert cleared.snapshot.helm_chart_project is None
    assert cleared.snapshot.project_mappings == {}


def test_destination_mapping_policy_rejects_invalid_project_names(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        service = DestinationMappingPolicyService(session)
        with pytest.raises(DestinationMappingPolicyError) as exc:
            service.update({"container_image_project": "Team/Images"})

    assert exc.value.code == "destination_mapping_policy_project_invalid"
