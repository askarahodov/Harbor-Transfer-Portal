from app.schemas.imports import ImportDestinationPlanRequest


def test_destination_plan_request_model_smoke() -> None:
    assert ImportDestinationPlanRequest(container_image_project="docker").container_image_project == "docker"
