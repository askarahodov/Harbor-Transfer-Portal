from app.domain.bundle import ArtifactStatus
from app.schemas.imports import ImportReceiptArtifactResponse


def test_import_receipt_artifact_can_record_actual_target_reference() -> None:
    artifact = ImportReceiptArtifactResponse(
        index=0,
        artifact_type="container-image",
        repository="source-team/app/api",
        reference="1.4.2",
        expected_digest="sha256:" + "a" * 64,
        target_digest="sha256:" + "a" * 64,
        target_repository="docker-prod/app/api",
        final_reference="harbor.target.local/docker-prod/app/api:1.4.2",
        status=ArtifactStatus.VERIFIED,
    )

    assert artifact.repository == "source-team/app/api"
    assert artifact.target_repository == "docker-prod/app/api"
    assert artifact.final_reference == "harbor.target.local/docker-prod/app/api:1.4.2"
