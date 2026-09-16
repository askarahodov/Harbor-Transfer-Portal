from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.check_design_tokens import check_repository, contrast_ratio


VALID_TOKENS = """:root {
  --color-cloud-white: #FFFFFF;
  --color-fog-gray: #F1F5F9;
  --color-deep-harbor: #0B1E3A;
  --color-bridge-blue: #2563EB;
  --color-mint: #D1FAE5;
  --color-sand: #FEF3C7;
  --color-rose: #FEE2E2;
  --color-sky: #DBEAFE;
  --color-surface: var(--color-cloud-white);
  --color-background: var(--color-fog-gray);
  --color-border-control: #64748B;
  --color-text: var(--color-deep-harbor);
  --color-text-muted: #475569;
  --color-action: var(--color-bridge-blue);
  --color-action-surface: var(--color-bridge-blue);
  --color-on-accent: #FFFFFF;
  --color-brand-surface: var(--color-deep-harbor);
  --color-brand-text-muted: #C4C9D0;
  --color-brand-hover-surface: #102C5D;
  --color-focus-ring: var(--color-bridge-blue);
  --color-success-text: #065F46;
  --color-success-surface: var(--color-mint);
  --color-warning-text: #92400E;
  --color-warning-surface: var(--color-sand);
  --color-danger-text: #991B1B;
  --color-danger-surface: var(--color-rose);
  --color-danger-action-surface: #B91C1C;
  --color-on-danger-action: #FFFFFF;
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
    --color-action: #60A5FA;
    --color-action-surface: #1D4ED8;
    --color-on-accent: #FFFFFF;
    --color-brand-surface: #0B1E3A;
    --color-brand-text-muted: #C4C9D0;
    --color-brand-hover-surface: #102C5D;
    --color-focus-ring: #60A5FA;
    --color-success-text: #D1FAE5;
    --color-success-surface: #064E3B;
    --color-warning-text: #FEF3C7;
    --color-warning-surface: #78350F;
    --color-danger-text: #FEE2E2;
    --color-danger-surface: #7F1D1D;
    --color-danger-action-surface: #991B1B;
    --color-on-danger-action: #FFFFFF;
    --color-info-text: #DBEAFE;
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
            "<template><p>ok</p></template>\n"
            "<style scoped>.x { color: var(--color-danger-text); "
            "background: var(--color-surface); }</style>\n",
            encoding="utf-8",
        )
        return temporary, root

    def test_known_light_and_dark_pairs_meet_expected_ratios(self) -> None:
        self.assertGreaterEqual(contrast_ratio("#0B1E3A", "#FFFFFF"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#F8FAFC", "#111827"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#FFFFFF", "#1D4ED8"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#D1FAE5", "#064E3B"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#FFFFFF", "#B91C1C"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#FFFFFF", "#991B1B"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#C4C9D0", "#0B1E3A"), 4.5)
        self.assertGreaterEqual(contrast_ratio("#FFFFFF", "#102C5D"), 4.5)

    def test_valid_repository_checks_both_themes(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)

        errors, rows = check_repository(root)

        self.assertEqual(errors, [])
        self.assertTrue(any(row.startswith("[light]") for row in rows))
        self.assertTrue(any(row.startswith("[dark]") for row in rows))
        self.assertTrue(
            any("--color-on-danger-action / --color-danger-action-surface" in row for row in rows)
        )
        self.assertTrue(
            any("--color-brand-text-muted / --color-brand-surface" in row for row in rows)
        )

    def test_low_contrast_dark_text_fails(self) -> None:
        temporary, root = self._repository(
            VALID_TOKENS.replace("--color-text: #F8FAFC;", "--color-text: #111827;")
        )
        self.addCleanup(temporary.cleanup)

        errors, _ = check_repository(root)

        self.assertTrue(
            any("[dark] contrast --color-text / --color-surface" in error for error in errors)
        )

    def test_low_contrast_dark_danger_action_fails(self) -> None:
        temporary, root = self._repository(
            VALID_TOKENS.replace(
                "--color-danger-action-surface: #991B1B;",
                "--color-danger-action-surface: #FEE2E2;",
            )
        )
        self.addCleanup(temporary.cleanup)

        errors, _ = check_repository(root)

        self.assertTrue(
            any(
                "[dark] contrast --color-on-danger-action / --color-danger-action-surface"
                in error
                for error in errors
            )
        )

    def test_palette_reference_in_vue_is_rejected(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)
        (root / "frontend/src/views/TestView.vue").write_text(
            "<style scoped>.x { color: var(--color-deep-harbor); }</style>\n",
            encoding="utf-8",
        )

        errors, _ = check_repository(root)

        self.assertTrue(any("palette token --color-deep-harbor" in error for error in errors))

    def test_undefined_color_token_is_rejected(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)
        (root / "frontend/src/views/TestView.vue").write_text(
            "<style scoped>.x { background: var(--color-cloud); }</style>\n",
            encoding="utf-8",
        )

        errors, _ = check_repository(root)

        self.assertTrue(any("undefined color token --color-cloud" in error for error in errors))

    def test_raw_hex_in_view_is_rejected(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)
        (root / "frontend/src/views/TestView.vue").write_text(
            "<style scoped>.x { color: #991B1B; }</style>\n",
            encoding="utf-8",
        )

        errors, _ = check_repository(root)

        self.assertTrue(any("raw hex #991B1B" in error for error in errors))

    def test_raw_functional_color_in_vue_is_rejected(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)
        (root / "frontend/src/views/TestView.vue").write_text(
            "<style scoped>.x { background: rgba(37, 99, 235, .2); }</style>\n",
            encoding="utf-8",
        )

        errors, _ = check_repository(root)

        self.assertTrue(any("raw rgb/hsl functional color" in error for error in errors))

    def test_named_white_or_black_declaration_is_rejected(self) -> None:
        temporary, root = self._repository()
        self.addCleanup(temporary.cleanup)
        (root / "frontend/src/views/TestView.vue").write_text(
            "<style scoped>.x { background: white; color: black; }</style>\n",
            encoding="utf-8",
        )

        errors, _ = check_repository(root)

        self.assertTrue(any("named white/black color declaration" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
