from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tools.ci_scope import Scope, classify_paths


class CiScopeTest(TestCase):
    def _root(
        self,
        *,
        backend=True,
        frontend=True,
        protocol=True,
        security=True,
        integration=True,
        compose=True,
        docs=True,
    ):
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        if backend:
            (root / "backend").mkdir(parents=True, exist_ok=True)
            (root / "backend/pyproject.toml").touch()
        if protocol:
            (root / "backend/tests").mkdir(parents=True, exist_ok=True)
            (root / "backend/tests/test_bundle_protocol.py").touch()
        if security:
            (root / "backend/tests").mkdir(parents=True, exist_ok=True)
            (root / "backend/tests/test_bundle_package_service.py").touch()
        if integration:
            (root / "backend/integration").mkdir(parents=True, exist_ok=True)
            (root / "backend/integration/registry_smoke.py").touch()
            (root / "backend/integration/isolated_transfer_acceptance.py").touch()
            (root / "deploy").mkdir(parents=True, exist_ok=True)
            (root / "deploy/compose-registry-integration.yml").touch()
            (root / "deploy/smoke-registry-integration.sh").touch()
            (root / "deploy/compose-isolated-transfer-acceptance.yml").touch()
            (root / "deploy/qualify-isolated-transfer.sh").touch()
        if frontend:
            (root / "frontend").mkdir(parents=True, exist_ok=True)
            (root / "frontend/package.json").touch()
        if compose:
            (root / "compose.yaml").touch()
            (root / "deploy").mkdir(parents=True, exist_ok=True)
            (root / "deploy/smoke-compose.sh").touch()
        if docs:
            (root / "tools").mkdir(parents=True, exist_ok=True)
            (root / "tools/check_doc_links.py").touch()
        self.addCleanup(temporary.cleanup)
        return root

    def test_docs_only_runs_only_docs(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["docs/architecture.md"], root=root),
            Scope(docs=True),
        )

    def test_backend_only_runs_backend(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["backend/app/schemas/harbor.py"], root=root),
            Scope(backend=True),
        )

    def test_frontend_only_runs_frontend(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["frontend/src/views/DashboardView.vue"], root=root),
            Scope(frontend=True),
        )

    def test_runtime_dependency_graph_runs_security_and_transfer_integration(self):
        root = self._root()
        for path in (
            "backend/pyproject.toml",
            "backend/requirements-runtime.lock",
            "backend/uv.lock",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    classify_paths([path], root=root),
                    Scope(backend=True, security=True, integration=True),
                )

    def test_protocol_backend_path_runs_backend_and_protocol(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["backend/app/domain/protocol.py"], root=root),
            Scope(backend=True, protocol=True),
        )

    def test_package_service_runs_protocol_security_and_transfer_integration(self):
        root = self._root()
        self.assertEqual(
            classify_paths(
                ["backend/app/services/bundle_package_service.py"],
                root=root,
            ),
            Scope(backend=True, protocol=True, security=True, integration=True),
        )

    def test_import_orchestrator_runs_security_and_transfer_integration(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["backend/app/services/import_orchestrator.py"], root=root),
            Scope(backend=True, security=True, integration=True),
        )

    def test_import_helm_adapter_runs_security_and_transfer_integration(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["backend/app/services/import_helm_service.py"], root=root),
            Scope(backend=True, security=True, integration=True),
        )

    def test_export_orchestrator_runs_security_and_transfer_integration(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["backend/app/services/export_orchestrator.py"], root=root),
            Scope(backend=True, security=True, integration=True),
        )

    def test_transfer_boundary_paths_keep_integration_gate(self):
        root = self._root()
        cases = {
            "backend/app/api/exports.py": Scope(
                backend=True,
                security=True,
                integration=True,
            ),
            "backend/app/api/imports.py": Scope(
                backend=True,
                security=True,
                integration=True,
            ),
            "backend/app/services/export_publication_guard.py": Scope(
                backend=True,
                security=True,
                integration=True,
            ),
            "backend/app/domain/bundle.py": Scope(
                backend=True,
                protocol=True,
                integration=True,
            ),
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classify_paths([path], root=root), expected)

    def test_harbor_profile_secret_paths_run_security_regression(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["backend/app/services/harbor_profiles.py"], root=root),
            Scope(backend=True, security=True, integration=True),
        )
        self.assertEqual(
            classify_paths(["backend/tests/test_harbor_profiles_api.py"], root=root),
            Scope(backend=True, security=True),
        )

    def test_harbor_profile_and_settings_changes_run_security_and_integration(self):
        root = self._root()
        for path in (
            "backend/app/services/harbor_settings.py",
            "backend/app/services/harbor_profiles.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    classify_paths([path], root=root),
                    Scope(backend=True, security=True, integration=True),
                )

    def test_harbor_project_mutation_paths_run_security_regression(self):
        root = self._root()
        for path in (
            "backend/app/services/harbor_client.py",
            "backend/app/api/harbor_projects.py",
            "backend/tests/test_harbor_client.py",
            "backend/tests/test_harbor_project_creation.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    classify_paths([path], root=root),
                    Scope(backend=True, security=True),
                )

    def test_key_management_runs_security_regression(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["backend/app/services/key_management.py"], root=root),
            Scope(backend=True, security=True),
        )

    def test_auth_changes_run_security_regression(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["backend/app/auth/security.py"], root=root),
            Scope(backend=True, security=True),
        )

    def test_skopeo_and_helm_changes_run_security_and_integration(self):
        root = self._root()
        self.assertEqual(
            classify_paths(
                [
                    "backend/app/services/skopeo_service.py",
                    "backend/app/services/helm_oci_service.py",
                ],
                root=root,
            ),
            Scope(backend=True, security=True, integration=True),
        )

    def test_backend_dockerfile_runs_runtime_integration(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["backend/Dockerfile"], root=root),
            Scope(backend=True, integration=True, compose=True),
        )

    def test_integration_harness_and_runner_keep_integration_scope(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["backend/integration/registry_smoke.py"], root=root),
            Scope(backend=True, integration=True),
        )
        self.assertEqual(
            classify_paths(["deploy/smoke-registry-integration.sh"], root=root),
            Scope(integration=True, compose=True),
        )
        self.assertEqual(
            classify_paths(["deploy/compose-registry-integration.yml"], root=root),
            Scope(integration=True, compose=True),
        )
        self.assertEqual(
            classify_paths(["backend/integration/isolated_transfer_acceptance.py"], root=root),
            Scope(backend=True, integration=True),
        )
        self.assertEqual(
            classify_paths(["deploy/qualify-isolated-transfer.sh"], root=root),
            Scope(integration=True, compose=True),
        )

    def test_release_runtime_scripts_keep_transfer_integration_scope(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["deploy/build-offline-kit.sh"], root=root),
            Scope(integration=True, compose=True),
        )
        self.assertEqual(
            classify_paths(["deploy/offline/install.sh"], root=root),
            Scope(integration=True, compose=True),
        )
        self.assertEqual(
            classify_paths(["compose.yaml"], root=root),
            Scope(backend=True, integration=True, compose=True),
        )

    def test_browser_runtime_boundary_files_run_backend_regression(self):
        root = self._root()
        cases = {
            ".env.example": Scope(backend=True),
            "frontend/nginx.conf": Scope(backend=True, frontend=True, compose=True),
            "deploy/offline/compose.yaml": Scope(backend=True, compose=True),
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classify_paths([path], root=root), expected)

    def test_export_api_and_regression_test_run_security_and_integration(self):
        root = self._root()
        self.assertEqual(
            classify_paths(
                [
                    "backend/app/api/exports.py",
                    "backend/tests/test_exports_api.py",
                ],
                root=root,
            ),
            Scope(backend=True, security=True, integration=True),
        )

    def test_security_regression_test_changes_keep_security_scope(self):
        root = self._root()
        self.assertEqual(
            classify_paths(
                [
                    "backend/tests/test_bundle_package_key_bounds.py",
                    "backend/tests/test_import_orchestrator.py",
                ],
                root=root,
            ),
            Scope(backend=True, protocol=True, security=True),
        )

    def test_nested_protocol_path_keeps_legacy_prefix_semantics(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["backend/app/domain/v1/nested.py"], root=root),
            Scope(backend=True, protocol=True),
        )
        self.assertEqual(
            classify_paths(["docs/schema/v1/nested/schema.json"], root=root),
            Scope(protocol=True, docs=True),
        )

    def test_deploy_markdown_runs_only_docs(self):
        root = self._root()
        for path in ("deploy/README.md", "deploy/offline/README.md"):
            with self.subTest(path=path):
                self.assertEqual(
                    classify_paths([path], root=root),
                    Scope(docs=True),
                )

    def test_nested_frontend_entrypoint_is_compose_scope(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["frontend/docker-entrypoint.d/hooks/40-runtime.sh"], root=root),
            Scope(frontend=True, compose=True),
        )

    def test_makefile_keeps_current_multi_area_policy(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["Makefile"], root=root),
            Scope(backend=True, frontend=True, compose=True, docs=True),
        )

    def test_cross_platform_dev_tooling_runs_frontend_compose_and_docs(self):
        root = self._root()
        for path in ("tools/dev.py", "tools/test_dev.py", "dev.ps1"):
            with self.subTest(path=path):
                self.assertEqual(
                    classify_paths([path], root=root),
                    Scope(frontend=True, compose=True, docs=True),
                )

    def test_workflow_change_self_tests_all_existing_areas(self):
        root = self._root()
        self.assertEqual(
            classify_paths([".github/workflows/ci.yml"], root=root),
            Scope(
                backend=True,
                frontend=True,
                protocol=True,
                security=True,
                integration=True,
                compose=True,
                docs=True,
            ),
        )

    def test_scope_policy_change_self_tests_all_existing_areas(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["tools/ci_scope.py"], root=root),
            Scope(
                backend=True,
                frontend=True,
                protocol=True,
                security=True,
                integration=True,
                compose=True,
                docs=True,
            ),
        )

    def test_workflow_change_does_not_invent_missing_components(self):
        root = self._root(
            backend=False,
            frontend=False,
            protocol=False,
            security=False,
            integration=False,
            compose=False,
            docs=False,
        )
        self.assertEqual(classify_paths([".github/workflows/ci.yml"], root=root), Scope())

    def test_security_scope_does_not_exist_without_regression_marker(self):
        root = self._root(security=False)
        self.assertEqual(
            classify_paths(["backend/app/services/import_orchestrator.py"], root=root),
            Scope(backend=True, integration=True),
        )

    def test_integration_scope_does_not_exist_without_harness_markers(self):
        root = self._root(integration=False)
        self.assertEqual(
            classify_paths(["backend/app/services/skopeo_service.py"], root=root),
            Scope(backend=True, security=True),
        )

    def test_mixed_diff_unions_scopes(self):
        root = self._root()
        self.assertEqual(
            classify_paths(
                [
                    "backend/app/services/harbor_client.py",
                    "frontend/src/views/SettingsView.vue",
                    "docs/security.md",
                ],
                root=root,
            ),
            Scope(backend=True, frontend=True, security=True, docs=True),
        )
