from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.check_dependency_locks import check_repository


class DependencyLockCheckTests(unittest.TestCase):
    def _repository(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "backend").mkdir()
        (root / "frontend").mkdir()
        (root / "backend" / "pyproject.toml").write_text(
            """[project]\n"
            "name = \"sample-backend\"\n"
            "dependencies = [\"fastapi>=0.1,<1\", \"uvicorn[standard]>=0.1,<1\"]\n"
            "[project.optional-dependencies]\n"
            "dev = [\"pytest>=8,<9\"]\n"
            """,
            encoding="utf-8",
        )
        package = {
            "name": "sample-frontend",
            "version": "1.0.0",
            "dependencies": {"vue": "^3.5.0"},
            "devDependencies": {"vitest": "^2.1.0"},
        }
        (root / "frontend" / "package.json").write_text(
            json.dumps(package), encoding="utf-8"
        )
        lock = {
            "name": package["name"],
            "version": package["version"],
            "lockfileVersion": 3,
            "packages": {
                "": package,
                "node_modules/vue": {
                    "version": "3.5.0",
                    "resolved": "https://registry.npmjs.org/vue/-/vue-3.5.0.tgz",
                    "integrity": "sha512-test",
                },
                "node_modules/vitest": {
                    "version": "2.1.0",
                    "resolved": "https://registry.npmjs.org/vitest/-/vitest-2.1.0.tgz",
                    "integrity": "sha512-test",
                },
            },
        }
        (root / "frontend" / "package-lock.json").write_text(
            json.dumps(lock), encoding="utf-8"
        )
        (root / "backend" / "requirements-runtime.lock").write_text(
            "fastapi==0.9\nhatchling==1.0\nuvicorn==0.9\n", encoding="utf-8"
        )
        (root / "backend" / "requirements-dev.lock").write_text(
            "fastapi==0.9\nhatchling==1.0\npytest==8.4\nuvicorn==0.9\n",
            encoding="utf-8",
        )
        return temporary, root

    def test_valid_repository_locks_pass(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)
        self.assertEqual(check_repository(root), [])

    def test_frontend_root_metadata_drift_is_rejected(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)
        lock_path = root / "frontend" / "package-lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["packages"][""]["dependencies"]["vue"] = "^2.0.0"
        lock_path.write_text(json.dumps(lock), encoding="utf-8")

        errors = check_repository(root)

        self.assertTrue(any("dependencies" in error for error in errors), errors)

    def test_missing_backend_top_level_pin_is_rejected(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)
        (root / "backend" / "requirements-runtime.lock").write_text(
            "hatchling==1.0\nuvicorn==0.9\n", encoding="utf-8"
        )

        errors = check_repository(root)

        self.assertIn("backend runtime lock is missing top-level package fastapi", errors)

    def test_runtime_and_dev_common_versions_must_match(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)
        (root / "backend" / "requirements-dev.lock").write_text(
            "fastapi==0.8\nhatchling==1.0\npytest==8.4\nuvicorn==0.9\n",
            encoding="utf-8",
        )

        errors = check_repository(root)

        self.assertIn(
            "backend lock mismatch for fastapi: runtime=0.9, dev=0.8",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
