#!/usr/bin/env python3
"""Проверка repository-relative ссылок в Markdown без сетевых запросов."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

INLINE_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
REFERENCE_LINK_RE = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)")
EXTERNAL_SCHEMES = {"http", "https", "mailto", "tel", "data"}


@dataclass(frozen=True)
class LinkError:
    source: Path
    line: int
    target: str
    reason: str


def markdown_files(root: Path) -> list[Path]:
    """Возвращает человекоориентированные Markdown-файлы, проверяемые CI."""
    candidates: set[Path] = set(root.glob("*.md"))
    for directory in (root / "docs", root / "deploy"):
        if directory.exists():
            candidates.update(directory.rglob("*.md"))
    return sorted(path for path in candidates if path.is_file())


def _destination(raw: str) -> str:
    """Извлекает destination из inline Markdown target с optional title."""
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        return value[1 : value.index(">")].strip()
    # В проекте repository-relative paths не используют пробелы. Всё после
    # первого whitespace трактуется как optional Markdown title.
    return value.split(maxsplit=1)[0] if value else ""


def _is_external_or_anchor(target: str) -> bool:
    if not target or target.startswith("#") or target.startswith("//"):
        return True
    parsed = urlsplit(target)
    return bool(parsed.scheme and parsed.scheme.lower() in EXTERNAL_SCHEMES)


def _resolve_local_target(root: Path, source: Path, target: str) -> tuple[Path | None, str | None]:
    parsed = urlsplit(target)
    if parsed.scheme:
        # Неизвестная URI scheme не является repository-relative ссылкой.
        return None, None

    raw_path = unquote(parsed.path)
    if not raw_path:
        # Ссылка вида other.md#anchor с пустым path сюда не попадёт; pure anchor
        # отфильтрован ранее. Пустой destination не проверяем как файл.
        return None, None

    if raw_path.startswith("/"):
        return None, "absolute path не является repository-relative ссылкой"

    candidate = (source.parent / raw_path).resolve(strict=False)
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None, "ссылка выходит за корень репозитория"

    return candidate, None


def check_file(root: Path, source: Path) -> list[LinkError]:
    errors: list[LinkError] = []
    text = source.read_text(encoding="utf-8")
    in_fence = False
    fence_marker = ""

    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue

        raw_targets = [match.group(1) for match in INLINE_LINK_RE.finditer(line)]
        reference = REFERENCE_LINK_RE.match(line)
        if reference:
            raw_targets.append(reference.group(1))

        for raw in raw_targets:
            target = _destination(raw)
            if _is_external_or_anchor(target):
                continue

            candidate, reason = _resolve_local_target(root, source, target)
            if reason:
                errors.append(LinkError(source, line_no, target, reason))
                continue
            if candidate is None:
                continue
            if not candidate.exists():
                errors.append(LinkError(source, line_no, target, "target не существует"))

    return errors


def check_repository(root: Path) -> list[LinkError]:
    errors: list[LinkError] = []
    for source in markdown_files(root):
        errors.extend(check_file(root, source))
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = check_repository(root)
    if not errors:
        print(f"Документация: локальные ссылки корректны ({len(markdown_files(root))} Markdown-файлов).")
        return 0

    print("Обнаружены некорректные локальные ссылки:", file=sys.stderr)
    for error in errors:
        relative = error.source.relative_to(root)
        print(
            f"- {relative}:{error.line}: {error.target!r} — {error.reason}",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
