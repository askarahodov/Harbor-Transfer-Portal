from __future__ import annotations

import json
from dataclasses import dataclass
from re import fullmatch
from typing import Final

from sqlalchemy.orm import Session

from app.db.repositories import SettingMetadataRepository
from app.schemas.imports import ImportDestinationPlanRequest

_PROJECT_PATTERN: Final = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
_KEYS: Final = {
    "revision": "transfer.destination_mapping.revision",
    "container_image_project": "transfer.destination_mapping.container_image_project",
    "helm_chart_project": "transfer.destination_mapping.helm_chart_project",
    "project_mappings": "transfer.destination_mapping.project_mappings",
}
_POLICY_FIELDS: Final = frozenset(
    {"container_image_project", "helm_chart_project", "project_mappings"}
)


@dataclass(frozen=True, slots=True)
class DestinationMappingPolicySnapshot:
    revision: int
    container_image_project: str | None
    helm_chart_project: str | None
    project_mappings: dict[str, str]


@dataclass(frozen=True, slots=True)
class DestinationMappingPolicyChange:
    changed_fields: tuple[str, ...]
    before: dict[str, object]
    after: dict[str, object]
    snapshot: DestinationMappingPolicySnapshot


@dataclass(slots=True)
class DestinationMappingPolicyError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class DestinationMappingPolicyService:
    """Persistent admin-managed defaults used when a new TARGET plan is built."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.metadata = SettingMetadataRepository(session)

    def resolve(self) -> DestinationMappingPolicySnapshot:
        revision = self._revision()
        container = self._optional_project(_KEYS["container_image_project"])
        helm = self._optional_project(_KEYS["helm_chart_project"])
        mappings = self._project_mappings()
        return DestinationMappingPolicySnapshot(
            revision=revision,
            container_image_project=container,
            helm_chart_project=helm,
            project_mappings=mappings,
        )

    def update(self, values: dict[str, object]) -> DestinationMappingPolicyChange:
        unknown = sorted(set(values) - _POLICY_FIELDS)
        if unknown:
            raise DestinationMappingPolicyError(
                "destination_mapping_policy_unknown_field",
                f"Неподдерживаемые поля destination mapping policy: {', '.join(unknown)}",
            )
        current = self.resolve()
        candidate = {
            "container_image_project": current.container_image_project,
            "helm_chart_project": current.helm_chart_project,
            "project_mappings": current.project_mappings,
            **values,
        }
        normalized = self._normalize_candidate(candidate)
        before_values = {
            "container_image_project": current.container_image_project,
            "helm_chart_project": current.helm_chart_project,
            "project_mappings": current.project_mappings,
        }
        changed = tuple(
            sorted(field for field in values if normalized[field] != before_values[field])
        )
        before = {field: before_values[field] for field in changed}
        after = {field: normalized[field] for field in changed}
        if changed:
            for field in changed:
                self.metadata.set_value(_KEYS[field], self._serialize(field, normalized[field]))
            self.metadata.set_value(_KEYS["revision"], str(current.revision + 1))
        return DestinationMappingPolicyChange(
            changed_fields=changed,
            before=before,
            after=after,
            snapshot=self.resolve(),
        )

    def resolve_request(self, request: ImportDestinationPlanRequest) -> ImportDestinationPlanRequest:
        snapshot = self.resolve()
        merged_mappings = {**snapshot.project_mappings, **request.project_mappings}
        return request.model_copy(
            update={
                "mapping_policy_revision": snapshot.revision,
                "container_image_project": (
                    request.container_image_project or snapshot.container_image_project
                ),
                "helm_chart_project": request.helm_chart_project or snapshot.helm_chart_project,
                "project_mappings": merged_mappings,
            }
        )

    def _revision(self) -> int:
        raw = self.metadata.get_value(_KEYS["revision"])
        if raw is None:
            return 0
        try:
            revision = int(raw)
        except ValueError as exc:
            raise DestinationMappingPolicyError(
                "destination_mapping_policy_persisted_invalid",
                "Сохранённая revision destination mapping policy некорректна",
            ) from exc
        if revision < 0:
            raise DestinationMappingPolicyError(
                "destination_mapping_policy_persisted_invalid",
                "Сохранённая revision destination mapping policy некорректна",
            )
        return revision

    def _optional_project(self, key: str) -> str | None:
        raw = self.metadata.get_value(key)
        if raw in {None, ""}:
            return None
        return self._validate_project(raw)

    def _project_mappings(self) -> dict[str, str]:
        raw = self.metadata.get_value(_KEYS["project_mappings"])
        if raw in {None, ""}:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DestinationMappingPolicyError(
                "destination_mapping_policy_persisted_invalid",
                "Сохранённые source→target mappings повреждены",
            ) from exc
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(target, str) for key, target in value.items()
        ):
            raise DestinationMappingPolicyError(
                "destination_mapping_policy_persisted_invalid",
                "Сохранённые source→target mappings имеют неверный формат",
            )
        return self._validate_mappings(value)

    def _normalize_candidate(self, candidate: dict[str, object]) -> dict[str, object]:
        result: dict[str, object] = {}
        for field in ("container_image_project", "helm_chart_project"):
            raw = candidate[field]
            if raw is None:
                result[field] = None
            elif not isinstance(raw, str):
                raise DestinationMappingPolicyError(
                    "destination_mapping_policy_invalid",
                    f"{field} должен быть Harbor project или null",
                )
            else:
                stripped = raw.strip()
                result[field] = self._validate_project(stripped) if stripped else None
        raw_mappings = candidate["project_mappings"]
        if not isinstance(raw_mappings, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in raw_mappings.items()
        ):
            raise DestinationMappingPolicyError(
                "destination_mapping_policy_invalid",
                "project_mappings должен быть объектом source project → target project",
            )
        result["project_mappings"] = self._validate_mappings(raw_mappings)
        return result

    def _validate_mappings(self, mappings: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for source, target in mappings.items():
            source_name = source.strip()
            target_name = target.strip()
            normalized[self._validate_project(source_name)] = self._validate_project(target_name)
        return {key: normalized[key] for key in sorted(normalized)}

    @staticmethod
    def _validate_project(value: str) -> str:
        if fullmatch(_PROJECT_PATTERN, value) is None:
            raise DestinationMappingPolicyError(
                "destination_mapping_policy_project_invalid",
                f"Недопустимое имя Harbor project: {value!r}",
            )
        return value

    @staticmethod
    def _serialize(field: str, value: object) -> str:
        if field == "project_mappings":
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "" if value is None else str(value)
