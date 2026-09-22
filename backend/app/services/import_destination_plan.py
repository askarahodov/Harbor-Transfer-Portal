from __future__ import annotations

import json
import os
import secrets
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, datetime
from re import fullmatch
from typing import Any, cast

from sqlalchemy.orm import Session

from app.db.models import Operation
from app.domain.bundle import (
    ContainerImageArtifact,
    HelmChartArtifact,
    OperationStatus,
    OperationType,
)
from app.domain.imports import ImportPreviewState
from app.schemas.imports import (
    ImportArtifactPreviewResponse,
    ImportDestinationArtifactPlanResponse,
    ImportDestinationPlanRequest,
    ImportDestinationPlanResponse,
    ImportPreviewResponse,
    ImportReceiptArtifactResponse,
    ImportReceiptResponse,
)
from app.services.bundle_package_service import (
    BundlePackageError,
    BundlePackageService,
    BundleVerificationResult,
)
from app.services.destination_plan_integrity import (
    canonical_plan_hash,
    colliding_artifact_indices,
    validated_source_repository,
)
from app.services.harbor_destination_validator import (
    DestinationCapability,
    DestinationValidator,
    HarborDestinationValidator,
)
from app.services.harbor_settings import HarborSettingsError
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmOciService,
    HelmServiceError,
    HelmTargetState,
)
from app.services.import_orchestrator import ImportOrchestrationError
from app.services.import_preview_projection import ImportPreviewProjectionOrchestrator
from app.services.operation_manager import (
    OperationContext,
    OperationManagerError,
    OperationTaskFailure,
)
from app.services.skopeo_service import (
    ImageReference,
    SkopeoService,
    SkopeoServiceError,
    TargetState,
)

_PROJECT_PATTERN = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
_POLICY_VERSION = 2
DestinationValidatorFactory = Callable[[Session], DestinationValidator]


class _DestinationProjectedPackageService:
    """Project a verified signed manifest onto an already-persisted destination plan."""

    def __init__(
        self,
        delegate: BundlePackageService,
        operation_id: ContextVar[int | None],
        projector: Callable[[int, BundleVerificationResult], BundleVerificationResult],
    ) -> None:
        self._delegate = delegate
        self._operation_id = operation_id
        self._projector = projector

    def verify_bundle(self, *args: Any, **kwargs: Any) -> BundleVerificationResult:
        verified = self._delegate.verify_bundle(*args, **kwargs)
        operation_id = self._operation_id.get()
        if operation_id is None:
            return verified
        return self._projector(operation_id, verified)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class ImportDestinationPlanOrchestrator(ImportPreviewProjectionOrchestrator):
    """Add immutable TARGET destination planning without duplicating the import worker."""

    def __init__(
        self,
        *args: Any,
        destination_validator_factory: DestinationValidatorFactory | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.destination_validator_factory = destination_validator_factory or (
            lambda session: HarborDestinationValidator(
                session,
                self.settings,
                profile_id=self._harbor_profile_context.get(),
            )
        )
        self._execution_operation_id: ContextVar[int | None] = ContextVar(
            "import_destination_operation_id",
            default=None,
        )
        base_factory = self.package_factory

        def destination_factory() -> BundlePackageService:
            service = _DestinationProjectedPackageService(
                base_factory(),
                self._execution_operation_id,
                self._project_verified_bundle,
            )
            return cast(BundlePackageService, service)

        self.package_factory = destination_factory

    async def build_destination_plan(
        self,
        operation_id: int,
        mapping: ImportDestinationPlanRequest,
        *,
        actor_username: str | None = None,
    ) -> ImportDestinationPlanResponse:
        return await self._build_destination_plan(
            operation_id,
            mapping,
            actor_username=actor_username,
        )

    def destination_plan(self, operation_id: int) -> ImportDestinationPlanResponse:
        plan = self._persisted_destination_plan(operation_id)
        if plan is None:
            raise ImportOrchestrationError(
                "import_destination_plan_not_ready",
                "Destination plan ещё не сохранён",
            )
        return plan

    async def _build_destination_plan(
        self,
        operation_id: int,
        mapping: ImportDestinationPlanRequest,
        *,
        actor_username: str | None,
    ) -> ImportDestinationPlanResponse:
        self._require_target()
        operation = self._get_import_operation(operation_id)
        if operation.status is not OperationStatus.READY or operation.import_preview_json is None:
            raise ImportOrchestrationError(
                "import_not_ready",
                "Destination plan можно строить только после verified preview",
            )
        preview = ImportPreviewResponse.model_validate_json(operation.import_preview_json)
        planner_username = actor_username or operation.actor_username
        if not planner_username:
            raise ImportOrchestrationError(
                "import_destination_plan_actor_missing",
                "Destination plan нельзя сохранить без actor identity",
            )
        overrides = {item.index: item.target_project for item in mapping.artifact_overrides}
        known_indices = {item.index for item in preview.artifacts}
        unknown_overrides = sorted(set(overrides) - known_indices)
        if unknown_overrides:
            raise ImportOrchestrationError(
                "import_destination_override_invalid",
                f"Per-artifact override ссылается на неизвестный index {unknown_overrides[0]}",
            )

        try:
            binding = self._verified_operation_binding(operation_id)
        except OperationTaskFailure as exc:
            raise ImportOrchestrationError(exc.code, exc.message) from exc
        token = self._harbor_profile_context.set(binding.id)
        try:
            with self.session_factory() as session:
                try:
                    validator = self.destination_validator_factory(session)
                except (HarborSettingsError, ValueError) as exc:
                    raise ImportOrchestrationError(
                        getattr(exc, "code", "import_destination_validation_failed"),
                        str(exc),
                    ) from exc
                skopeo = self.skopeo_factory(session)
                helm = self.helm_factory(session)
                planned = [
                    await self._plan_artifact(
                        item,
                        mapping,
                        overrides,
                        validator,
                        skopeo,
                        helm,
                    )
                    for item in preview.artifacts
                ]
        finally:
            self._harbor_profile_context.reset(token)

        collisions = colliding_artifact_indices(planned)
        if collisions:
            planned = [
                item.model_copy(
                    update={
                        "classification": ImportPreviewState.ERROR,
                        "error_code": "import_destination_collision",
                        "message": "Несколько source artifacts дают один final TARGET reference",
                    }
                )
                if item.index in collisions
                else item
                for item in planned
            ]

        created_at = datetime.now(UTC)
        plan_hash = canonical_plan_hash(
            operation_id=operation_id,
            source_delivery_id=preview.source_delivery_id,
            actor_username=planner_username,
            bundle_sha256=preview.bundle_sha256,
            created_at=created_at,
            mapping=mapping,
            artifacts=planned,
        )
        plan = ImportDestinationPlanResponse(
            operation_id=operation_id,
            source_delivery_id=preview.source_delivery_id,
            actor_username=planner_username,
            bundle_sha256=preview.bundle_sha256,
            plan_id=secrets.token_hex(32),
            plan_hash=plan_hash,
            created_at=created_at,
            valid=all(
                item.project_exists
                and item.write_allowed
                and item.classification
                in {ImportPreviewState.NEW, ImportPreviewState.SAME, ImportPreviewState.CONFLICT}
                for item in planned
            ),
            artifacts=planned,
        )
        self._persist_destination_plan(operation_id, mapping, plan)
        return plan

    async def _plan_artifact(
        self,
        item: ImportArtifactPreviewResponse,
        mapping: ImportDestinationPlanRequest,
        overrides: dict[int, str],
        validator: DestinationValidator,
        skopeo: SkopeoService,
        helm: HelmOciService,
    ) -> ImportDestinationArtifactPlanResponse:
        try:
            source_project, suffix = validated_source_repository(item.repository)
        except ValueError as exc:
            source_project = item.repository.partition("/")[0]
            return ImportDestinationArtifactPlanResponse(
                index=item.index,
                artifact_type=item.artifact_type,
                source_repository=item.repository,
                source_project=source_project,
                name=item.name,
                reference=item.reference,
                version=item.version,
                expected_digest=item.expected_digest,
                payload_size=item.payload_size,
                classification=ImportPreviewState.ERROR,
                error_code="import_destination_repository_invalid",
                message=str(exc),
            )

        target_project = overrides.get(item.index) or mapping.project_mappings.get(source_project)
        if target_project is None:
            if item.artifact_type == "container-image":
                target_project = mapping.container_image_project
            elif item.artifact_type == "helm-chart":
                target_project = mapping.helm_chart_project

        base: dict[str, Any] = {
            "index": item.index,
            "artifact_type": item.artifact_type,
            "source_repository": item.repository,
            "source_project": source_project,
            "name": item.name,
            "reference": item.reference,
            "version": item.version,
            "expected_digest": item.expected_digest,
            "payload_size": item.payload_size,
        }
        if target_project is None:
            return ImportDestinationArtifactPlanResponse(
                **base,
                classification=ImportPreviewState.ERROR,
                error_code="import_destination_unmapped",
                message="Для артефакта не задан TARGET Harbor project",
            )
        if fullmatch(_PROJECT_PATTERN, target_project) is None:
            return ImportDestinationArtifactPlanResponse(
                **base,
                target_project=target_project,
                classification=ImportPreviewState.ERROR,
                error_code="import_destination_project_invalid",
                message="TARGET Harbor project имеет недопустимое имя",
            )

        target_repository = target_project + (f"/{suffix}" if suffix else "")
        if item.artifact_type == "container-image":
            return await self._plan_image(
                base,
                target_project,
                target_repository,
                item.reference,
                item.expected_digest,
                validator,
                skopeo,
            )
        if item.artifact_type == "helm-chart":
            return await self._plan_chart(
                base,
                target_project,
                target_repository,
                item.name,
                item.version,
                item.expected_digest,
                validator,
                helm,
            )
        return ImportDestinationArtifactPlanResponse(
            **base,
            target_project=target_project,
            target_repository=target_repository,
            classification=ImportPreviewState.ERROR,
            error_code="import_destination_reference_invalid",
            message="Unsupported bundle artifact type",
        )

    async def _plan_image(
        self,
        base: dict[str, Any],
        target_project: str,
        target_repository: str,
        reference: str | None,
        expected_digest: str | None,
        validator: DestinationValidator,
        skopeo: SkopeoService,
    ) -> ImportDestinationArtifactPlanResponse:
        try:
            if reference is None:
                raise ValueError("Container image reference отсутствует")
            target = ImageReference(target_repository, reference)
        except ValueError as exc:
            return self._invalid_reference(base, target_project, target_repository, str(exc))
        final_reference = self._image_display_reference(
            validator.registry_host,
            target.repository,
            target.reference,
        )
        capability = await validator.validate(target_project, target.repository)
        denied = self._capability_error(
            base,
            target_project,
            target_repository,
            final_reference,
            capability,
        )
        if denied is not None:
            return denied
        try:
            inspected = await skopeo.inspect_target(target, expected_digest=expected_digest)
            classification = {
                TargetState.ABSENT: ImportPreviewState.NEW,
                TargetState.SAME_DIGEST: ImportPreviewState.SAME,
                TargetState.CONFLICTING_DIGEST: ImportPreviewState.CONFLICT,
                TargetState.PRESENT: ImportPreviewState.UNKNOWN,
            }[inspected.state]
            return ImportDestinationArtifactPlanResponse(
                **base,
                target_project=target_project,
                target_repository=target_repository,
                final_reference=final_reference,
                project_exists=True,
                write_allowed=True,
                target_digest=inspected.digest,
                classification=classification,
            )
        except (SkopeoServiceError, ValueError) as exc:
            return self._destination_inspection_error(
                base,
                target_project,
                target_repository,
                final_reference,
                exc,
            )

    async def _plan_chart(
        self,
        base: dict[str, Any],
        target_project: str,
        target_repository: str,
        name: str | None,
        version: str | None,
        expected_digest: str | None,
        validator: DestinationValidator,
        helm: HelmOciService,
    ) -> ImportDestinationArtifactPlanResponse:
        try:
            if name is None or version is None:
                raise ValueError("Helm chart name/version отсутствуют")
            target = HelmChartReference(target_repository, name, version)
        except ValueError as exc:
            return self._invalid_reference(base, target_project, target_repository, str(exc))
        final_reference = (
            f"oci://{validator.registry_host}/{target.harbor_repository}:{target.version}"
        )
        capability = await validator.validate(target_project, target.harbor_repository)
        denied = self._capability_error(
            base,
            target_project,
            target_repository,
            final_reference,
            capability,
        )
        if denied is not None:
            return denied
        try:
            inspected = await helm.inspect_target(target, expected_digest=expected_digest)
            classification = {
                HelmTargetState.ABSENT: ImportPreviewState.NEW,
                HelmTargetState.SAME_DIGEST: ImportPreviewState.SAME,
                HelmTargetState.CONFLICTING_DIGEST: ImportPreviewState.CONFLICT,
                HelmTargetState.PRESENT: ImportPreviewState.UNKNOWN,
            }[inspected.state]
            return ImportDestinationArtifactPlanResponse(
                **base,
                target_project=target_project,
                target_repository=target_repository,
                final_reference=final_reference,
                project_exists=True,
                write_allowed=True,
                target_digest=inspected.digest,
                classification=classification,
            )
        except (HelmServiceError, ValueError) as exc:
            return self._destination_inspection_error(
                base,
                target_project,
                target_repository,
                final_reference,
                exc,
            )

    @staticmethod
    def _invalid_reference(
        base: dict[str, Any],
        target_project: str,
        target_repository: str,
        message: str,
    ) -> ImportDestinationArtifactPlanResponse:
        return ImportDestinationArtifactPlanResponse(
            **base,
            target_project=target_project,
            target_repository=target_repository,
            classification=ImportPreviewState.ERROR,
            error_code="import_destination_reference_invalid",
            message=message,
        )

    @staticmethod
    def _capability_error(
        base: dict[str, Any],
        target_project: str,
        target_repository: str,
        final_reference: str,
        capability: DestinationCapability,
    ) -> ImportDestinationArtifactPlanResponse | None:
        if capability.project_exists and capability.write_allowed:
            return None
        return ImportDestinationArtifactPlanResponse(
            **base,
            target_project=target_project,
            target_repository=target_repository,
            final_reference=final_reference,
            project_exists=capability.project_exists,
            write_allowed=capability.write_allowed,
            classification=ImportPreviewState.ERROR,
            error_code=capability.error_code or "import_destination_validation_failed",
            message=capability.message or "TARGET destination validation failed",
        )

    @staticmethod
    def _destination_inspection_error(
        base: dict[str, Any],
        target_project: str,
        target_repository: str,
        final_reference: str,
        exc: Exception,
    ) -> ImportDestinationArtifactPlanResponse:
        return ImportDestinationArtifactPlanResponse(
            **base,
            target_project=target_project,
            target_repository=target_repository,
            final_reference=final_reference,
            project_exists=True,
            write_allowed=True,
            classification=ImportPreviewState.ERROR,
            error_code=getattr(exc, "code", "import_target_inspection_failed"),
            message=str(exc),
        )

    async def start_import(
        self,
        operation_id: int,
        *,
        actor_username: str,
        overwrite_conflicts: bool,
        destination_plan_id: str | None = None,
    ) -> None:
        self._require_target()
        plan = self._persisted_destination_plan(operation_id)
        if plan is None:
            raise ImportOrchestrationError(
                "import_destination_plan_not_ready",
                "Import требует заранее сохранённый destination plan",
            )
        if destination_plan_id is None:
            raise ImportOrchestrationError(
                "import_destination_plan_required",
                "Укажите plan_id подтверждённого destination plan",
            )
        if destination_plan_id != plan.plan_id:
            raise ImportOrchestrationError(
                "import_destination_plan_stale",
                "Destination plan изменился; обновите Preview перед Import",
            )
        if plan.actor_username != actor_username:
            raise ImportOrchestrationError(
                "import_destination_plan_actor_mismatch",
                "Destination plan подтверждён другим actor",
            )
        if not plan.valid:
            raise ImportOrchestrationError(
                "import_destination_plan_invalid",
                "Destination plan содержит неразрешённые TARGET destinations",
            )
        conflicts = [
            item for item in plan.artifacts if item.classification is ImportPreviewState.CONFLICT
        ]
        if conflicts and not overwrite_conflicts:
            raise ImportOrchestrationError(
                "import_conflict_blocked",
                "Destination plan содержит CONFLICT; overwrite по умолчанию запрещён",
            )
        if overwrite_conflicts and not self.settings.import_allow_overwrite:
            raise ImportOrchestrationError(
                "import_overwrite_disabled",
                "Overwrite запрещён server-side policy",
            )

        requested_at = datetime.now(UTC)
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None or operation.type is not OperationType.IMPORT:
                raise ImportOrchestrationError(
                    "import_operation_not_found",
                    "Import-операция не найдена",
                )
            if operation.status is not OperationStatus.READY:
                raise ImportOrchestrationError(
                    "import_not_ready",
                    "Import можно запускать только после verified preview",
                )
            if operation.import_preview_json is None:
                raise ImportOrchestrationError(
                    "import_preview_not_ready",
                    "Verified preview отсутствует",
                )
            preview = ImportPreviewResponse.model_validate_json(
                operation.import_preview_json
            )
            self._require_preview_signer_trusted(preview)
            policy = self._policy_object(operation)
            persisted = self._plan_from_policy(policy)
            if persisted is None or persisted.plan_id != plan.plan_id:
                raise ImportOrchestrationError(
                    "import_destination_plan_stale",
                    "Persisted destination plan изменился перед запуском Import",
                )
            if (
                persisted.operation_id != operation.id
                or persisted.bundle_sha256 != operation.bundle_sha256
                or persisted.source_delivery_id != operation.source_delivery_id
                or persisted.actor_username != actor_username
            ):
                raise ImportOrchestrationError(
                    "import_destination_plan_stale",
                    "Destination plan больше не связан с этой import operation",
                )
            policy.update(
                {
                    "bundle_sha256": operation.bundle_sha256,
                    "destination_plan_hash": persisted.plan_hash,
                    "overwrite_conflicts": overwrite_conflicts,
                    "requested_by": actor_username,
                    "requested_at": requested_at.isoformat(),
                }
            )
            operation.import_policy_json = self._dump_policy(policy)
            session.commit()
        try:
            self.operation_manager.submit(
                operation_id,
                lambda context: self._import_worker(context, operation_id),
            )
        except OperationManagerError as exc:
            raise ImportOrchestrationError(exc.code, exc.message) from exc

    async def _import_worker(self, context: OperationContext, operation_id: int) -> None:
        token = self._execution_operation_id.set(operation_id)
        try:
            # The base worker re-inspects each mapped final TARGET reference immediately
            # before mutation. Preview classification is advisory and never trusted here.
            await super()._import_worker(context, operation_id)
        finally:
            self._execution_operation_id.reset(token)

    def _project_verified_bundle(
        self,
        operation_id: int,
        verified: BundleVerificationResult,
    ) -> BundleVerificationResult:
        plan = self.destination_plan(operation_id)
        if plan.operation_id != operation_id or plan.bundle_sha256 != verified.archive_sha256:
            raise BundlePackageError(
                "import_destination_plan_stale",
                "Persisted destination plan не соответствует verified bundle",
            )
        if len(plan.artifacts) != len(verified.manifest.artifacts):
            raise BundlePackageError(
                "import_destination_plan_tampered",
                "Состав persisted destination plan не совпадает с signed manifest",
            )

        mapped: list[ContainerImageArtifact | HelmChartArtifact] = []
        for index, (descriptor, planned) in enumerate(
            zip(verified.manifest.artifacts, plan.artifacts, strict=True)
        ):
            self._assert_plan_matches_descriptor(index, descriptor, planned)
            if planned.target_repository is None:
                raise BundlePackageError(
                    "import_destination_plan_tampered",
                    "Persisted destination repository отсутствует",
                )
            if isinstance(descriptor, ContainerImageArtifact):
                if planned.reference is None:
                    raise BundlePackageError(
                        "import_destination_plan_tampered",
                        "Persisted image reference отсутствует",
                    )
                mapped.append(
                    descriptor.model_copy(
                        update={
                            "repository": planned.target_repository,
                            "reference": planned.reference,
                        }
                    )
                )
            else:
                if planned.name is None or planned.version is None:
                    raise BundlePackageError(
                        "import_destination_plan_tampered",
                        "Persisted Helm identity отсутствует",
                    )
                mapped.append(
                    descriptor.model_copy(
                        update={
                            "repository": planned.target_repository,
                            "name": planned.name,
                            "version": planned.version,
                        }
                    )
                )
        manifest = verified.manifest.model_copy(update={"artifacts": mapped})
        return replace(verified, manifest=manifest)

    @staticmethod
    def _assert_plan_matches_descriptor(
        index: int,
        descriptor: ContainerImageArtifact | HelmChartArtifact,
        planned: ImportDestinationArtifactPlanResponse,
    ) -> None:
        if (
            planned.index != index
            or planned.artifact_type != descriptor.type
            or planned.source_repository != descriptor.repository
            or planned.expected_digest != descriptor.source_digest
        ):
            raise BundlePackageError(
                "import_destination_plan_tampered",
                "Persisted destination plan не совпадает с signed manifest",
            )
        if isinstance(descriptor, ContainerImageArtifact):
            matches = planned.reference == descriptor.reference and planned.name is None
        else:
            matches = (
                planned.name == descriptor.name
                and planned.version == descriptor.version
                and planned.reference is None
            )
        if not matches:
            raise BundlePackageError(
                "import_destination_plan_tampered",
                "Persisted destination artifact identity не совпадает с signed manifest",
            )

    def _persist_destination_plan(
        self,
        operation_id: int,
        mapping: ImportDestinationPlanRequest,
        plan: ImportDestinationPlanResponse,
    ) -> None:
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None or operation.type is not OperationType.IMPORT:
                raise ImportOrchestrationError(
                    "import_operation_not_found",
                    "Import-операция не найдена",
                )
            if operation.status is not OperationStatus.READY:
                raise ImportOrchestrationError(
                    "import_not_ready",
                    "Import operation изменилась во время destination preview",
                )
            if (
                operation.bundle_sha256 != plan.bundle_sha256
                or operation.source_delivery_id != plan.source_delivery_id
                or operation.id != plan.operation_id
            ):
                raise ImportOrchestrationError(
                    "import_destination_plan_stale",
                    "Bundle или delivery изменились во время построения destination plan",
                )
            operation.import_policy_json = self._dump_policy(
                {
                    "version": _POLICY_VERSION,
                    "mapping_request": mapping.model_dump(mode="json"),
                    "destination_plan": plan.model_dump(mode="json"),
                }
            )
            session.commit()

    def _persisted_destination_plan(
        self,
        operation_id: int,
    ) -> ImportDestinationPlanResponse | None:
        operation = self._get_import_operation(operation_id)
        if operation.import_policy_json is None:
            return None
        plan = self._plan_from_policy(self._policy_object(operation))
        if plan is None:
            return None
        if (
            plan.operation_id != operation.id
            or plan.bundle_sha256 != operation.bundle_sha256
            or plan.source_delivery_id != operation.source_delivery_id
        ):
            raise ImportOrchestrationError(
                "import_destination_plan_stale",
                "Persisted destination plan не связан с текущей import operation",
            )
        return plan

    def _plan_from_policy(self, policy: dict[str, Any]) -> ImportDestinationPlanResponse | None:
        raw = policy.get("destination_plan")
        if raw is None:
            return None
        if policy.get("version") != _POLICY_VERSION:
            raise ImportOrchestrationError(
                "import_destination_plan_invalid",
                "Persisted destination plan использует устаревшую integrity policy",
            )
        try:
            mapping = ImportDestinationPlanRequest.model_validate(policy.get("mapping_request", {}))
            plan = ImportDestinationPlanResponse.model_validate(raw)
        except ValueError as exc:
            raise ImportOrchestrationError(
                "import_destination_plan_invalid",
                "Persisted destination plan повреждён",
            ) from exc
        expected_hash = canonical_plan_hash(
            operation_id=plan.operation_id,
            source_delivery_id=plan.source_delivery_id,
            actor_username=plan.actor_username,
            bundle_sha256=plan.bundle_sha256,
            created_at=plan.created_at,
            mapping=mapping,
            artifacts=plan.artifacts,
        )
        if plan.plan_hash != expected_hash:
            raise ImportOrchestrationError(
                "import_destination_plan_invalid",
                "Persisted destination plan hash не соответствует его содержимому",
            )
        if colliding_artifact_indices(plan.artifacts):
            raise ImportOrchestrationError(
                "import_destination_plan_invalid",
                "Persisted destination plan содержит duplicate final TARGET references",
            )
        return plan

    @staticmethod
    def _policy_object(operation: Operation) -> dict[str, Any]:
        if operation.import_policy_json is None:
            return {}
        try:
            payload = json.loads(operation.import_policy_json)
        except json.JSONDecodeError as exc:
            raise ImportOrchestrationError(
                "import_destination_plan_invalid",
                "Persisted import policy повреждена",
            ) from exc
        if not isinstance(payload, dict):
            raise ImportOrchestrationError(
                "import_destination_plan_invalid",
                "Persisted import policy имеет неверный формат",
            )
        return payload

    @staticmethod
    def _dump_policy(policy: dict[str, Any]) -> str:
        return json.dumps(policy, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _image_display_reference(host: str, repository: str, reference: str) -> str:
        separator = "@" if reference.startswith("sha256:") else ":"
        return f"{host}/{repository}{separator}{reference}"

    def _write_receipt(
        self,
        operation_id: int,
        preview: ImportPreviewResponse,
        overwrite: bool,
        requested_at: datetime,
        failures: int,
    ) -> None:
        plan = self.destination_plan(operation_id)
        now = datetime.now(UTC)
        with self.session_factory() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise OperationTaskFailure(
                    "import_operation_not_found",
                    "Operation отсутствует при формировании receipt",
                )
            rows = sorted(operation.artifacts, key=lambda item: item.id)
            if len(rows) != len(plan.artifacts):
                raise OperationTaskFailure(
                    "import_destination_plan_tampered",
                    "Destination plan не совпадает с persisted artifact rows",
                )
            receipt = ImportReceiptResponse(
                operation_id=operation.id,
                source_delivery_id=preview.source_delivery_id,
                bundle_sha256=preview.bundle_sha256,
                actor_username=operation.actor_username,
                started_at=requested_at,
                finished_at=now,
                overwrite_conflicts=overwrite,
                destination_plan_id=plan.plan_id,
                destination_plan_hash=plan.plan_hash,
                result="FAILED" if failures else "COMPLETED",
                artifacts=[
                    ImportReceiptArtifactResponse(
                        index=index,
                        artifact_type=row.artifact_type,
                        repository=row.repository,
                        name=row.name,
                        reference=row.reference,
                        version=row.version,
                        expected_digest=row.source_digest,
                        target_digest=row.target_digest,
                        target_repository=planned.target_repository,
                        final_reference=planned.final_reference,
                        status=row.status,
                        error_code=row.error_code,
                        error_message=row.error_message,
                    )
                    for index, (row, planned) in enumerate(
                        zip(rows, plan.artifacts, strict=True)
                    )
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
