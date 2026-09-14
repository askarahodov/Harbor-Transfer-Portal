from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.ci_scope import AREAS, classify_paths


class CiScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self._touch("backend/pyproject.toml")
        self._touch("frontend/package.json")
        self._touch("backend/tests/test_bundle_protocol.py")
        self._touch("compose.yaml")
        self._touch("deploy/smoke-compose.sh")
        self._touch("tools/check_doc_links.py")

    def _touch(self, relative: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    def _selected(self, *paths: str) -> set[str]:
        result = classify_paths(paths, self.root)
        self.assertEqual(set(result), set(AREAS))
        return {area for area, enabled in result.items() if enabled}

    def test_docs_only_selects_only_docs(self) -> None:
        self.assertEqual(self._selected("docs/architecture.md"), {"docs"})

    def test_backend_only_selects_backend(self) -> None:
        self.assertEqual(
            self._selected("backend/app/services/harbor_client.py"),
            {"backend"},
        )

    def test_frontend_only_selects_frontend(self) -> None:
        self.assertEqual(
            self._selected("frontend/src/views/LoginView.vue"),
            {"frontend"},
        )

    def test_backend_protocol_path_selects_backend_and_protocol(self) -> None:
        self.assertEqual(
            self._selected("backend/app/domain/protocol.py"),
            {"backend", "protocol"},
        )

    def test_protocol_document_selects_protocol_and_docs(self) -> None:
        self.assertEqual(
            self._selected("docs/schema/manifest-v1.schema.json"),
            {"protocol", "docs"},
        )

    def test_deploy_runtime_and_markdown_policy(self) -> None:
        self.assertEqual(
            self._selected("deploy/smoke-compose.sh"),
            {"compose"},
        )
        self.assertEqual(
            self._selected("deploy/README.md"),
            {"compose", "docs"},
        )

    def test_makefile_keeps_backend_frontend_and_docs_policy(self) -> None:
        self.assertEqual(
            self._selected("Makefile"),
            {"backend", "frontend", "docs"},
        )

    def test_workflow_change_selects_every_available_area(self) -> None:
        self.assertEqual(
            self._selected(".github/workflows/ci.yml"),
            set(AREAS),
        )

    def test_mixed_diff_unions_areas_without_losing_flags(self) -> None:
        self.assertEqual(
            self._selected(
                "backend/app/services/harbor_client.py",
                "frontend/src/App.vue",
                "docs/user-guide.md",
                "deploy/smoke-compose.sh",
            ),
            {"backend", "frontend", "compose", "docs"},
        )

    def test_missing_optional_component_does_not_create_false_job(self) -> None:
        (self.root / "frontend/package.json").unlink()
        self.assertEqual(
            self._selected("frontend/src/App.vue"),
            set(),
        )

    def test_workflow_self_test_respects_missing_components(self) -> None:
        (self.root / "frontend/package.json").unlink()
        result = self._selected(".github/workflows/ci.yml")
        self.assertEqual(result, {"backend", "protocol", "compose", "docs"})


if __name__ == "__main__":
    unittest.main()
