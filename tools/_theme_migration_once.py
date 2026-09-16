from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TOKENS = ':root {\n  /* Immutable palette primitives. Components consume semantic aliases below. */\n  --color-deep-harbor: #0B1E3A;\n  --color-bridge-blue: #2563EB;\n  --color-transfer-green: #10B981;\n  --color-alert-amber: #F59E0B;\n  --color-stop-red: #EF4444;\n  --color-cloud-white: #FFFFFF;\n  --color-fog-gray: #F1F5F9;\n  --color-steel: #475569;\n  --color-mist: #E2E8F0;\n  --color-sky: #DBEAFE;\n  --color-mint: #D1FAE5;\n  --color-sand: #FEF3C7;\n  --color-rose: #FEE2E2;\n\n  /* Semantic theme aliases — light mode. */\n  --color-background: var(--color-fog-gray);\n  --color-surface: var(--color-cloud-white);\n  --color-surface-subtle: var(--color-fog-gray);\n  --color-border: var(--color-mist);\n  --color-border-control: #64748B;\n  --color-text: var(--color-deep-harbor);\n  --color-text-muted: var(--color-steel);\n  --color-action: var(--color-bridge-blue);\n  --color-action-surface: var(--color-bridge-blue);\n  --color-on-accent: var(--color-cloud-white);\n  --color-brand-surface: var(--color-deep-harbor);\n  --color-positive-accent: var(--color-transfer-green);\n  --color-warning-accent: var(--color-alert-amber);\n  --color-negative-accent: var(--color-stop-red);\n\n  --color-success-text: #065F46;\n  --color-success-surface: var(--color-mint);\n  --color-warning-text: #92400E;\n  --color-warning-surface: var(--color-sand);\n  --color-danger-text: #991B1B;\n  --color-danger-surface: var(--color-rose);\n  --color-info-text: #1E40AF;\n  --color-info-surface: var(--color-sky);\n  --color-danger: var(--color-danger-text);\n\n  --color-focus-ring: var(--color-action);\n  --focus-ring-width: 3px;\n  --focus-ring-offset: 2px;\n\n  --space-1: 4px;\n  --space-2: 8px;\n  --space-3: 12px;\n  --space-4: 16px;\n  --space-5: 20px;\n  --space-6: 24px;\n  --space-8: 32px;\n  --space-12: 48px;\n\n  --radius-sm: 4px;\n  --radius-md: 8px;\n  --radius-lg: 12px;\n  --radius-full: 999px;\n\n  --shadow-sm: 0 1px 2px rgba(11, 30, 58, 0.06);\n  --shadow-md: 0 4px 12px rgba(11, 30, 58, 0.08);\n  --shadow-lg: 0 12px 32px rgba(11, 30, 58, 0.12);\n\n  --layout-sidebar: 240px;\n  --layout-sidebar-collapsed: 64px;\n  --layout-header: 64px;\n  --layout-content-max: 1200px;\n\n  /* Element Plus follows the same semantic theme as custom controls. */\n  --el-color-primary: var(--color-action);\n  --el-color-success: var(--color-success-text);\n  --el-color-warning: var(--color-warning-text);\n  --el-color-danger: var(--color-danger-text);\n  --el-color-info: var(--color-info-text);\n  --el-text-color-primary: var(--color-text);\n  --el-text-color-regular: var(--color-text-muted);\n  --el-text-color-secondary: var(--color-text-muted);\n  --el-bg-color: var(--color-surface);\n  --el-bg-color-overlay: var(--color-surface);\n  --el-fill-color: var(--color-surface-subtle);\n  --el-fill-color-light: var(--color-surface-subtle);\n  --el-fill-color-blank: var(--color-surface);\n  --el-border-color: var(--color-border-control);\n  --el-border-color-light: var(--color-border);\n  --el-border-color-lighter: var(--color-border);\n  --el-border-radius-base: var(--radius-md);\n  --el-border-radius-small: var(--radius-sm);\n  --el-border-radius-round: var(--radius-full);\n  --el-button-outline-color: var(--color-focus-ring);\n}\n\n@media (prefers-color-scheme: dark) {\n  :root {\n    /* Palette primitives never change; only semantic aliases switch theme. */\n    --color-background: #0F172A;\n    --color-surface: #111827;\n    --color-surface-subtle: #1E293B;\n    --color-border: #334155;\n    --color-border-control: #94A3B8;\n    --color-text: #F8FAFC;\n    --color-text-muted: #CBD5E1;\n    --color-action: #60A5FA;\n    --color-action-surface: #1D4ED8;\n    --color-on-accent: #FFFFFF;\n    --color-brand-surface: #0B1E3A;\n    --color-positive-accent: #34D399;\n    --color-warning-accent: #FBBF24;\n    --color-negative-accent: #F87171;\n\n    --color-success-text: #D1FAE5;\n    --color-success-surface: #064E3B;\n    --color-warning-text: #FEF3C7;\n    --color-warning-surface: #78350F;\n    --color-danger-text: #FEE2E2;\n    --color-danger-surface: #7F1D1D;\n    --color-info-text: #DBEAFE;\n    --color-info-surface: #1E3A8A;\n    --color-focus-ring: #60A5FA;\n\n    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.28);\n    --shadow-md: 0 4px 12px rgba(0, 0, 0, 0.32);\n    --shadow-lg: 0 12px 32px rgba(0, 0, 0, 0.38);\n  }\n}\n'
CHECKER = '#!/usr/bin/env python3\n"""Validate semantic design-token use and WCAG contrast in both color schemes."""\n\nfrom __future__ import annotations\n\nimport re\nimport sys\nfrom pathlib import Path\n\n_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]\n_TOKEN_RE = re.compile(r"(--[A-Za-z0-9_-]+)\\s*:\\s*([^;]+);")\n_HEX_RE = re.compile(r"#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?(?:[0-9A-Fa-f]{2})?\\b")\n_VAR_RE = re.compile(r"var\\((--[A-Za-z0-9_-]+)\\)")\n_COLOR_VAR_REF_RE = re.compile(r"var\\((--color-[A-Za-z0-9_-]+)(?:\\s*,[^)]*)?\\)")\n_NAMED_COLOR_DECL_RE = re.compile(\n    r"\\b(?:background(?:-color)?|color)\\s*:\\s*(?:white|black)\\b",\n    re.IGNORECASE,\n)\n\n_PALETTE_TOKENS = frozenset({\n    "--color-deep-harbor", "--color-bridge-blue", "--color-transfer-green",\n    "--color-alert-amber", "--color-stop-red", "--color-cloud-white",\n    "--color-fog-gray", "--color-steel", "--color-mist", "--color-sky",\n    "--color-mint", "--color-sand", "--color-rose",\n})\n\n_TEXT_PAIRS = (\n    ("--color-text", "--color-surface", 4.5),\n    ("--color-text", "--color-background", 4.5),\n    ("--color-text-muted", "--color-surface", 4.5),\n    ("--color-text-muted", "--color-background", 4.5),\n    ("--color-action", "--color-surface", 4.5),\n    ("--color-action", "--color-background", 4.5),\n    ("--color-on-accent", "--color-action-surface", 4.5),\n    ("--color-on-accent", "--color-brand-surface", 4.5),\n    ("--color-success-text", "--color-success-surface", 4.5),\n    ("--color-warning-text", "--color-warning-surface", 4.5),\n    ("--color-danger-text", "--color-danger-surface", 4.5),\n    ("--color-info-text", "--color-info-surface", 4.5),\n)\n_NON_TEXT_PAIRS = (\n    ("--color-border-control", "--color-surface", 3.0),\n    ("--color-border-control", "--color-background", 3.0),\n    ("--color-focus-ring", "--color-surface", 3.0),\n    ("--color-focus-ring", "--color-background", 3.0),\n)\n\n\ndef _hex_to_rgb(value: str) -> tuple[int, int, int]:\n    value = value.lstrip("#")\n    if len(value) == 3:\n        value = "".join(character * 2 for character in value)\n    if len(value) != 6:\n        raise ValueError(f"unsupported color value: #{value}")\n    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]\n\n\ndef _channel_luminance(channel: int) -> float:\n    normalized = channel / 255\n    if normalized <= 0.04045:\n        return normalized / 12.92\n    return ((normalized + 0.055) / 1.055) ** 2.4\n\n\ndef _luminance(rgb: tuple[int, int, int]) -> float:\n    red, green, blue = (_channel_luminance(channel) for channel in rgb)\n    return 0.2126 * red + 0.7152 * green + 0.0722 * blue\n\n\ndef contrast_ratio(first: str, second: str) -> float:\n    light = max(_luminance(_hex_to_rgb(first)), _luminance(_hex_to_rgb(second)))\n    dark = min(_luminance(_hex_to_rgb(first)), _luminance(_hex_to_rgb(second)))\n    return (light + 0.05) / (dark + 0.05)\n\n\ndef _block_after(source: str, marker: str, start: int = 0) -> str:\n    marker_index = source.find(marker, start)\n    if marker_index < 0:\n        raise ValueError(f"missing CSS block: {marker}")\n    opening = source.find("{", marker_index)\n    if opening < 0:\n        raise ValueError(f"missing opening brace after: {marker}")\n    depth = 0\n    for index in range(opening, len(source)):\n        if source[index] == "{":\n            depth += 1\n        elif source[index] == "}":\n            depth -= 1\n            if depth == 0:\n                return source[opening + 1:index]\n    raise ValueError(f"unclosed CSS block: {marker}")\n\n\ndef _parse_token_block(block: str) -> dict[str, str]:\n    return {name: raw_value.strip() for name, raw_value in _TOKEN_RE.findall(block)}\n\n\ndef _parse_themes(path: Path) -> dict[str, dict[str, str]]:\n    source = path.read_text(encoding="utf-8")\n    light = _parse_token_block(_block_after(source, ":root"))\n    dark_marker = "@media (prefers-color-scheme: dark)"\n    dark_start = source.find(dark_marker)\n    if dark_start < 0:\n        raise ValueError(f"missing CSS block: {dark_marker}")\n    dark_overrides = _parse_token_block(_block_after(source, ":root", dark_start))\n    return {"light": light, "dark": {**light, **dark_overrides}}\n\n\ndef _resolve_color(name: str, tokens: dict[str, str], stack: tuple[str, ...] = ()) -> str:\n    if name in stack:\n        raise ValueError(f"cyclic token reference: {\' -> \'.join((*stack, name))}")\n    raw = tokens.get(name)\n    if raw is None:\n        raise ValueError(f"missing token: {name}")\n    if re.fullmatch(r"#[0-9A-Fa-f]{6}", raw):\n        return raw.upper()\n    variable = _VAR_RE.fullmatch(raw)\n    if variable is not None:\n        return _resolve_color(variable.group(1), tokens, (*stack, name))\n    raise ValueError(f"token {name} is not a resolvable opaque hex color: {raw}")\n\n\ndef _contrast_errors(themes: dict[str, dict[str, str]]) -> tuple[list[str], list[str]]:\n    errors: list[str] = []\n    rows: list[str] = []\n    for theme_name, tokens in themes.items():\n        for foreground_name, background_name, threshold in (*_TEXT_PAIRS, *_NON_TEXT_PAIRS):\n            try:\n                foreground = _resolve_color(foreground_name, tokens)\n                background = _resolve_color(background_name, tokens)\n            except ValueError as exc:\n                errors.append(f"[{theme_name}] {exc}")\n                continue\n            ratio = contrast_ratio(foreground, background)\n            status = "PASS" if ratio >= threshold else "FAIL"\n            rows.append(\n                f"[{theme_name}] {foreground_name} / {background_name}: {ratio:.2f}:1 "\n                f"(threshold {threshold:.1f}:1) {status}"\n            )\n            if ratio < threshold:\n                errors.append(\n                    f"[{theme_name}] contrast {foreground_name} / {background_name} = "\n                    f"{ratio:.2f}:1 below {threshold:.1f}:1"\n                )\n    return errors, rows\n\n\ndef _frontend_style_errors(root: Path, defined_colors: set[str]) -> list[str]:\n    errors: list[str] = []\n    for directory in (root / "frontend/src/views", root / "frontend/src/components"):\n        if not directory.is_dir():\n            errors.append(f"missing frontend directory: {directory.relative_to(root)}")\n            continue\n        for path in sorted(directory.rglob("*.vue")):\n            relative = path.relative_to(root)\n            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):\n                for color in _HEX_RE.findall(line):\n                    errors.append(f"{relative}:{line_number}: raw hex {color}; use a design token")\n                if _NAMED_COLOR_DECL_RE.search(line):\n                    errors.append(\n                        f"{relative}:{line_number}: named white/black color declaration; use a semantic token"\n                    )\n                for token in _COLOR_VAR_REF_RE.findall(line):\n                    if token in _PALETTE_TOKENS:\n                        errors.append(\n                            f"{relative}:{line_number}: palette token {token}; use a semantic alias"\n                        )\n                    elif token not in defined_colors:\n                        errors.append(f"{relative}:{line_number}: undefined color token {token}")\n    return errors\n\n\ndef check_repository(root: Path = _REPOSITORY_ROOT) -> tuple[list[str], list[str]]:\n    token_path = root / "frontend/src/styles/tokens.css"\n    if not token_path.is_file():\n        return ["frontend/src/styles/tokens.css is missing"], []\n    try:\n        themes = _parse_themes(token_path)\n    except ValueError as exc:\n        return [str(exc)], []\n    contrast_errors, rows = _contrast_errors(themes)\n    defined_colors = {\n        name for tokens in themes.values() for name in tokens if name.startswith("--color-")\n    }\n    return contrast_errors + _frontend_style_errors(root, defined_colors), rows\n\n\ndef main() -> int:\n    errors, rows = check_repository()\n    print("Design token contrast matrix:")\n    for row in rows:\n        print(f"  {row}")\n    if errors:\n        for error in errors:\n            print(f"design-token error: {error}", file=sys.stderr)\n        return 1\n    print("Light/dark contrast and semantic color usage satisfy repository policy.")\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
TESTS = 'from __future__ import annotations\n\nimport tempfile\nimport unittest\nfrom pathlib import Path\n\nfrom tools.check_design_tokens import check_repository, contrast_ratio\n\n\nVALID_TOKENS = """:root {\n  --color-cloud-white: #FFFFFF;\n  --color-fog-gray: #F1F5F9;\n  --color-deep-harbor: #0B1E3A;\n  --color-bridge-blue: #2563EB;\n  --color-mint: #D1FAE5;\n  --color-sand: #FEF3C7;\n  --color-rose: #FEE2E2;\n  --color-sky: #DBEAFE;\n  --color-surface: var(--color-cloud-white);\n  --color-background: var(--color-fog-gray);\n  --color-border-control: #64748B;\n  --color-text: var(--color-deep-harbor);\n  --color-text-muted: #475569;\n  --color-action: var(--color-bridge-blue);\n  --color-action-surface: var(--color-bridge-blue);\n  --color-on-accent: #FFFFFF;\n  --color-brand-surface: var(--color-deep-harbor);\n  --color-focus-ring: var(--color-bridge-blue);\n  --color-success-text: #065F46;\n  --color-success-surface: var(--color-mint);\n  --color-warning-text: #92400E;\n  --color-warning-surface: var(--color-sand);\n  --color-danger-text: #991B1B;\n  --color-danger-surface: var(--color-rose);\n  --color-info-text: #1E40AF;\n  --color-info-surface: var(--color-sky);\n}\n\n@media (prefers-color-scheme: dark) {\n  :root {\n    --color-surface: #111827;\n    --color-background: #0F172A;\n    --color-border-control: #94A3B8;\n    --color-text: #F8FAFC;\n    --color-text-muted: #CBD5E1;\n    --color-action: #60A5FA;\n    --color-action-surface: #1D4ED8;\n    --color-on-accent: #FFFFFF;\n    --color-brand-surface: #0B1E3A;\n    --color-focus-ring: #60A5FA;\n    --color-success-text: #D1FAE5;\n    --color-success-surface: #064E3B;\n    --color-warning-text: #FEF3C7;\n    --color-warning-surface: #78350F;\n    --color-danger-text: #FEE2E2;\n    --color-danger-surface: #7F1D1D;\n    --color-info-text: #DBEAFE;\n    --color-info-surface: #1E3A8A;\n  }\n}\n"""\n\n\nclass DesignTokenCheckerTests(unittest.TestCase):\n    def _repository(self, tokens: str = VALID_TOKENS) -> tuple[tempfile.TemporaryDirectory[str], Path]:\n        temporary = tempfile.TemporaryDirectory()\n        root = Path(temporary.name)\n        (root / "frontend/src/styles").mkdir(parents=True)\n        (root / "frontend/src/views").mkdir(parents=True)\n        (root / "frontend/src/components").mkdir(parents=True)\n        (root / "frontend/src/styles/tokens.css").write_text(tokens, encoding="utf-8")\n        (root / "frontend/src/views/TestView.vue").write_text(\n            "<template><p>ok</p></template>\\n"\n            "<style scoped>.x { color: var(--color-danger-text); "\n            "background: var(--color-surface); }</style>\\n",\n            encoding="utf-8",\n        )\n        return temporary, root\n\n    def test_known_light_and_dark_pairs_meet_expected_ratios(self) -> None:\n        self.assertGreaterEqual(contrast_ratio("#0B1E3A", "#FFFFFF"), 4.5)\n        self.assertGreaterEqual(contrast_ratio("#F8FAFC", "#111827"), 4.5)\n        self.assertGreaterEqual(contrast_ratio("#FFFFFF", "#1D4ED8"), 4.5)\n        self.assertGreaterEqual(contrast_ratio("#D1FAE5", "#064E3B"), 4.5)\n\n    def test_valid_repository_checks_both_themes(self) -> None:\n        temporary, root = self._repository()\n        self.addCleanup(temporary.cleanup)\n\n        errors, rows = check_repository(root)\n\n        self.assertEqual(errors, [])\n        self.assertTrue(any(row.startswith("[light]") for row in rows))\n        self.assertTrue(any(row.startswith("[dark]") for row in rows))\n\n    def test_low_contrast_dark_text_fails(self) -> None:\n        temporary, root = self._repository(\n            VALID_TOKENS.replace("--color-text: #F8FAFC;", "--color-text: #111827;")\n        )\n        self.addCleanup(temporary.cleanup)\n\n        errors, _ = check_repository(root)\n\n        self.assertTrue(\n            any("[dark] contrast --color-text / --color-surface" in error for error in errors)\n        )\n\n    def test_palette_reference_in_vue_is_rejected(self) -> None:\n        temporary, root = self._repository()\n        self.addCleanup(temporary.cleanup)\n        (root / "frontend/src/views/TestView.vue").write_text(\n            "<style scoped>.x { color: var(--color-deep-harbor); }</style>\\n",\n            encoding="utf-8",\n        )\n\n        errors, _ = check_repository(root)\n\n        self.assertTrue(any("palette token --color-deep-harbor" in error for error in errors))\n\n    def test_undefined_color_token_is_rejected(self) -> None:\n        temporary, root = self._repository()\n        self.addCleanup(temporary.cleanup)\n        (root / "frontend/src/views/TestView.vue").write_text(\n            "<style scoped>.x { background: var(--color-cloud); }</style>\\n",\n            encoding="utf-8",\n        )\n\n        errors, _ = check_repository(root)\n\n        self.assertTrue(any("undefined color token --color-cloud" in error for error in errors))\n\n    def test_raw_hex_in_view_is_rejected(self) -> None:\n        temporary, root = self._repository()\n        self.addCleanup(temporary.cleanup)\n        (root / "frontend/src/views/TestView.vue").write_text(\n            "<style scoped>.x { color: #991B1B; }</style>\\n",\n            encoding="utf-8",\n        )\n\n        errors, _ = check_repository(root)\n\n        self.assertTrue(any("raw hex #991B1B" in error for error in errors))\n\n    def test_named_white_or_black_declaration_is_rejected(self) -> None:\n        temporary, root = self._repository()\n        self.addCleanup(temporary.cleanup)\n        (root / "frontend/src/views/TestView.vue").write_text(\n            "<style scoped>.x { background: white; color: black; }</style>\\n",\n            encoding="utf-8",\n        )\n\n        errors, _ = check_repository(root)\n\n        self.assertTrue(any("named white/black color declaration" in error for error in errors))\n\n\nif __name__ == "__main__":\n    unittest.main()\n'
SYSTEM_TEST = 'from __future__ import annotations\n\nimport unittest\nfrom pathlib import Path\n\n\n_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]\n\n\nclass FrontendSystemUiPolicyTests(unittest.TestCase):\n    def test_document_branding_has_plain_title_and_favicon(self) -> None:\n        html = (_REPOSITORY_ROOT / "frontend/index.html").read_text(encoding="utf-8")\n\n        self.assertIn("<title>Harbor Transfer Portal</title>", html)\n        self.assertNotIn("🐴", html)\n        self.assertIn(\'rel="icon"\', html)\n        self.assertIn(\'href="/favicon.svg"\', html)\n        self.assertTrue((_REPOSITORY_ROOT / "frontend/public/favicon.svg").is_file())\n\n    def test_reduced_motion_is_global(self) -> None:\n        css = (_REPOSITORY_ROOT / "frontend/src/styles/base.css").read_text(encoding="utf-8")\n\n        self.assertIn("@media (prefers-reduced-motion: reduce)", css)\n        self.assertIn("animation-duration: 0.01ms !important;", css)\n        self.assertIn("animation-iteration-count: 1 !important;", css)\n        self.assertIn("transition-duration: 0.01ms !important;", css)\n        self.assertIn("scroll-behavior: auto !important;", css)\n\n    def test_system_light_dark_theme_policy_is_explicit(self) -> None:\n        base_css = (_REPOSITORY_ROOT / "frontend/src/styles/base.css").read_text(encoding="utf-8")\n        tokens_css = (_REPOSITORY_ROOT / "frontend/src/styles/tokens.css").read_text(encoding="utf-8")\n        decisions = (_REPOSITORY_ROOT / "docs/decisions.md").read_text(encoding="utf-8")\n\n        self.assertIn("html { color-scheme: light dark; }", base_css)\n        self.assertIn("@media (prefers-color-scheme: dark)", tokens_css)\n        self.assertIn("Theme policy — system light/dark", decisions)\n        self.assertNotIn("Theme policy — light-only", decisions)\n\n\nif __name__ == "__main__":\n    unittest.main()\n'
DOC_SECTION = '### Theme policy — system light/dark\n\nНа 2026-09-16 портал поддерживает **системную светлую и тёмную цветовые схемы** через `prefers-color-scheme`; отдельный пользовательский переключатель темы и собственное хранилище preference не вводятся.\n\nПравила реализации:\n\n- palette primitives (`--color-deep-harbor`, `--color-cloud-white` и другие) остаются неизменяемыми исходными цветами и используются только внутри `tokens.css`;\n- views/components используют semantic aliases (`--color-surface`, `--color-text`, `--color-border`, `--color-action`, status aliases и другие), поэтому смена режима не меняет смысл palette tokens;\n- dark mode переопределяет только semantic aliases внутри `@media (prefers-color-scheme: dark)`;\n- Element Plus получает те же semantic aliases, чтобы custom controls и UI-kit не расходились по теме;\n- `tools/check_design_tokens.py` автоматически проверяет WCAG-контраст text/status/control/focus пар **отдельно для light и dark**, запрещает raw/named color hardcodes, прямые palette references и неизвестные color tokens в Vue-файлах;\n- `prefers-reduced-motion` поддерживается независимо от цветовой схемы и продолжает глобально сокращать переходы и анимации.\n\nBrand surface (sidebar) подключён через semantic `--color-brand-surface`; это явно отделяет постоянную идентичность продукта от общего surface/background режима.\n'


def _migrate_vue(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    original = text

    text = re.sub(
        r"(background(?:-color)?\s*:\s*)var\(--color-deep-harbor\)",
        r"\1var(--color-brand-surface)",
        text,
    )
    text = re.sub(
        r"(background(?:-color)?\s*:\s*)var\(--color-bridge-blue\)",
        r"\1var(--color-action-surface)",
        text,
    )
    text = re.sub(
        r"(color\s*:\s*)var\(--color-bridge-blue\)",
        r"\1var(--color-action)",
        text,
    )
    text = re.sub(
        r"(border(?:-[a-z-]+)?\s*:\s*[^;]*?)var\(--color-bridge-blue\)",
        r"\1var(--color-action)",
        text,
    )
    text = re.sub(
        r"(accent-color\s*:\s*)var\(--color-bridge-blue\)",
        r"\1var(--color-action)",
        text,
    )

    for primitive, semantic in (
        ("--color-transfer-green", "--color-positive-accent"),
        ("--color-alert-amber", "--color-warning-accent"),
        ("--color-stop-red", "--color-negative-accent"),
    ):
        text = re.sub(
            rf"(background(?:-color)?\s*:\s*)var\({re.escape(primitive)}\)",
            rf"\1var({semantic})",
            text,
        )
        text = re.sub(
            rf"(border(?:-[a-z-]+)?\s*:\s*[^;]*?)var\({re.escape(primitive)}\)",
            rf"\1var({semantic})",
            text,
        )

    text = re.sub(
        r"(color\s*:\s*)var\(--color-transfer-green\)",
        r"\1var(--color-success-text)",
        text,
    )
    text = re.sub(
        r"(color\s*:\s*)var\(--color-alert-amber\)",
        r"\1var(--color-warning-text)",
        text,
    )
    text = re.sub(
        r"(color\s*:\s*)var\(--color-stop-red\)",
        r"\1var(--color-danger-text)",
        text,
    )

    replacements = {
        "var(--color-cloud)": "var(--color-surface-subtle)",
        "var(--color-cloud-white)": "var(--color-surface)",
        "var(--color-fog-gray)": "var(--color-surface-subtle)",
        "var(--color-steel)": "var(--color-text-muted)",
        "var(--color-mist)": "var(--color-border)",
        "var(--color-deep-harbor)": "var(--color-text)",
        "var(--color-sky)": "var(--color-info-surface)",
        "var(--color-mint)": "var(--color-success-surface)",
        "var(--color-sand)": "var(--color-warning-surface)",
        "var(--color-rose)": "var(--color-danger-surface)",
        "var(--color-bridge-blue)": "var(--color-action)",
        "var(--color-transfer-green)": "var(--color-positive-accent)",
        "var(--color-alert-amber)": "var(--color-warning-accent)",
        "var(--color-stop-red)": "var(--color-negative-accent)",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(
        r"\b(background(?:-color)?)\s*:\s*white\b",
        lambda match: f"{match.group(1)}: var(--color-surface)",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bcolor\s*:\s*white\b",
        "color: var(--color-on-accent)",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(background(?:-color)?)\s*:\s*black\b",
        lambda match: f"{match.group(1)}: var(--color-text)",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bcolor\s*:\s*black\b",
        "color: var(--color-text)",
        text,
        flags=re.IGNORECASE,
    )

    if text != original:
        path.write_text(text, encoding="utf-8")


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(
            f"{path.relative_to(ROOT)}: expected exactly one occurrence of {old!r}"
        )
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> None:
    for directory in (ROOT / "frontend/src/views", ROOT / "frontend/src/components"):
        for path in sorted(directory.rglob("*.vue")):
            _migrate_vue(path)

    (ROOT / "frontend/src/styles/tokens.css").write_text(TOKENS, encoding="utf-8")
    _replace_once(
        ROOT / "frontend/src/styles/base.css",
        "html { color-scheme: light; }",
        "html { color-scheme: light dark; }",
    )

    decisions_path = ROOT / "docs/decisions.md"
    decisions = decisions_path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"### Theme policy — light-only\n.*?(?=\n## Значение статусов)",
        DOC_SECTION.rstrip(),
        decisions,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError(
            "docs/decisions.md: light-only theme section not found exactly once"
        )
    decisions_path.write_text(updated, encoding="utf-8")

    (ROOT / "tools/check_design_tokens.py").write_text(CHECKER, encoding="utf-8")
    (ROOT / "tools/test_design_tokens.py").write_text(TESTS, encoding="utf-8")
    (ROOT / "tools/test_frontend_system_ui.py").write_text(
        SYSTEM_TEST, encoding="utf-8"
    )


if __name__ == "__main__":
    main()
