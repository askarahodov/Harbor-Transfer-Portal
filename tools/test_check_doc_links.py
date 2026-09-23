from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.check_doc_links import check_current_contract_claims, check_file


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


    def write_current_guides(self) -> None:
        self.write(
            "docs/user-guide.md",
            "\n".join(
                (
                    "Terminal History остаётся историческим/read-only",
                    "owner может открыть/продолжить тот же workflow либо запросить штатную отмену",
                    "остаётся полностью read-only",
                )
            ),
        )
        self.write(
            "docs/admin-guide.md",
            "\n".join(
                (
                    "installation-wide active selector не является registry authority",
                    "immutable snapshot `harbor_profile_id/name/url`",
                    "**Legacy fallback Harbor**",
                    "Переключение fallback не блокируется новыми pinned operations",
                    "Mutation safety относится к самому operation-bound profile",
                )
            ),
        )

    def test_current_contract_claims_accept_current_guides(self) -> None:
        self.write_current_guides()
        self.assertEqual(check_current_contract_claims(self.root), [])

    def test_current_contract_claims_reject_stale_history_statement(self) -> None:
        self.write_current_guides()
        source = self.root / "docs/user-guide.md"
        source.write_text(
            source.read_text(encoding="utf-8")
            + "\nHistory — read-only экран. Из него нельзя менять policy, перезапускать или отменять operation.\n",
            encoding="utf-8",
        )

        errors = check_current_contract_claims(self.root)
        self.assertEqual(len(errors), 1)
        self.assertIn("устаревшее утверждение", errors[0].reason)
        self.assertGreater(errors[0].line, 1)

    def test_current_contract_claims_reject_stale_active_harbor_authority(self) -> None:
        self.write_current_guides()
        source = self.root / "docs/admin-guide.md"
        source.write_text(
            source.read_text(encoding="utf-8")
            + "\nИменно его используют Harbor browse API, SOURCE export, TARGET destination validation, Skopeo и Helm.\n",
            encoding="utf-8",
        )

        errors = check_current_contract_claims(self.root)
        self.assertEqual(len(errors), 1)
        self.assertIn("current contract", errors[0].reason)



if __name__ == "__main__":
    unittest.main()
