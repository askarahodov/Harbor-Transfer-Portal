from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.check_design_tokens import check_repository, contrast_ratio


VALID_TOKENS = """:root {
  --color-deep-harbor: #0B1E3A;
  --color-cloud-white: #FFFFFF;
  --color-fog-gray: #F1F5F9;
  --color-steel: #475569;
  --color-mist: #E2E8F0;
  --color-mint: #D1FAE5;
  --color-sand: #FEF3C7;
  --color-rose: #FEE2E2;
  --color-sky: #DBEAFE;
  --color-surface: var(--color-cloud-white);
  --color-background: var(--color-fog-gray);
  --color-border-control: #64748B;
  --color-text: var(--color-deep-harbor);
  --color-text-muted: var(--color-steel);
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

@media (prefers-color-scheme: dark) {
  :root {
    --color-surface: #111827;
    --color-background: #0F172A;
    --color-border-control: #94A3B8;
    --color-text: #F8FAFC;
    --color-text-muted: #CBD5E1;
    --color-focus-ring: #60A5FA;
    --color-success-text: #A7F3D0;
    --color-success-surface: #064E3B;
    --color-warning-text: #FDE68A;
    --color-warning-surface: #78350F;
    --color-danger-text: #FECACA;
    --color-danger-surface: #7F1D1D;
    --color-info-text: #BFDBFE;
    --color-info-surface: #1E3A8A;
  }
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
            "<template><p>ok</p></template>\n<style scoped>.x { color: var(--color-text); }</style>\n",
            encoding="utf-8",
        )
        return temporary, root

    def test_known_light_and_dark_pairs_meet_expected_ratios(self) -> None:
        self.assertGreaterEqual(contrast_ratio("#065F46", "#D1FAE5"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#92400E", "#FEF3C7"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#991B1B", "#FEE2E2"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#1E40AF", "#DBEAFE"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#F8FAFC", "#111827"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#CBD5E1", "#0F172A"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#60A5FA", "#111827"), 3.0)
        self.assertGreaterEqual(contrast_ratio("#A7F3D0", "#064E3B"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#FDE68A", "#78350F"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#FECACA", "#7F1D1D"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#BFDBFE", "#1E3A8A"), 4.5)

    def test_valid_repository_passes_both_themes(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)

        errors, rows = check_repository(root)

        self.assertEqual(errors, [])
        self.assertTrue(any(row.startswith("light:") for row in rows))
        self.assertTrue(any(row.startswith("dark:") for row in rows))
        self.assertTrue(any("--color-text / --color-surface" in row for row in rows))
        self.assertTrue(any("--color-focus-ring" in row for row in rows))

    def test_missing_dark_theme_fails(self) -> None:
        temporary, root = self._repository(VALID_TOKENS.split("@media", 1)[0])
        self.addCleanup(temporary.cleanup)

        errors, _ = check_repository(root)

        self.assertTrue(any("prefers-color-scheme: dark" in error for error in errors))

    def test_low_contrast_dark_status_pair_fails(self) -> None:
        temporary, root = self._repository(
            VALID_TOKENS.replace("--color-danger-text: #FECACA;", "--color-danger-text: #7F1D1D;")
        )
        self.addCleanup(temporary.cleanup)

        errors, _ = check_repository(root)

        self.assertTrue(
            any(
                error.startswith("dark:") and "--color-danger-text / --color-danger-surface" in error
                for error in errors
            )
        )

    def test_low_contrast_control_border_fails(self) -> None:
        temporary, root = self._repository(
            VALID_TOKENS.replace("--color-border-control: #64748B;", "--color-border-control: #E2E8F0;", 1)
        )
        self.addCleanup(temporary.cleanup)

        errors, _ = check_repository(root)

        self.assertTrue(
            any(
                error.startswith("light:") and "--color-border-control / --color-surface" in error
                for error in errors
            )
        )

    def test_raw_hex_in_view_is_rejected(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)
        (root / "frontend/src/views/TestView.vue").write_text(
            "<style scoped>.x { color: #991B1B; }</style>\n",
            encoding="utf-8",
        )

        errors, _ = check_repository(root)

        self.assertTrue(any("raw hex #991B1B" in error for error in errors))

    def test_direct_neutral_palette_token_in_view_is_rejected(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)
        (root / "frontend/src/views/TestView.vue").write_text(
            "<style scoped>.x { color: var(--color-steel); }</style>\n",
            encoding="utf-8",
        )

        errors, _ = check_repository(root)

        self.assertTrue(any("direct palette token --color-steel" in error for error in errors))

    def test_explicit_brand_palette_exception_is_allowed(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)
        (root / "frontend/src/components/Brand.vue").write_text(
            "<style scoped>.brand { background: var(--color-deep-harbor); } /* palette-ok: brand */</style>\n",
            encoding="utf-8",
        )

        errors, _ = check_repository(root)

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
