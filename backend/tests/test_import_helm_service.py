import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.db.base import Base
from app.db.models import ArtifactResult, Operation
from app.db.session import create_db_engine, create_session_factory
from app.domain.bundle import ArtifactStatus, OperationStatus, OperationType
from app.schemas.imports import ImportReceiptArtifactResponse, ImportReceiptResponse
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmServiceError,
    HelmTargetState,
)
from app.services.import_helm_service import ImportHelmOciService

SOURCE_DIGEST = "sha256:" + "a" * 64
TARGET_DIGEST = "sha256:" + "b" * 64
REPLACED_DIGEST = "sha256:" + "c" * 64


class DummySession:
    def get(self, _model: object, _key: object) -> None:
        return None


def _service(tmp_path: Path) -> ImportHelmOciService:
    settings = Settings(
        _env_file=None,
        helm_workspace_root=tmp_path / "packages",
        bundle_extract_root=tmp_path / "incoming" / "verified",
    )
    return ImportHelmOciService(  # type: ignore[arg-type]
        DummySession(),
        settings,
        digest_resolver=lambda _chart: None,
    )


def _package(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic-chart")
    return path


def _history_service(
    tmp_path: Path,
    observed_digest: str,
    *,
    source_repository: str = "project/charts",
    receipt_target_repository: str | None = None,
) -> ImportHelmOciService:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'history.db'}",
        helm_workspace_root=tmp_path / "packages",
        bundle_extract_root=tmp_path / "incoming" / "verified",
    )
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    operation = Operation(
        type=OperationType.IMPORT,
        status=OperationStatus.COMPLETED,
        actor_username="target-operator",
    )
    session.add(operation)
    session.flush()
    session.add(
        ArtifactResult(
            operation_id=operation.id,
            artifact_type="helm-chart",
            repository=source_repository,
            name="sample",
            version="1.2.3",
            source_digest=SOURCE_DIGEST,
            target_digest=TARGET_DIGEST,
            status=ArtifactStatus.VERIFIED,
        )
    )
    if receipt_target_repository is not None:
        now = datetime.now(UTC)
        operation.import_receipt_json = ImportReceiptResponse(
            operation_id=operation.id,
            source_delivery_id="SOURCE-DELIVERY-1",
            bundle_sha256="d" * 64,
            actor_username="target-operator",
            started_at=now,
            finished_at=now,
            overwrite_conflicts=False,
            destination_plan_id="e" * 64,
            destination_plan_hash="f" * 64,
            result="COMPLETED",
            artifacts=[
                ImportReceiptArtifactResponse(
                    index=0,
                    artifact_type="helm-chart",
                    repository=source_repository,
                    name="sample",
                    version="1.2.3",
                    expected_digest=SOURCE_DIGEST,
                    target_digest=TARGET_DIGEST,
                    target_repository=receipt_target_repository,
                    final_reference=(
                        f"oci://harbor.test/{receipt_target_repository}/sample:1.2.3"
                    ),
                    status=ArtifactStatus.VERIFIED,
                )
            ],
        ).model_dump_json()
    session.commit()
    return ImportHelmOciService(
        session,
        settings,
        digest_resolver=lambda _chart: observed_digest,
    )


def test_import_adapter_accepts_verified_bundle_package(tmp_path: Path) -> None:
    service = _service(tmp_path)
    package = _package(
        tmp_path / "incoming" / "verified" / "import-7" / "charts" / "sample-1.2.3.tgz"
    )

    assert service._validate_package_path(package) == package.resolve()


def test_import_adapter_keeps_generic_workspace_support(tmp_path: Path) -> None:
    service = _service(tmp_path)
    package = _package(tmp_path / "packages" / "sample-1.2.3.tgz")

    assert service._validate_package_path(package) == package.resolve()


def test_import_adapter_rejects_package_outside_allowed_roots(tmp_path: Path) -> None:
    service = _service(tmp_path)
    package = _package(tmp_path / "untrusted" / "sample-1.2.3.tgz")

    with pytest.raises(HelmServiceError) as exc:
        service._validate_package_path(package)

    assert exc.value.code == "helm_workspace_path_outside_root"


def test_import_adapter_rejects_symlink_inside_verified_root(tmp_path: Path) -> None:
    service = _service(tmp_path)
    target = _package(tmp_path / "outside" / "sample-1.2.3.tgz")
    link = tmp_path / "incoming" / "verified" / "import-7" / "charts" / "sample-1.2.3.tgz"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)

    with pytest.raises(HelmServiceError) as exc:
        service._validate_package_path(link)

    assert exc.value.code == "helm_package_invalid"


def test_verified_cross_registry_digest_pair_is_same_on_replay(tmp_path: Path) -> None:
    service = _history_service(tmp_path, TARGET_DIGEST)

    result = asyncio.run(
        service.inspect_target(
            HelmChartReference("project/charts", "sample", "1.2.3"),
            expected_digest=SOURCE_DIGEST,
        )
    )

    assert result.state is HelmTargetState.SAME_DIGEST
    assert result.digest == TARGET_DIGEST


def test_mapped_verified_digest_pair_uses_receipt_target_on_replay(tmp_path: Path) -> None:
    service = _history_service(
        tmp_path,
        TARGET_DIGEST,
        source_repository="source/charts",
        receipt_target_repository="mapped/charts",
    )

    result = asyncio.run(
        service.inspect_target(
            HelmChartReference("mapped/charts", "sample", "1.2.3"),
            expected_digest=SOURCE_DIGEST,
        )
    )

    assert result.state is HelmTargetState.SAME_DIGEST
    assert result.digest == TARGET_DIGEST


def test_verified_pair_from_other_mapped_target_remains_conflict(tmp_path: Path) -> None:
    service = _history_service(
        tmp_path,
        TARGET_DIGEST,
        source_repository="source/charts",
        receipt_target_repository="mapped/charts",
    )

    result = asyncio.run(
        service.inspect_target(
            HelmChartReference("other/charts", "sample", "1.2.3"),
            expected_digest=SOURCE_DIGEST,
        )
    )

    assert result.state is HelmTargetState.CONFLICTING_DIGEST
    assert result.digest == TARGET_DIGEST


def test_external_target_digest_replacement_remains_conflict(tmp_path: Path) -> None:
    service = _history_service(tmp_path, REPLACED_DIGEST)

    result = asyncio.run(
        service.inspect_target(
            HelmChartReference("project/charts", "sample", "1.2.3"),
            expected_digest=SOURCE_DIGEST,
        )
    )

    assert result.state is HelmTargetState.CONFLICTING_DIGEST
    assert result.digest == REPLACED_DIGEST
