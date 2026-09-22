from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.artifacts import ArtifactKind, classify_artifact_kind
from app.schemas.exports import ExportArtifactSelection
from app.services.harbor_client import HarborArtifact, HarborClientError


class HarborArtifactLookup(Protocol):
    def get_artifact(
        self,
        project: str,
        repository: str,
        reference: str,
    ) -> HarborArtifact: ...


@dataclass(slots=True)
class ExportSelectionResolutionError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ResolvedExportArtifact:
    kind: ArtifactKind
    project: str
    repository: str
    reference: str
    digest: str
    size_bytes: int | None

    @property
    def full_repository(self) -> str:
        return f"{self.project}/{self.repository}"


_HARBOR_ERROR_MAPPING = {
    "unauthorized": ("harbor_auth_failed", "Harbor отклонил учётные данные"),
    "forbidden": ("harbor_forbidden", "Harbor запретил доступ порталу"),
    "timeout": ("harbor_unavailable", "Harbor не ответил вовремя"),
    "tls_failed": ("harbor_tls_failed", "Не удалось проверить TLS Harbor"),
    "connection_failed": (
        "harbor_unavailable",
        "Не удалось подключиться к Harbor",
    ),
    "harbor_unavailable": ("harbor_unavailable", "Harbor временно недоступен"),
    "rate_limited": ("harbor_rate_limited", "Harbor ограничил частоту запросов"),
    "invalid_response": (
        "harbor_invalid_response",
        "Harbor вернул некорректный ответ",
    ),
}


def _normalize_harbor_error(exc: HarborClientError) -> ExportSelectionResolutionError:
    code, message = _HARBOR_ERROR_MAPPING.get(
        exc.code,
        ("harbor_error", "Ошибка обращения к локальному Harbor"),
    )
    return ExportSelectionResolutionError(code, message)


def resolve_export_selection(
    client: HarborArtifactLookup,
    selection: ExportArtifactSelection,
) -> ResolvedExportArtifact:
    try:
        artifact = client.get_artifact(
            selection.project,
            selection.repository,
            selection.reference,
        )
    except HarborClientError as exc:
        if exc.code == "not_found":
            raise ExportSelectionResolutionError(
                "export_source_not_found",
                "Выбранный SOURCE artifact больше не найден",
            ) from exc
        raise _normalize_harbor_error(exc) from exc

    actual_kind = classify_artifact_kind(
        artifact.type,
        artifact.media_type,
        artifact.extra_attrs,
    )
    if actual_kind is ArtifactKind.UNKNOWN_OCI:
        raise ExportSelectionResolutionError(
            "export_artifact_unsupported",
            "Этот OCI artifact type не поддерживается export v1",
        )
    if actual_kind is not selection.kind:
        raise ExportSelectionResolutionError(
            "export_artifact_kind_changed",
            "Тип SOURCE artifact изменился после выбора",
        )
    if artifact.digest != selection.digest:
        raise ExportSelectionResolutionError(
            "export_source_changed",
            "SOURCE artifact digest изменился после выбора",
        )

    return ResolvedExportArtifact(
        kind=actual_kind,
        project=selection.project,
        repository=selection.repository,
        reference=selection.reference,
        digest=artifact.digest,
        size_bytes=artifact.size,
    )
