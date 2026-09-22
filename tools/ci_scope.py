#!/usr/bin/env python3
"""Deterministic changed-path classification for scoped GitHub Actions checks.

The module intentionally uses only Python's standard library so the scope job can
run immediately after checkout without installing project dependencies.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

_AREA_NAMES = (
    "backend",
    "frontend",
    "protocol",
    "security",
    "integration",
    "compose",
    "docs",
)

_BACKEND_RUNTIME_DEPENDENCY_FILES = {
    "backend/pyproject.toml",
    "backend/requirements-runtime.lock",
    "backend/uv.lock",
}

_SECURITY_SERVICE_FILES = {
    "bundle_package_service.py",
    "export_orchestrator.py",
    "export_publication_guard.py",
    "harbor_client.py",
    "harbor_profiles.py",
    "import_helm_service.py",
    "import_orchestrator.py",
    "key_management.py",
    "skopeo_service.py",
    "helm_oci_service.py",
}
_SECURITY_API_FILES = {
    "auth.py",
    "exports.py",
    "harbor_projects.py",
    "imports.py",
    "key_settings.py",
    "users.py",
}
_SECURITY_TEST_PREFIXES = (
    "test_auth",
    "test_bundle_package_",
    "test_export_",
    "test_exports_api",
    "test_harbor_client",
    "test_harbor_profiles",
    "test_harbor_project_creation",
    "test_import_",
    "test_key_management_",
    "test_helm_oci_",
    "test_login_rate_limit",
    "test_skopeo_service",
    "test_structured_logging",
    "test_user_admin_api",
)
_INTEGRATION_SERVICE_FILES = {
    "bundle_package_service.py",
    "export_orchestrator.py",
    "export_publication_guard.py",
    "import_helm_service.py",
    "import_orchestrator.py",
    "operation_manager.py",
    "report_service.py",
    "skopeo_service.py",
    "helm_oci_service.py",
}
_INTEGRATION_API_FILES = {
    "exports.py",
    "imports.py",
}
_INTEGRATION_DEPLOY_FILES = {
    "compose.yaml",
    "deploy/build-offline-kit.sh",
    "deploy/compose-registry-integration.yml",
    "deploy/compose-isolated-transfer-acceptance.yml",
    "deploy/qualify-clean-offline-install.sh",
    "deploy/qualify-isolated-transfer.sh",
    "deploy/smoke-registry-integration.sh",
}


@dataclass(frozen=True, slots=True)
class Scope:
    backend: bool = False
    frontend: bool = False
    protocol: bool = False
    security: bool = False
    integration: bool = False
    compose: bool = False
    docs: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "backend": self.backend,
            "frontend": self.frontend,
            "protocol": self.protocol,
            "security": self.security,
            "integration": self.integration,
            "compose": self.compose,
            "docs": self.docs,
        }


def _under(path: PurePosixPath, directory: str) -> bool:
    return bool(path.parts) and path.parts[0] == directory


def _starts_with(path: PurePosixPath, *parts: str) -> bool:
    return path.parts[: len(parts)] == parts


def _is_deploy_markdown(path: PurePosixPath) -> bool:
    return len(path.parts) >= 2 and path.parts[0] == "deploy" and path.suffix == ".md"


def _is_protocol_adr(path: PurePosixPath) -> bool:
    return (
        len(path.parts) == 3
        and path.parts[:2] == ("docs", "adr")
        and path.name.startswith("ADR-009-")
    )


def _is_security_sensitive_backend(path: PurePosixPath) -> bool:
    if _starts_with(path, "backend", "app", "auth"):
        return True
    if (
        path.parts[:3] == ("backend", "app", "services")
        and path.name in _SECURITY_SERVICE_FILES
    ):
        return True
    if (
        path.parts[:3] == ("backend", "app", "api")
        and path.name in _SECURITY_API_FILES
    ):
        return True
    if path.parts[:2] == ("backend", "tests"):
        return any(path.name.startswith(prefix) for prefix in _SECURITY_TEST_PREFIXES)
    return False


def _is_local_registry_integration_path(path: PurePosixPath, path_text: str) -> bool:
    if path_text in {
        "backend/Dockerfile",
        "backend/app/domain/bundle.py",
    }:
        return True
    if (
        path.parts[:3] == ("backend", "app", "services")
        and path.name in _INTEGRATION_SERVICE_FILES
    ):
        return True
    if (
        path.parts[:3] == ("backend", "app", "api")
        and path.name in _INTEGRATION_API_FILES
    ):
        return True
    if _starts_with(path, "backend", "integration"):
        return True
    if _starts_with(path, "deploy", "offline") and path.suffix == ".sh":
        return True
    return path_text in _INTEGRATION_DEPLOY_FILES


def _is_package_protocol_path(path_text: str) -> bool:
    return path_text == "backend/app/services/bundle_package_service.py" or path_text.startswith(
        "backend/tests/test_bundle_package_"
    )


def _classify_path(path_text: str) -> tuple[set[str], bool]:
    path = PurePosixPath(path_text)
    areas: set[str] = set()
    workflow_changed = path in {
        PurePosixPath(".github/workflows/ci.yml"),
        PurePosixPath("tools/ci_scope.py"),
    }

    if _under(path, "backend") or path_text == "Makefile":
        areas.add("backend")

    if path_text in {
        ".env.example",
        "compose.yaml",
        "deploy/offline/compose.yaml",
        "frontend/nginx.conf",
    }:
        # Backend regression tests assert the browser/runtime trust boundary
        # across these deployment files, so changes must execute that suite.
        areas.add("backend")

    if path_text in _BACKEND_RUNTIME_DEPENDENCY_FILES:
        # Runtime dependency graph changes can alter crypto and transfer behavior
        # without touching application source, so qualify both boundaries.
        areas.update({"security", "integration"})

    if _under(path, "frontend") or path_text == "Makefile":
        areas.add("frontend")

    if path_text in {"tools/dev.py", "tools/test_dev.py", "dev.ps1"}:
        areas.update({"frontend", "compose", "docs"})

    if (
        _starts_with(path, "backend", "app", "domain")
        or _is_package_protocol_path(path_text)
        or path_text in {
            "backend/tests/test_bundle_protocol.py",
            "backend/tests/test_bundle_schema.py",
            "docs/offline-bundle-v1.md",
        }
        or _starts_with(path, "docs", "schema")
        or _is_protocol_adr(path)
    ):
        areas.add("protocol")

    if _is_security_sensitive_backend(path):
        areas.add("security")

    if _is_local_registry_integration_path(path, path_text):
        areas.add("integration")

    if (
        path_text
        in {
            "compose.yaml",
            ".dockerignore",
            "Makefile",
            "backend/Dockerfile",
            "frontend/Dockerfile",
            "frontend/nginx.conf",
        }
        or _starts_with(path, "frontend", "docker-entrypoint.d")
        or (_under(path, "deploy") and not _is_deploy_markdown(path))
    ):
        areas.add("compose")

    if (
        path_text
        in {
            "README.md",
            "CONTRIBUTING.md",
            "tools/check_doc_links.py",
            "tools/test_check_doc_links.py",
            "Makefile",
        }
        or _under(path, "docs")
        or _is_deploy_markdown(path)
    ):
        areas.add("docs")

    return areas, workflow_changed


def _integration_markers_exist(root: Path) -> bool:
    return all(
        path.is_file()
        for path in (
            root / "backend/integration/registry_smoke.py",
            root / "deploy/compose-registry-integration.yml",
            root / "deploy/smoke-registry-integration.sh",
            root / "backend/integration/isolated_transfer_acceptance.py",
            root / "deploy/compose-isolated-transfer-acceptance.yml",
            root / "deploy/qualify-isolated-transfer.sh",
        )
    )


def classify_paths(paths: Iterable[str], *, root: Path = Path(".")) -> Scope:
    areas: set[str] = set()
    workflow_changed = False

    for raw_path in paths:
        path = raw_path.strip()
        if not path:
            continue
        path_areas, changed_workflow = _classify_path(path)
        areas.update(path_areas)
        workflow_changed = workflow_changed or changed_workflow

    if workflow_changed:
        areas.update({"backend", "protocol", "security", "docs"})
        if (root / "frontend/package.json").is_file():
            areas.add("frontend")
        if _integration_markers_exist(root):
            areas.add("integration")
        if (root / "compose.yaml").is_file():
            areas.add("compose")

    # Do not create a green/skipped fiction for components absent in the checked revision.
    if not (root / "backend/pyproject.toml").is_file():
        areas.discard("backend")
    if not (root / "frontend/package.json").is_file():
        areas.discard("frontend")
    if not (root / "backend/tests/test_bundle_protocol.py").is_file():
        areas.discard("protocol")
    if not (root / "backend/tests/test_bundle_package_service.py").is_file():
        areas.discard("security")
    if not _integration_markers_exist(root):
        areas.discard("integration")
    if not (
        (root / "compose.yaml").is_file()
        and (root / "deploy/smoke-compose.sh").is_file()
    ):
        areas.discard("compose")
    if not (root / "tools/check_doc_links.py").is_file():
        areas.discard("docs")

    return Scope(**{name: name in areas for name in _AREA_NAMES})


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def write_github_outputs(scope: Scope, output_path: Path) -> None:
    with output_path.open("a", encoding="utf-8") as handle:
        for name, enabled in scope.as_dict().items():
            handle.write(f"{name}={_bool_text(enabled)}\n")


def write_summary(scope: Scope, summary_path: Path) -> None:
    labels = {
        "backend": "Backend",
        "frontend": "Frontend",
        "protocol": "Bundle protocol",
        "security": "Security regression",
        "integration": "Skopeo/Helm + isolated transfer integration",
        "compose": "Compose",
        "docs": "Documentation",
    }
    with summary_path.open("a", encoding="utf-8") as handle:
        handle.write("### Область CI\n\n")
        handle.write("| Область | Запуск |\n")
        handle.write("| --- | --- |\n")
        for name, enabled in scope.as_dict().items():
            handle.write(f"| {labels[name]} | {_bool_text(enabled)} |\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("changed_files", type=Path, help="newline-separated changed paths")
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--summary", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = args.changed_files.read_text(encoding="utf-8").splitlines()
    scope = classify_paths(paths, root=args.root)

    if args.github_output is not None:
        write_github_outputs(scope, args.github_output)
    else:
        for name, enabled in scope.as_dict().items():
            print(f"{name}={_bool_text(enabled)}")

    if args.summary is not None:
        write_summary(scope, args.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())