#!/usr/bin/env python3
"""Validate semantic design-token contrast and reject raw semantic colors in Vue UI."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

TOKEN_RE = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", re.IGNORECASE)
VAR_RE = re.compile(r"^var\((--[a-z0-9-]+)\)$", re.IGNORECASE)
HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")


@dataclass(frozen=True)
class ContrastCheck:
    label: str
    foreground: str
    background: str
    minimum: float


CHECKS = (
    ContrastCheck("success text/surface", "--color-success-text", "--color-success-surface", 4.5),
    ContrastCheck("warning text/surface", "--color-warning-text", "--color-warning-surface", 4.5),
    ContrastCheck("danger text/surface", "--color-danger-text", "--color-danger-surface", 4.5),
    ContrastCheck("info text/surface", "--color-info-text", "--color-info-surface", 4.5),
    ContrastCheck("danger text/surface base", "--color-danger-text", "--color-surface", 4.5),
    ContrastCheck("danger text/background", "--color-danger-text", "--color-background", 4.5),
    ContrastCheck("control border/surface", "--color-border-control", "--color-surface", 3.0),
    ContrastCheck("control border/background", "--color-border-control", "--color-background", 3.0),
    ContrastCheck("focus ring/surface", "--color-focus-ring", "--color-surface", 3.0),
    ContrastCheck("focus ring/background", "--color-focus-ring", "--color-background", 3.0),
)


def parse_tokens(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    return {name: value.strip() for name, value in TOKEN_RE.findall(text)}


def _expand_hex(value: str) -> str | None:
    value = value.strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{3,8}", value):
        return None
    digits = value[1:]
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    if len(digits) != 6:
        return None
    return f"#{digits.upper()}"


def resolve_color(tokens: dict[str, str], name: str, seen: set[str] | None = None) -> str:
    seen = set() if seen is None else set(seen)
    if name in seen:
        raise ValueError(f"cyclic token reference: {name}")
    seen.add(name)
    if name not in tokens:
        raise ValueError(f"missing token: {name}")
    value = tokens[name].strip()
    direct = _expand_hex(value)
    if direct is not None:
        return direct
    match = VAR_RE.fullmatch(value)
    if match:
        return resolve_color(tokens, match.group(1), seen)
    raise ValueError(f"token {name} is not a resolvable opaque hex color: {value}")


def _channel(value: int) -> float:
    normalized = value / 255.0
    return normalized / 12.92 if normalized <= 0.04045 else ((normalized + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    value = color.lstrip("#")
    red, green, blue = (int(value[index : index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    first, second = relative_luminance(foreground), relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def find_raw_hex(root: Path) -> list[str]:
    violations: list[str] = []
    for relative in (Path("frontend/src/views"), Path("frontend/src/components")):
        directory = root / relative
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.vue")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for match in HEX_RE.finditer(line):
                    violations.append(f"{path.relative_to(root)}:{number}: raw color {match.group(0)}")
    return violations


def audit(root: Path) -> tuple[list[tuple[ContrastCheck, float]], list[str]]:
    token_path = root / "frontend/src/styles/tokens.css"
    if not token_path.is_file():
        return [], [f"missing token file: {token_path.relative_to(root)}"]
    tokens = parse_tokens(token_path)
    results: list[tuple[ContrastCheck, float]] = []
    violations: list[str] = []
    for check in CHECKS:
        try:
            foreground = resolve_color(tokens, check.foreground)
            background = resolve_color(tokens, check.background)
        except ValueError as exc:
            violations.append(f"{check.label}: {exc}")
            continue
        ratio = contrast_ratio(foreground, background)
        results.append((check, ratio))
        if ratio + 1e-9 < check.minimum:
            violations.append(
                f"{check.label}: {ratio:.2f}:1 < {check.minimum:.1f}:1 "
                f"({check.foreground} {foreground} on {check.background} {background})"
            )
    violations.extend(find_raw_hex(root))
    return results, violations


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    results, violations = audit(root)
    print("Design-token contrast")
    print("pair | ratio | minimum | status")
    print("--- | ---: | ---: | ---")
    for check, ratio in results:
        status = "PASS" if ratio + 1e-9 >= check.minimum else "FAIL"
        print(f"{check.label} | {ratio:.2f}:1 | {check.minimum:.1f}:1 | {status}")
    if violations:
        print("\nViolations:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1
    print("\nDesign tokens and semantic color usage are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
