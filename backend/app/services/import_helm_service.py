from __future__ import annotations

import asyncio
from pathlib import Path

from app.services.helm_oci_service import (
    HelmChartReference,
    HelmOciService,
    HelmPhase,
    HelmPushResult,
    HelmServiceError,
    HelmTargetState,
)


class ImportHelmOciService(HelmOciService):
    """Helm OCI adapter for verified TARGET import payloads and overwrite policy."""

    def _validate_package_path(self, path: Path) -> Path:
        if path.is_symlink():
            raise HelmServiceError(
                "helm_package_invalid",
                "Ожидается обычный .tgz chart package без symlink",
            )

        resolved = path.resolve()
        verified_bundle_root = self.settings.bundle_extract_root.resolve()
        try:
            resolved.relative_to(verified_bundle_root)
        except ValueError:
            return super()._validate_package_path(path)

        if not resolved.is_file() or resolved.suffix != ".tgz":
            raise HelmServiceError(
                "helm_package_invalid",
                "Ожидается существующий .tgz chart package внутри verified bundle root",
            )
        return resolved

    async def push_chart(
        self,
        package: Path,
        target: HelmChartReference,
        *,
        source_digest: str | None = None,
        allow_existing: bool = False,
    ) -> HelmPushResult:
        if source_digest is not None:
            self._validate_optional_digest(source_digest)
        preflight = await self.inspect_target(target, expected_digest=source_digest)
        if preflight.state is not HelmTargetState.ABSENT and not allow_existing:
            raise HelmServiceError(
                "helm_target_exists",
                "TARGET уже содержит chart/version; решение конфликта выполняет import engine",
            )

        package_path = self._validate_package_path(package)
        harbor = self.harbor_settings.resolve()
        registry = self._registry_host(harbor)
        with self._security_context(harbor) as security:
            package_metadata = await self._validate_package(package_path, target, security)
            await self._login(registry, harbor, security, target)
            self._emit(HelmPhase.PUSHING, target)
            argv = (
                self.settings.helm_binary,
                "push",
                str(package_path),
                self._repository_oci_reference(registry, target),
                *self._tls_flags(harbor, security),
            )
            await self._run_checked(argv, security)

        self._emit(HelmPhase.VERIFYING_TARGET, target)
        target_digest = await asyncio.to_thread(self.digest_resolver, target)
        if target_digest is None:
            raise HelmServiceError(
                "helm_target_not_visible",
                "Harbor не подтвердил Helm artifact после успешного helm push",
            )
        self._validate_optional_digest(target_digest)
        self._emit(HelmPhase.PUSHED, target)
        return HelmPushResult(
            package=package_metadata,
            target_digest=target_digest,
            source_digest=source_digest,
            digest_matches_source=(
                target_digest == source_digest if source_digest is not None else None
            ),
        )
