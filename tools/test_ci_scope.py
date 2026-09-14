from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tools.ci_scope import Scope, classify_paths


class CiScopeTest(TestCase):
    def _root(self, *, backend=True, frontend=True, protocol=True, compose=True, docs=True):
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        if backend:
            (root / "backend").mkdir(parents=True, exist_ok=True)
            (root / "backend/pyproject.toml").touch()
        if protocol:
            (root / "backend/tests").mkdir(parents=True, exist_ok=True)
            (root / "backend/tests/test_bundle_protocol.py").touch()
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
            classify_paths(["backend/app/services/harbor_client.py"], root=root),
            Scope(backend=True),
        )

    def test_frontend_only_runs_frontend(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["frontend/src/views/DashboardView.vue"], root=root),
            Scope(frontend=True),
        )

    def test_protocol_backend_path_runs_backend_and_protocol(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["backend/app/domain/protocol.py"], root=root),
            Scope(backend=True, protocol=True),
        )

    def test_deploy_markdown_runs_compose_and_docs(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["deploy/README.md"], root=root),
            Scope(compose=True, docs=True),
        )

    def test_makefile_keeps_current_multi_area_policy(self):
        root = self._root()
        self.assertEqual(
            classify_paths(["Makefile"], root=root),
            Scope(backend=True, frontend=True, docs=True),
        )

    def test_workflow_change_self_tests_all_existing_areas(self):
        root = self._root()
        self.assertEqual(
            classify_paths([".github/workflows/ci.yml"], root=root),
            Scope(backend=True, frontend=True, protocol=True, compose=True, docs=True),
        )

    def test_workflow_change_does_not_invent_missing_components(self):
        root = self._root(backend=False, frontend=False, protocol=False, compose=False, docs=False)
        self.assertEqual(classify_paths([".github/workflows/ci.yml"], root=root), Scope())

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
            Scope(backend=True, frontend=True, docs=True),
        )
