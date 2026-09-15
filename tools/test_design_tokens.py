from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.check_design_tokens import audit, contrast_ratio, resolve_color


VALID_TOKENS = """:root {
  --color-surface: #FFFFFF;
  --color-background: #F1F5F9;
  --color-mint: #D1FAE5;
  --color-sand: #FEF3C7;
  --color-rose: #FEE2E2;
  --color-sky: #DBEAFE;
  --color-success-text: #065F46;
  --color-success-surface: var(--color-mint);
  --color-warning-text: #92400E;
  --color-warning-surface: var(--color-sand);
  --color-danger-text: #991B1B;
  --color-danger-surface: var(--color-rose);
  --color-info-text: #1E40AF;
  --color-info-surface: var(--color-sky);
  --color-border-control: #64748B;
  --color-focus-ring: #2563EB;
}
"""


class DesignTokenCheckerTests(unittest.TestCase):
    def _root(self, tokens: str = VALID_TOKENS, vue: str = "") -> Path:
        directory = Path(tempfile.mkdtemp())
        token_path = directory / "frontend/src/styles/tokens.css"
        token_path.parent.mkdir(parents=True)
        token_path.write_text(tokens, encoding="utf-8")
        view_path = directory / "frontend/src/views/TestView.vue"
        view_path.parent.mkdir(parents=True)
        view_path.write_text(vue, encoding="utf-8")
        (directory / "frontend/src/components").mkdir(parents=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(directory))
        return directory

    def test_current_semantic_pairs_pass(self) -> None:
        results, violations = audit(self._root())
        self.assertEqual(violations, [])
        self.assertGreaterEqual(len(results), 10)

    def test_low_contrast_status_pair_fails(self) -> None:
        tokens = VALID_TOKENS.replace("#92400E", "#F59E0B")
        _, violations = audit(self._root(tokens))
        self.assertTrue(any("warning text/surface" in item for item in violations))

    def test_low_contrast_control_border_fails(self) -> None:
        tokens = VALID_TOKENS.replace("#64748B", "#E2E8F0")
        _, violations = audit(self._root(tokens))
        self.assertTrue(any("control border/surface" in item for item in violations))

    def test_raw_hex_in_vue_fails(self) -> None:
        _, violations = audit(self._root(vue="<style>.error { color: #B91C1C; }</style>\n"))
        self.assertTrue(any("raw color #B91C1C" in item for item in violations))

    def test_var_aliases_resolve(self) -> None:
        tokens = {
            "--base": "#FFFFFF",
            "--alias": "var(--base)",
        }
        self.assertEqual(resolve_color(tokens, "--alias"), "#FFFFFF")

    def test_wcag_reference_ratio(self) -> None:
        self.assertAlmostEqual(contrast_ratio("#000000", "#FFFFFF"), 21.0, places=6)


if __name__ == "__main__":
    unittest.main()
