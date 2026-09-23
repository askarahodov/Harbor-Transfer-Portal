#!/usr/bin/env python3
"""Проверка repository-relative ссылок и current documentation contracts без сети."""

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


@dataclass(frozen=True)
class ContractError:
    source: Path
    line: int
    claim: str
    reason: str


_CURRENT_GUIDE_REQUIRED_CLAIMS: dict[str, tuple[str, ...]] = {
    "docs/user-guide.md": (
        "Terminal History остаётся историческим/read-only",
        "может открыть/продолжить тот же workflow либо запросить штатную отмену",
        "остаётся полностью read-only",
    ),
    "docs/admin-guide.md": (
        "installation-wide active selector не является",
        "immutable snapshot `harbor_profile_id/name/url`",
        "**Legacy fallback Harbor**",
        "Переключение fallback не блокируется новыми pinned",
        "Mutation safety относится к самому operation-bound profile",
    ),
}

_CURRENT_GUIDE_FORBIDDEN_CLAIMS: dict[str, tuple[str, ...]] = {
    "docs/user-guide.md": (
        "History — read-only экран. Из него нельзя менять policy, перезапускать или отменять operation.",
    ),
    "docs/admin-guide.md": (
        "Именно его используют Harbor browse API, SOURCE export, TARGET destination validation, Skopeo и Helm.",
        "runtime browse/transfer используют authoritative active profile",
    ),
}


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


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, max(offset, 0)) + 1


def check_current_contract_claims(root: Path) -> list[ContractError]:
    """Защищает current guides от известных устаревших product claims."""
    errors: list[ContractError] = []

    for relative, required_claims in _CURRENT_GUIDE_REQUIRED_CLAIMS.items():
        source = root / relative
        if not source.is_file():
            errors.append(ContractError(source, 1, relative, "current guide отсутствует"))
            continue

        text = source.read_text(encoding="utf-8")
        for claim in required_claims:
            if claim not in text:
                errors.append(
                    ContractError(
                        source,
                        1,
                        claim,
                        "обязательное current-contract утверждение отсутствует",
                    )
                )

        for claim in _CURRENT_GUIDE_FORBIDDEN_CLAIMS.get(relative, ()):
            offset = text.find(claim)
            if offset >= 0:
                errors.append(
                    ContractError(
                        source,
                        _line_number(text, offset),
                        claim,
                        "обнаружено устаревшее утверждение, противоречащее current contract",
                    )
                )

    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    link_errors = check_repository(root)
    contract_errors = check_current_contract_claims(root)
    if not link_errors and not contract_errors:
        print(
            "Документация: локальные ссылки и current-contract assertions корректны "
            f"({len(markdown_files(root))} Markdown-файлов)."
        )
        return 0

    if link_errors:
        print("Обнаружены некорректные локальные ссылки:", file=sys.stderr)
        for error in link_errors:
            relative = error.source.relative_to(root)
            print(
                f"- {relative}:{error.line}: {error.target!r} — {error.reason}",
                file=sys.stderr,
            )

    if contract_errors:
        print("Обнаружены рассинхронизированные current documentation contracts:", file=sys.stderr)
        for error in contract_errors:
            relative = error.source.relative_to(root)
            print(
                f"- {relative}:{error.line}: {error.claim!r} — {error.reason}",
                file=sys.stderr,
            )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
