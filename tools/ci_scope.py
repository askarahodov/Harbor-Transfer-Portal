#!/usr/bin/env python3
"""Deterministic changed-path classification for scoped GitHub Actions checks.

The module intentionally uses only Python's standard library so the scope job can
run immediately after checkout without installing project dependencies.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

_AREA_NAMES = ("backend", "frontend", "protocol", "compose", "docs")


@dataclass(frozen=True, slots=True)
class Scope:
    backend: bool = False
    frontend: bool = False
    protocol: bool = False
    compose: bool = False
    docs: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "backend": self.backend,
            "frontend": self.frontend,
            "protocol": self.protocol,
            "compose": self.compose,
            "docs": self.docs,
        }


def _under(path: PurePosixPath, directory: str) -> bool:
    return bool(path.parts) and path.parts[0] == directory


def _is_deploy_markdown(path: PurePosixPath) -> bool:
    return len(path.parts) == 2 and path.parts[0] == "deploy" and path.suffix == ".md"


def _classify_path(path_text: str) -> tuple[set[str], bool]:
    path = PurePosixPath(path_text)
    areas: set[str] = set()
    workflow_changed = path == PurePosixPath(".github/workflows/ci.yml")

    if _under(path, "backend") or path_text == "Makefile":
        areas.add("backend")

    if _under(path, "frontend") or path_text == "Makefile":
        areas.add("frontend")

    if (
        path.match("backend/app/domain/*")
        or path_text in {
            "backend/tests/test_bundle_protocol.py",
            "backend/tests/test_bundle_schema.py",
            "docs/offline-bundle-v1.md",
        }
        or path.match("docs/schema/*")
        or path.match("docs/adr/ADR-009-*")
    ):
        areas.add("protocol")

    if (
        path_text
        in {
            "compose.yaml",
            ".dockerignore",
            "backend/Dockerfile",
            "frontend/Dockerfile",
            "frontend/nginx.conf",
        }
        or path.match("frontend/docker-entrypoint.d/*")
        or _under(path, "deploy")
    ):
        areas.add("compose")

    if (
        path_text
        in {
            "README.md",
            "CONTRIBUTING.md",
            "tools/check_doc_links.py",
            "tools/test_check_doc_links.py",
            "Makefile",
        }
        or _under(path, "docs")
        or _is_deploy_markdown(path)
    ):
        areas.add("docs")

    return areas, workflow_changed


def classify_paths(paths: Iterable[str], *, root: Path = Path(".")) -> Scope:
    areas: set[str] = set()
    workflow_changed = False

    for raw_path in paths:
        path = raw_path.strip()
        if not path:
            continue
        path_areas, changed_workflow = _classify_path(path)
        areas.update(path_areas)
        workflow_changed = workflow_changed or changed_workflow

    if workflow_changed:
        areas.update({"backend", "protocol", "docs"})
        if (root / "frontend/package.json").is_file():
            areas.add("frontend")
        if (root / "compose.yaml").is_file():
            areas.add("compose")

    # Do not create a green/skipped fiction for components absent in the checked revision.
    if not (root / "backend/pyproject.toml").is_file():
        areas.discard("backend")
    if not (root / "frontend/package.json").is_file():
        areas.discard("frontend")
    if not (root / "backend/tests/test_bundle_protocol.py").is_file():
        areas.discard("protocol")
    if not (
        (root / "compose.yaml").is_file()
        and (root / "deploy/smoke-compose.sh").is_file()
    ):
        areas.discard("compose")
    if not (root / "tools/check_doc_links.py").is_file():
        areas.discard("docs")

    return Scope(**{name: name in areas for name in _AREA_NAMES})


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def write_github_outputs(scope: Scope, output_path: Path) -> None:
    with output_path.open("a", encoding="utf-8") as handle:
        for name, enabled in scope.as_dict().items():
            handle.write(f"{name}={_bool_text(enabled)}\n")


def write_summary(scope: Scope, summary_path: Path) -> None:
    labels = {
        "backend": "Backend",
        "frontend": "Frontend",
        "protocol": "Bundle protocol",
        "compose": "Compose",
        "docs": "Documentation",
    }
    with summary_path.open("a", encoding="utf-8") as handle:
        handle.write("### Область CI\n\n")
        handle.write("| Область | Запуск |\n")
        handle.write("| --- | --- |\n")
        for name, enabled in scope.as_dict().items():
            handle.write(f"| {labels[name]} | {_bool_text(enabled)} |\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("changed_files", type=Path, help="newline-separated changed paths")
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--summary", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = args.changed_files.read_text(encoding="utf-8").splitlines()
    scope = classify_paths(paths, root=args.root)

    if args.github_output is not None:
        write_github_outputs(scope, args.github_output)
    else:
        for name, enabled in scope.as_dict().items():
            print(f"{name}={_bool_text(enabled)}")

    if args.summary is not None:
        write_summary(scope, args.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
