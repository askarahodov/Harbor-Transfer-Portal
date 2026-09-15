from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from re import fullmatch
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Operation
from app.domain.bundle import (
    ArtifactStatus,
    ContainerImageArtifact,
    HelmChartArtifact,
    OperationStatus,
    OperationType,
)
from app.domain.imports import ImportPreviewState
from app.schemas.imports import (
    ImportDestinationArtifactPlanResponse,
    ImportDestinationPlanRequest,
    ImportDestinationPlanResponse,
    ImportPreviewResponse,
    ImportReceiptArtifactResponse,
    ImportReceiptResponse,
)
from app.services.harbor_client import HarborClientError
from app.services.harbor_settings import HarborSettingsError, HarborSettingsService
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
_POLICY_VERSION = 1


@dataclass(frozen=True, slots=True)
class DestinationCapability:
    project_exists: bool
    write_allowed: bool
    error_code: str | None = None
    message: str | None = None


class DestinationValidator(Protocol):
    @property
    def registry_host(self) -> str: ...

    async def validate(self, project: str, repository: str) -> DestinationCapability: ...


class HarborDestinationValidator:
    """Non-mutating TARGET project/write capability validator.

    Project existence is checked through Harbor v2 API. Push capability is checked
    through Harbor's registry token service with a repository pull,push scope. The
    returned JWT is only decoded as an authorization result received over the
    configured Harbor transport; it is not used as an authentication credential.
    """

    def __init__(self, session: Session, settings: Settings) -> None:
        self.settings = settings
        self.harbor_settings = HarborSettingsService(session, settings)
        resolved = self.harbor_settings.resolve()
        if not resolved.url:
            raise HarborSettingsError("harbor_not_configured", "Локальный Harbor не настроен")
        self._resolved = resolved
        self._registry_host = urlsplit(resolved.url).netloc
        if not self._registry_host:
            raise HarborSettingsError(
                "harbor_configuration_invalid",
                "URL локального Harbor не содержит registry host",
            )
        self._project_cache: dict[str, bool] = {}
        self._write_cache: dict[str, bool] = {}

    @property
    def registry_host(self) -> str:
        return self._registry_host

    async def validate(self, project: str, repository: str) -> DestinationCapability:
        try:
            exists = await asyncio.to_thread(self._project_exists, project)
            if not exists:
                return DestinationCapability(
                    False,
                    False,
                    "import_destination_project_missing",
                    f"TARGET Harbor project '{project}' не существует",
                )
            write_allowed = await asyncio.to_thread(self._repository_push_allowed, repository)
            if not write_allowed:
                return DestinationCapability(
                    True,
                    False,
                    "import_destination_write_forbidden",
                    f"Настроенные Harbor credentials не имеют push-доступа к '{repository}'",
                )
            return DestinationCapability(True, True)
        except (HarborClientError, HarborSettingsError, httpx.HTTPError, ValueError) as exc:
            return DestinationCapability(
                False,
                False,
                getattr(exc, "code", "import_destination_validation_failed"),
                str(exc) or "Не удалось проверить TARGET destination",
            )

    def _project_exists(self, project: str) -> bool:
        cached = self._project_cache.get(project)
        if cached is not None:
            return cached
        with self.harbor_settings.build_client() as client:
            page = client.list_projects_page(1, 100, search_needle=project)
        exists = any(item.name == project for item in page.items)
        self._project_cache[project] = exists
        return exists

    def _repository_push_allowed(self, repository: str) -> bool:
        cached = self._write_cache.get(repository)
        if cached is not None:
            return cached
        verify: bool | str = self._resolved.verify_tls
        if self._resolved.verify_tls and self._resolved.ca_file is not None:
            verify = str(self._resolved.ca_file)
        auth = (
            httpx.BasicAuth(self._resolved.username, self._resolved.password or "")
            if self._resolved.username
            else None
        )
        with httpx.Client(
            base_url=self._resolved.url,
            auth=auth,
            verify=verify,
            timeout=httpx.Timeout(
                self.settings.harbor_read_timeout_seconds,
                connect=self.settings.harbor_connect_timeout_seconds,
            ),
            headers={"Accept": "application/json"},
        ) as client:
            response = client.get(
                "/service/token",
                params={
                    "service": "harbor-registry",
                    "scope": f"repository:{repository}:pull,push",
                },
            )
        if response.status_code in {401, 403}:
            self._write_cache[repository] = False
            return False
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Harbor token endpoint returned an invalid object")
        raw_token = payload.get("token") or payload.get("access_token")
        if not isinstance(raw_token, str) or not raw_token:
            raise ValueError("Harbor token endpoint did not return a registry token")
        allowed = self._jwt_allows_push(raw_token, repository)
        self._write_cache[repository] = allowed
        return allowed

    @staticmethod
    def _jwt_allows_push(token: str, repository: str) -> bool:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Harbor registry token is not a JWT")
        encoded = parts[1]
        encoded += "=" * (-len(encoded) % 4)
        try:
            payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Harbor registry token payload is invalid") from exc
        access = payload.get("access") if isinstance(payload, dict) else None
        if not isinstance(access, list):
            return False
        for item in access:
            if not isinstance(item, dict):
                continue
            actions = item.get("actions")
            if (
                item.get("type") == "repository"
                and item.get("name") == repository
                and isinstance(actions, list)
                and "push" in actions
            ):
                return True
        return False


DestinationValidatorFactory = Callable[[Session], DestinationValidator]


@dataclass(frozen=True, slots=True)
class _ResolvedTarget:
    project: str
    repository: str


class ImportDestinationPlanOrchestrator(ImportPreviewProjectionOrchestrator):
    """Adds immutable TARGET destination planning to the verified import workflow."""

    def __init__(
        self,
        *args: Any,
        destination_validator_factory: DestinationValidatorFactory | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.destination_validator_factory = destination_validator_factory or (
            lambda session: HarborDestinationValidator(session, self.settings)
        )

    async def build_destination_plan(
        self,
        operation_id: int,
        mapping: ImportDestinationPlanRequest,
    ) -> ImportDestinationPlanResponse:
        return await self._build_destination_plan(
            operation_id,
            mapping,
            identity_fallback=False,
        )

    async def _build_destination_plan(
        self,
        operation_id: int,
        mapping: ImportDestinationPlanRequest,
        *,
        identity_fallback: bool,
    ) -> ImportDestinationPlanResponse:
        self._require_target()
        operation = self._get_import_operation(operation_id)
        if operation.status is not OperationStatus.READY or operation.import_preview_json is None:
            raise ImportOrchestrationError(
                "import_not_ready",
                "Destination plan можно строить только после verified preview",
            )
        preview = ImportPreviewResponse.model_validate_json(operation.import_preview_json)
        overrides = {item.index: item.target_project for item in mapping.artifact_overrides}
        known_indices = {item.index for item in preview.artifacts}
        unknown_overrides = sorted(set(overrides) - known_indices)
        if unknown_overrides:
            raise ImportOrchestrationError(
                "import_destination_override_invalid",
                f"Per-artifact override ссылается на неизвестный index {unknown_overrides[0]}",
            )

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
            planned: list[ImportDestinationArtifactPlanResponse] = []
            for item in preview.artifacts:
                planned.append(
                    await self._plan_artifact(
                        item,
                        mapping,
                        overrides,
                        validator,
                        skopeo,
                        helm,
                        identity_fallback=identity_fallback,
                    )
                )

        plan_id = self._plan_id(preview.bundle_sha256, planned)
        plan = ImportDestinationPlanResponse(
            operation_id=operation_id,
            bundle_sha256=preview.bundle_sha256,
            plan_id=plan_id,
            created_at=datetime.now(UTC),
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
        item: Any,
        mapping: ImportDestinationPlanRequest,
        overrides: dict[int, str],
        validator: DestinationValidator,
        skopeo: SkopeoService,
        helm: HelmOciService,
        *,
        identity_fallback: bool,
    ) -> ImportDestinationArtifactPlanResponse:
        source_project, _, suffix = item.repository.partition("/")
        target_project = overrides.get(item.index) or mapping.project_mappings.get(source_project)
        if target_project is None:
            if item.artifact_type == "container-image":
                target_project = mapping.container_image_project
            elif item.artifact_type == "helm-chart":
                target_project = mapping.helm_chart_project
        if target_project is None and identity_fallback:
            target_project = source_project

        base = dict(
            index=item.index,
            artifact_type=item.artifact_type,
            source_repository=item.repository,
            source_project=source_project,
            name=item.name,
            reference=item.reference,
            version=item.version,
            expected_digest=item.expected_digest,
            payload_size=item.payload_size,
        )
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
        try:
            if item.artifact_type == "container-image":
                if not item.reference:
                    raise ValueError("Container image reference отсутствует")
                target = ImageReference(target_repository, item.reference)
                capability_repository = target.repository
                final_reference = self._image_display_reference(
                    validator.registry_host,
                    target.repository,
                    target.reference,
                )
            elif item.artifact_type == "helm-chart":
                if not item.name or not item.version:
                    raise ValueError("Helm chart name/version отсутствуют")
                target = HelmChartReference(target_repository, item.name, item.version)
                capability_repository = target.harbor_repository
                final_reference = (
                    f"oci://{validator.registry_host}/{target.harbor_repository}:{target.version}"
                )
            else:
                raise ValueError("Unsupported bundle artifact type")
        except ValueError as exc:
            return ImportDestinationArtifactPlanResponse(
                **base,
                target_project=target_project,
                target_repository=target_repository,
                classification=ImportPreviewState.ERROR,
                error_code="import_destination_reference_invalid",
                message=str(exc),
            )

        capability = await validator.validate(target_project, capability_repository)
        if not capability.project_exists or not capability.write_allowed:
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

        try:
            if item.artifact_type == "container-image":
                inspected = await skopeo.inspect_target(
                    target,
                    expected_digest=item.expected_digest,
                )
                classification = {
                    TargetState.ABSENT: ImportPreviewState.NEW,
                    TargetState.SAME_DIGEST: ImportPreviewState.SAME,
                    TargetState.CONFLICTING_DIGEST: ImportPreviewState.CONFLICT,
                    TargetState.PRESENT: ImportPreviewState.UNKNOWN,
                }[inspected.state]
            else:
                inspected = await helm.inspect_target(
                    target,
                    expected_digest=item.expected_digest,
                )
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
        except (SkopeoServiceError, HelmServiceError, ValueError) as exc:
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
            if destination_plan_id is not None:
                raise ImportOrchestrationError(
                    "import_destination_plan_not_ready",
                    "Указанный destination plan не найден",
                )
            # Compatibility until #191 makes mapping explicit in the UI. Even
            # identity routing is persisted and validated before Harbor mutation.
            plan = await self._build_destination_plan(
                operation_id,
                ImportDestinationPlanRequest(),
                identity_fallback=True,
            )
        if destination_plan_id is not None and destination_plan_id != plan.plan_id:
            raise ImportOrchestrationError(
                "import_destination_plan_stale",
                "Destination plan изменился; обновите Preview перед Import",
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
            policy = self._policy_object(operation)
            persisted = self._plan_from_policy(policy)
            if persisted is None or persisted.plan_id != plan.plan_id:
                raise ImportOrchestrationError(
                    "import_destination_plan_stale",
                    "Persisted destination plan изменился перед запуском Import",
                )
            policy["execution"] = {
                "overwrite_conflicts": overwrite_conflicts,
                "requested_by": actor_username,
                "requested_at": requested_at.isoformat(),
            }
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
        context.transition(OperationStatus.IMPORTING)
        operation, preview, plan, overwrite, requested_at = self._load_destination_execution_state(
            operation_id
        )
        archive, sidecar = self._bundle_paths(operation_id)
        observed_sha = await asyncio.to_thread(self._sha256_file, archive)
        if (
            observed_sha != operation.bundle_sha256
            or observed_sha != preview.bundle_sha256
            or observed_sha != plan.bundle_sha256
        ):
            raise OperationTaskFailure(
                "import_bundle_changed",
                "Bundle изменился после destination preview; import отменён до mutation Harbor",
            )

        extraction = self.extract_root / f"import-{operation_id}"
        self._remove_path(extraction)
        try:
            verified = await asyncio.to_thread(
                self.package_factory().verify_bundle,
                archive,
                sidecar_path=sidecar,
                extract_to=extraction,
            )
        except Exception as exc:
            code = getattr(exc, "code", "import_bundle_verification_failed")
            raise OperationTaskFailure(code, str(exc)) from exc
        if (
            verified.archive_sha256 != plan.bundle_sha256
            or verified.manifest.delivery_id != preview.source_delivery_id
            or verified.extracted_root is None
        ):
            raise OperationTaskFailure(
                "import_bundle_changed",
                "Bundle identity не совпадает с persisted destination plan",
            )

        rows = sorted(operation.artifacts, key=lambda item: item.id)
        descriptors = verified.manifest.artifacts
        if len(rows) != len(descriptors) or len(plan.artifacts) != len(descriptors):
            raise OperationTaskFailure(
                "import_preview_artifacts_changed",
                "Состав destination plan не совпадает с signed manifest",
            )

        failures = 0
        context.set_progress(current=0, total=len(rows))
        with self.session_factory() as session:
            skopeo = self.skopeo_factory(session)
            helm = self.helm_factory(session)
            for index, (row, descriptor, planned) in enumerate(
                zip(rows, descriptors, plan.artifacts, strict=True)
            ):
                context.raise_if_cancelled()
                self._assert_plan_matches_descriptor(index, descriptor, planned)
                try:
                    outcome = await self._preflight_planned_target(planned, skopeo, helm)
                except (SkopeoServiceError, HelmServiceError, ValueError) as exc:
                    context.set_artifact_status(
                        row.id,
                        ArtifactStatus.FAILED,
                        error_code=getattr(exc, "code", "import_target_inspection_failed"),
                        error_message=str(exc),
                    )
                    failures += 1
                    context.set_progress(current=index + 1, total=len(rows))
                    continue

                if outcome[0] is ImportPreviewState.SAME:
                    context.set_artifact_status(
                        row.id,
                        ArtifactStatus.SKIPPED,
                        target_digest=outcome[1],
                    )
                    context.set_progress(current=index + 1, total=len(rows))
                    continue
                if outcome[0] is ImportPreviewState.CONFLICT and not overwrite:
                    context.set_artifact_status(
                        row.id,
                        ArtifactStatus.CONFLICT,
                        target_digest=outcome[1],
                    )
                    failures += 1
                    context.set_progress(current=index + 1, total=len(rows))
                    continue
                if outcome[0] in {ImportPreviewState.UNKNOWN, ImportPreviewState.ERROR}:
                    context.set_artifact_status(
                        row.id,
                        ArtifactStatus.FAILED,
                        error_code="import_target_state_unresolved",
                        error_message="TARGET state нельзя безопасно разрешить перед mutation",
                    )
                    failures += 1
                    context.set_progress(current=index + 1, total=len(rows))
                    continue

                context.set_artifact_status(row.id, ArtifactStatus.RUNNING)
                payload = verified.extracted_root.joinpath(
                    *PurePosixPath(descriptor.payload_path).parts
                )
                try:
                    if isinstance(descriptor, ContainerImageArtifact):
                        if planned.target_repository is None or planned.reference is None:
                            raise ValueError("Persisted image destination incomplete")
                        imported = await skopeo.import_image(
                            payload,
                            ImageReference(planned.target_repository, planned.reference),
                            expected_digest=descriptor.source_digest,
                        )
                        target_digest = imported.target_digest
                    else:
                        if (
                            planned.target_repository is None
                            or planned.name is None
                            or planned.version is None
                        ):
                            raise ValueError("Persisted Helm destination incomplete")
                        pushed = await helm.push_chart(
                            payload,
                            HelmChartReference(
                                planned.target_repository,
                                planned.name,
                                planned.version,
                            ),
                            source_digest=descriptor.source_digest,
                            allow_existing=(
                                overwrite and outcome[0] is ImportPreviewState.CONFLICT
                            ),
                        )
                        if pushed.package.sha256 != descriptor.payload_sha256:
                            raise HelmServiceError(
                                "helm_payload_digest_mismatch",
                                "Helm package SHA-256 не совпадает с signed bundle payload",
                            )
                        target_digest = pushed.target_digest
                    context.set_artifact_status(
                        row.id,
                        ArtifactStatus.VERIFIED,
                        target_digest=target_digest,
                    )
                except (SkopeoServiceError, HelmServiceError, ValueError) as exc:
                    context.set_artifact_status(
                        row.id,
                        ArtifactStatus.FAILED,
                        error_code=getattr(exc, "code", "import_artifact_failed"),
                        error_message=str(exc),
                    )
                    failures += 1
                context.set_progress(current=index + 1, total=len(rows))

        context.transition(OperationStatus.VERIFYING_TARGET)
        self._write_destination_receipt(
            operation_id,
            preview,
            plan,
            overwrite,
            requested_at,
            failures,
        )
        if failures:
            raise OperationTaskFailure(
                "import_partial_failure",
                "Import завершён с ошибками отдельных артефактов; rollback не выполнялся",
            )
        context.transition(OperationStatus.COMPLETED)

    async def _preflight_planned_target(
        self,
        planned: ImportDestinationArtifactPlanResponse,
        skopeo: SkopeoService,
        helm: HelmOciService,
    ) -> tuple[ImportPreviewState, str | None]:
        if planned.target_repository is None:
            raise ValueError("Persisted destination repository missing")
        if planned.artifact_type == "container-image":
            if planned.reference is None:
                raise ValueError("Persisted image reference missing")
            inspected = await skopeo.inspect_target(
                ImageReference(planned.target_repository, planned.reference),
                expected_digest=planned.expected_digest,
            )
            return (
                {
                    TargetState.ABSENT: ImportPreviewState.NEW,
                    TargetState.SAME_DIGEST: ImportPreviewState.SAME,
                    TargetState.CONFLICTING_DIGEST: ImportPreviewState.CONFLICT,
                    TargetState.PRESENT: ImportPreviewState.UNKNOWN,
                }[inspected.state],
                inspected.digest,
            )
        if planned.name is None or planned.version is None:
            raise ValueError("Persisted Helm identity missing")
        inspected = await helm.inspect_target(
            HelmChartReference(planned.target_repository, planned.name, planned.version),
            expected_digest=planned.expected_digest,
        )
        return (
            {
                HelmTargetState.ABSENT: ImportPreviewState.NEW,
                HelmTargetState.SAME_DIGEST: ImportPreviewState.SAME,
                HelmTargetState.CONFLICTING_DIGEST: ImportPreviewState.CONFLICT,
                HelmTargetState.PRESENT: ImportPreviewState.UNKNOWN,
            }[inspected.state],
            inspected.digest,
        )

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
            raise OperationTaskFailure(
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
            raise OperationTaskFailure(
                "import_destination_plan_tampered",
                "Persisted destination artifact identity не совпадает с signed manifest",
            )

    def _load_destination_execution_state(
        self,
        operation_id: int,
    ) -> tuple[Operation, ImportPreviewResponse, ImportDestinationPlanResponse, bool, datetime]:
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
            policy = self._policy_object(operation)
            plan = self._plan_from_policy(policy)
            execution = policy.get("execution")
            if plan is None or not isinstance(execution, dict):
                raise OperationTaskFailure(
                    "import_execution_state_invalid",
                    "Destination plan/execution policy отсутствует",
                )
            requested_at_raw = execution.get("requested_at")
            if not isinstance(requested_at_raw, str):
                raise OperationTaskFailure(
                    "import_execution_state_invalid",
                    "Import requested_at отсутствует",
                )
            requested_at = datetime.fromisoformat(requested_at_raw)
            overwrite = bool(execution.get("overwrite_conflicts", False))
            session.expunge(operation)
            for artifact in operation.artifacts:
                session.expunge(artifact)
            return operation, preview, plan, overwrite, requested_at

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
            if operation.bundle_sha256 != plan.bundle_sha256:
                raise ImportOrchestrationError(
                    "import_destination_plan_stale",
                    "Bundle изменился во время построения destination plan",
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
        return self._plan_from_policy(self._policy_object(operation))

    @staticmethod
    def _plan_from_policy(policy: dict[str, Any]) -> ImportDestinationPlanResponse | None:
        raw = policy.get("destination_plan")
        if raw is None:
            return None
        try:
            return ImportDestinationPlanResponse.model_validate(raw)
        except ValueError as exc:
            raise ImportOrchestrationError(
                "import_destination_plan_invalid",
                "Persisted destination plan повреждён",
            ) from exc

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
    def _plan_id(
        bundle_sha256: str,
        artifacts: list[ImportDestinationArtifactPlanResponse],
    ) -> str:
        identity = {
            "bundle_sha256": bundle_sha256,
            "artifacts": [
                {
                    "index": item.index,
                    "artifact_type": item.artifact_type,
                    "source_repository": item.source_repository,
                    "source_project": item.source_project,
                    "name": item.name,
                    "reference": item.reference,
                    "version": item.version,
                    "expected_digest": item.expected_digest,
                    "target_project": item.target_project,
                    "target_repository": item.target_repository,
                    "final_reference": item.final_reference,
                }
                for item in artifacts
            ],
        }
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _image_display_reference(host: str, repository: str, reference: str) -> str:
        separator = "@" if reference.startswith("sha256:") else ":"
        return f"{host}/{repository}{separator}{reference}"

    def _write_destination_receipt(
        self,
        operation_id: int,
        preview: ImportPreviewResponse,
        plan: ImportDestinationPlanResponse,
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
            rows = sorted(operation.artifacts, key=lambda item: item.id)
            receipt = ImportReceiptResponse(
                operation_id=operation.id,
                source_delivery_id=preview.source_delivery_id,
                bundle_sha256=preview.bundle_sha256,
                actor_username=operation.actor_username,
                started_at=requested_at,
                finished_at=now,
                overwrite_conflicts=overwrite,
                destination_plan_id=plan.plan_id,
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
                import os

                os.fsync(handle.fileno())
            os.chmod(path, 0o440)
            self._fsync_directory(self.receipt_root)
        except FileExistsError as exc:
            raise OperationTaskFailure(
                "import_receipt_exists",
                "Immutable import receipt уже существует",
            ) from exc
