from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.check_doc_links import check_file


class CheckDocLinksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "docs").mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write(self, relative: str, content: str = "") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_valid_relative_file_link(self) -> None:
        self.write("docs/target.md", "# Target\n")
        source = self.write("docs/source.md", "[Target](target.md)\n")
        self.assertEqual(check_file(self.root, source), [])

    def test_missing_relative_file_fails(self) -> None:
        source = self.write("docs/source.md", "[Missing](missing.md)\n")
        errors = check_file(self.root, source)
        self.assertEqual(len(errors), 1)
        self.assertIn("не существует", errors[0].reason)

    def test_external_and_anchor_links_are_ignored(self) -> None:
        source = self.write(
            "docs/source.md",
            "[External](https://example.com/docs) [Anchor](#section)\n",
        )
        self.assertEqual(check_file(self.root, source), [])

    def test_docsify_sidebar_allows_site_root_markdown_target(self) -> None:
        self.write("docs/dashboard.md", "# Dashboard\n")
        sidebar = self.write("docs/_sidebar.md", "[Dashboard](/docs/dashboard.md)\n")
        self.assertEqual(check_file(self.root, sidebar), [])

    def test_site_root_markdown_target_remains_invalid_outside_sidebar(self) -> None:
        self.write("docs/dashboard.md", "# Dashboard\n")
        source = self.write("docs/source.md", "[Dashboard](/docs/dashboard.md)\n")
        errors = check_file(self.root, source)
        self.assertEqual(len(errors), 1)
        self.assertIn("absolute path", errors[0].reason)

    def test_path_escape_fails(self) -> None:
        source = self.write("docs/source.md", "[Outside](../../outside.md)\n")
        errors = check_file(self.root, source)
        self.assertEqual(len(errors), 1)
        self.assertIn("корень репозитория", errors[0].reason)

    def test_fenced_code_is_not_interpreted_as_document_link(self) -> None:
        source = self.write(
            "docs/source.md",
            "```markdown\n[Example](not-a-real-file.md)\n```\n",
        )
        self.assertEqual(check_file(self.root, source), [])

    def test_image_path_is_checked(self) -> None:
        self.write("docs/img/logo.png", "not-binary-for-test")
        source = self.write("docs/source.md", "![Logo](img/logo.png)\n")
        self.assertEqual(check_file(self.root, source), [])


if __name__ == "__main__":
    unittest.main()
