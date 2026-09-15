from app.schemas.imports import ImportDestinationPlanRequest


def test_destination_mapping_payload_contains_only_routing_data() -> None:
    payload = ImportDestinationPlanRequest(
        container_image_project="docker-prod",
        helm_chart_project="helm-prod",
        project_mappings={"source": "target"},
    ).model_dump()

    serialized_keys = " ".join(payload).lower()
    assert "password" not in serialized_keys
    assert "credential" not in serialized_keys
    assert "secret" not in serialized_keys
