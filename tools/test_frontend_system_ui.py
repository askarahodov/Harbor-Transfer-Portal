from __future__ import annotations

import unittest
from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class FrontendSystemUiPolicyTests(unittest.TestCase):
    def test_document_branding_has_plain_title_and_favicon(self) -> None:
        html = (_REPOSITORY_ROOT / "frontend/index.html").read_text(encoding="utf-8")

        self.assertIn("<title>Harbor Transfer Portal</title>", html)
        self.assertNotIn("🐴", html)
        self.assertIn('rel="icon"', html)
        self.assertIn('href="/favicon.svg"', html)
        self.assertTrue((_REPOSITORY_ROOT / "frontend/public/favicon.svg").is_file())

    def test_reduced_motion_is_global(self) -> None:
        css = (_REPOSITORY_ROOT / "frontend/src/styles/base.css").read_text(encoding="utf-8")

        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn("animation-duration: 0.01ms !important;", css)
        self.assertIn("animation-iteration-count: 1 !important;", css)
        self.assertIn("transition-duration: 0.01ms !important;", css)
        self.assertIn("scroll-behavior: auto !important;", css)

    def test_system_light_dark_theme_policy_is_explicit(self) -> None:
        base_css = (_REPOSITORY_ROOT / "frontend/src/styles/base.css").read_text(encoding="utf-8")
        tokens_css = (_REPOSITORY_ROOT / "frontend/src/styles/tokens.css").read_text(encoding="utf-8")
        decisions = (_REPOSITORY_ROOT / "docs/decisions.md").read_text(encoding="utf-8")

        self.assertIn("html { color-scheme: light dark; }", base_css)
        self.assertIn("@media (prefers-color-scheme: dark)", tokens_css)
        self.assertIn("Theme policy — system light/dark", decisions)
        self.assertNotIn("Theme policy — light-only", decisions)

    def test_destructive_buttons_use_dedicated_action_pair(self) -> None:
        base_css = (_REPOSITORY_ROOT / "frontend/src/styles/base.css").read_text(encoding="utf-8")
        tokens_css = (_REPOSITORY_ROOT / "frontend/src/styles/tokens.css").read_text(encoding="utf-8")

        self.assertIn("--color-danger-action-surface:", tokens_css)
        self.assertIn("--color-on-danger-action:", tokens_css)
        self.assertIn(".danger-button,\n.button--danger", base_css)
        self.assertIn("--color-danger-text: var(--color-danger-action-surface);", base_css)
        self.assertIn("--color-on-accent: var(--color-on-danger-action);", base_css)


if __name__ == "__main__":
    unittest.main()
