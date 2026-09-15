from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.check_design_tokens import check_repository, contrast_ratio


VALID_TOKENS = """:root {
  --color-cloud-white: #FFFFFF;
  --color-fog-gray: #F1F5F9;
  --color-mint: #D1FAE5;
  --color-sand: #FEF3C7;
  --color-rose: #FEE2E2;
  --color-sky: #DBEAFE;
  --color-surface: var(--color-cloud-white);
  --color-background: var(--color-fog-gray);
  --color-border-control: #64748B;
  --color-focus-ring: #2563EB;
  --color-success-text: #065F46;
  --color-success-surface: var(--color-mint);
  --color-warning-text: #92400E;
  --color-warning-surface: var(--color-sand);
  --color-danger-text: #991B1B;
  --color-danger-surface: var(--color-rose);
  --color-info-text: #1E40AF;
  --color-info-surface: var(--color-sky);
}
"""


class DesignTokenCheckerTests(unittest.TestCase):
    def _repository(self, tokens: str = VALID_TOKENS) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "frontend/src/styles").mkdir(parents=True)
        (root / "frontend/src/views").mkdir(parents=True)
        (root / "frontend/src/components").mkdir(parents=True)
        (root / "frontend/src/styles/tokens.css").write_text(tokens, encoding="utf-8")
        (root / "frontend/src/views/TestView.vue").write_text(
            "<template><p>ok</p></template>\n<style scoped>.x { color: var(--color-danger-text); }</style>\n",
            encoding="utf-8",
        )
        return temporary, root

    def test_known_status_pairs_meet_expected_ratios(self) -> None:
        self.assertGreaterEqual(contrast_ratio("#065F46", "#D1FAE5"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#92400E", "#FEF3C7"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#991B1B", "#FEE2E2"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#1E40AF", "#DBEAFE"), 4.5)

    def test_valid_repository_passes(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)

        errors, rows = check_repository(root)

        self.assertEqual(errors, [])
        self.assertTrue(any("--color-border-control" in row for row in rows))
        self.assertTrue(any("--color-focus-ring" in row for row in rows))

    def test_low_contrast_status_pair_fails(self) -> None:
        temporary, root = self._repository(
            VALID_TOKENS.replace("--color-danger-text: #991B1B;", "--color-danger-text: #EF4444;")
        )
        self.addCleanup(temporary.cleanup)

        errors, _ = check_repository(root)

        self.assertTrue(any("--color-danger-text / --color-danger-surface" in error for error in errors))

    def test_low_contrast_control_border_fails(self) -> None:
        temporary, root = self._repository(
            VALID_TOKENS.replace("--color-border-control: #64748B;", "--color-border-control: #E2E8F0;")
        )
        self.addCleanup(temporary.cleanup)

        errors, _ = check_repository(root)

        self.assertTrue(any("--color-border-control / --color-surface" in error for error in errors))

    def test_raw_hex_in_view_is_rejected(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)
        (root / "frontend/src/views/TestView.vue").write_text(
            "<style scoped>.x { color: #991B1B; }</style>\n",
            encoding="utf-8",
        )

        errors, _ = check_repository(root)

        self.assertTrue(any("raw hex #991B1B" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
