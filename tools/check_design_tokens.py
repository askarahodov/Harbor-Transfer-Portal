#!/usr/bin/env python3
"""Проверка контраста design tokens и запрета semantic color hardcode."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_TOKEN_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;]+);")
_HEX_RE = re.compile(r"#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?(?:[0-9A-Fa-f]{2})?\b")
_VAR_RE = re.compile(r"var\((--[A-Za-z0-9_-]+)\)")

_TEXT_PAIRS = (
    ("--color-success-text", "--color-success-surface", 4.5),
    ("--color-warning-text", "--color-warning-surface", 4.5),
    ("--color-danger-text", "--color-danger-surface", 4.5),
    ("--color-info-text", "--color-info-surface", 4.5),
)
_NON_TEXT_PAIRS = (
    ("--color-border-control", "--color-surface", 3.0),
    ("--color-border-control", "--color-background", 3.0),
    ("--color-focus-ring", "--color-surface", 3.0),
    ("--color-focus-ring", "--color-background", 3.0),
)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    if len(value) != 6:
        raise ValueError(f"unsupported color value: #{value}")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _channel_luminance(channel: int) -> float:
    normalized = channel / 255
    if normalized <= 0.04045:
        return normalized / 12.92
    return ((normalized + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[int, int, int]) -> float:
    red, green, blue = (_channel_luminance(channel) for channel in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    light = max(_luminance(_hex_to_rgb(first)), _luminance(_hex_to_rgb(second)))
    dark = min(_luminance(_hex_to_rgb(first)), _luminance(_hex_to_rgb(second)))
    return (light + 0.05) / (dark + 0.05)


def _parse_tokens(path: Path) -> dict[str, str]:
    return {
        name: raw_value.strip()
        for name, raw_value in _TOKEN_RE.findall(path.read_text(encoding="utf-8"))
    }


def _resolve_color(name: str, tokens: dict[str, str], stack: tuple[str, ...] = ()) -> str:
    if name in stack:
        raise ValueError(f"cyclic token reference: {' -> '.join((*stack, name))}")
    raw = tokens.get(name)
    if raw is None:
        raise ValueError(f"missing token: {name}")
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", raw):
        return raw.upper()
    variable = _VAR_RE.fullmatch(raw)
    if variable is not None:
        return _resolve_color(variable.group(1), tokens, (*stack, name))
    raise ValueError(f"token {name} is not a resolvable opaque hex color: {raw}")


def _contrast_errors(tokens: dict[str, str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    rows: list[str] = []
    for foreground_name, background_name, threshold in (*_TEXT_PAIRS, *_NON_TEXT_PAIRS):
        try:
            foreground = _resolve_color(foreground_name, tokens)
            background = _resolve_color(background_name, tokens)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        ratio = contrast_ratio(foreground, background)
        status = "PASS" if ratio >= threshold else "FAIL"
        rows.append(
            f"{foreground_name} / {background_name}: {ratio:.2f}:1 "
            f"(порог {threshold:.1f}:1) {status}"
        )
        if ratio < threshold:
            errors.append(
                f"contrast {foreground_name} / {background_name} = {ratio:.2f}:1 "
                f"below {threshold:.1f}:1"
            )
    return errors, rows


def _hardcoded_color_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for directory in (root / "frontend" / "src" / "views", root / "frontend" / "src" / "components"):
        if not directory.is_dir():
            errors.append(f"missing frontend directory: {directory.relative_to(root)}")
            continue
        for path in sorted(directory.rglob("*.vue")):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for color in _HEX_RE.findall(line):
                    errors.append(
                        f"{path.relative_to(root)}:{line_number}: raw hex {color}; use a design token"
                    )
    return errors


def check_repository(root: Path = _REPOSITORY_ROOT) -> tuple[list[str], list[str]]:
    token_path = root / "frontend" / "src" / "styles" / "tokens.css"
    if not token_path.is_file():
        return ["frontend/src/styles/tokens.css is missing"], []
    tokens = _parse_tokens(token_path)
    contrast_errors, rows = _contrast_errors(tokens)
    return contrast_errors + _hardcoded_color_errors(root), rows


def main() -> int:
    errors, rows = check_repository()
    print("Design token contrast matrix:")
    for row in rows:
        print(f"  {row}")
    if errors:
        for error in errors:
            print(f"design-token error: {error}", file=sys.stderr)
        return 1
    print("Design tokens and semantic color usage satisfy repository policy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
