from app.schemas.imports import ImportDestinationPlanRequest


def test_empty_destination_plan_request_is_explicitly_unmapped() -> None:
    request = ImportDestinationPlanRequest()

    assert request.container_image_project is None
    assert request.helm_chart_project is None
    assert request.project_mappings == {}
    assert request.artifact_overrides == []
