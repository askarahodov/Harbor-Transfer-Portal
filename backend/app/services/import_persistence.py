from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ArtifactResult, Operation
from app.domain.bundle import ArtifactStatus, OperationType
from app.schemas.imports import (
    ImportPreviewResponse,
    ImportReceiptArtifactResponse,
    ImportReceiptResponse,
)
from app.services.operation_manager import OperationTaskFailure


class ImportOperationPersistence:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        receipt_root: Path,
    ) -> None:
        self.session_factory = session_factory
        self.receipt_root = receipt_root

    def load_execution_state(
        self,
        operation_id: int,
    ) -> tuple[Operation, ImportPreviewResponse, bool, datetime]:
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if (
                operation is None
                or operation.type is not OperationType.IMPORT
                or operation.import_preview_json is None
                or operation.import_policy_json is None
                or operation.bundle_sha256 is None
            ):
                raise OperationTaskFailure(
                    "import_execution_state_invalid",
                    "Persisted import execution state неполон",
                )
            _ = operation.artifacts
            preview = ImportPreviewResponse.model_validate_json(operation.import_preview_json)
            policy = json.loads(operation.import_policy_json)
            requested_at = datetime.fromisoformat(policy["requested_at"])
            overwrite = bool(policy.get("overwrite_conflicts", False))
            session.expunge(operation)
            for artifact in operation.artifacts:
                session.expunge(artifact)
            return operation, preview, overwrite, requested_at

    def persist_verified_signer(
        self,
        operation_id: int,
        fingerprint: str,
    ) -> None:
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise OperationTaskFailure(
                    "import_operation_not_found",
                    "Import operation не найдена при сохранении verified signer",
                )
            existing = operation.bundle_signing_key_fingerprint
            if existing is not None and existing != fingerprint:
                raise OperationTaskFailure(
                    "import_signing_key_changed",
                    "Verified signer fingerprint изменился внутри import operation",
                )
            operation.bundle_signing_key_fingerprint = fingerprint
            session.commit()

    def persist_preview(self, operation_id: int, preview: ImportPreviewResponse) -> None:
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise OperationTaskFailure(
                    "import_operation_not_found",
                    "Import operation не найдена при сохранении preview",
                )
            if operation.artifacts:
                raise OperationTaskFailure(
                    "import_preview_already_persisted",
                    "Artifact preview уже был сохранён",
                )
            operation.source_delivery_id = preview.source_delivery_id
            operation.bundle_sha256 = preview.bundle_sha256
            operation.bundle_size_bytes = preview.bundle_size_bytes
            operation.bundle_signing_key_fingerprint = preview.signing_key_fingerprint
            operation.import_preview_json = preview.model_dump_json()
            for item in preview.artifacts:
                session.add(
                    ArtifactResult(
                        operation_id=operation.id,
                        artifact_type=item.artifact_type,
                        repository=item.repository,
                        name=item.name,
                        reference=item.reference,
                        version=item.version,
                        source_digest=item.expected_digest,
                        size_bytes=item.payload_size,
                        status=ArtifactStatus.PENDING,
                    )
                )
            operation.total_artifacts = len(preview.artifacts)
            operation.progress_total = len(preview.artifacts)
            operation.progress_current = 0
            session.commit()

    def write_receipt(
        self,
        operation_id: int,
        preview: ImportPreviewResponse,
        overwrite: bool,
        requested_at: datetime,
        failures: int,
    ) -> None:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise OperationTaskFailure(
                    "import_operation_not_found",
                    "Operation отсутствует при формировании receipt",
                )
            artifacts = sorted(operation.artifacts, key=lambda item: item.id)
            receipt = ImportReceiptResponse(
                operation_id=operation.id,
                source_delivery_id=preview.source_delivery_id,
                bundle_sha256=preview.bundle_sha256,
                actor_username=operation.actor_username,
                started_at=requested_at,
                finished_at=now,
                overwrite_conflicts=overwrite,
                result="FAILED" if failures else "COMPLETED",
                artifacts=[
                    ImportReceiptArtifactResponse(
                        index=index,
                        artifact_type=item.artifact_type,
                        repository=item.repository,
                        name=item.name,
                        reference=item.reference,
                        version=item.version,
                        expected_digest=item.source_digest,
                        target_digest=item.target_digest,
                        status=item.status,
                        error_code=item.error_code,
                        error_message=item.error_message,
                    )
                    for index, item in enumerate(artifacts)
                ],
            )
            payload = receipt.model_dump_json(indent=2) + "\n"
            operation.import_receipt_json = receipt.model_dump_json()
            session.commit()

        self.receipt_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.receipt_root / f"import-{operation_id}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(path, 0o440)
            self._fsync_directory(self.receipt_root)
        except FileExistsError as exc:
            raise OperationTaskFailure(
                "import_receipt_exists",
                "Immutable import receipt уже существует",
            ) from exc

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
