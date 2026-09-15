from app.services.import_destination_plan import ImportDestinationPlanOrchestrator


def test_image_final_reference_uses_at_for_digest() -> None:
    digest = "sha256:" + "a" * 64

    reference = ImportDestinationPlanOrchestrator._image_display_reference(
        "harbor.target.local",
        "docker-prod/app/api",
        digest,
    )

    assert reference == f"harbor.target.local/docker-prod/app/api@{digest}"
