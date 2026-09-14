#!/usr/bin/env python3
"""Classify changed repository paths into the CI areas that must run.

This module is intentionally Python-stdlib-only so the scope job can execute it
before backend/frontend dependencies are installed.  Git history discovery stays
in the workflow; this file owns only path -> area policy and component-presence
guards.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path

AREAS = ("backend", "frontend", "protocol", "compose", "docs")


def _available_areas(repo_root: Path) -> dict[str, bool]:
    """Return areas that physically exist in the checked-out revision."""
    return {
        "backend": (repo_root / "backend" / "pyproject.toml").is_file(),
        "frontend": (repo_root / "frontend" / "package.json").is_file(),
        "protocol": (
            repo_root / "backend" / "tests" / "test_bundle_protocol.py"
        ).is_file(),
        "compose": (repo_root / "compose.yaml").is_file()
        and (repo_root / "deploy" / "smoke-compose.sh").is_file(),
        "docs": (repo_root / "tools" / "check_doc_links.py").is_file(),
    }


def classify_paths(paths: Iterable[str], repo_root: Path | str = ".") -> dict[str, bool]:
    """Map changed paths to enabled CI areas, gated by component availability."""
    selected = {area: False for area in AREAS}
    workflow_changed = False

    for raw_path in paths:
        path = raw_path.strip()
        if not path:
            continue

        if path.startswith("backend/") or path == "Makefile":
            selected["backend"] = True

        if path.startswith("frontend/") or path == "Makefile":
            selected["frontend"] = True

        if (
            path.startswith("backend/app/domain/")
            or path == "backend/tests/test_bundle_protocol.py"
            or path == "backend/tests/test_bundle_schema.py"
            or path.startswith("docs/schema/")
            or path == "docs/offline-bundle-v1.md"
            or path.startswith("docs/adr/ADR-009-")
        ):
            selected["protocol"] = True

        if (
            path == "compose.yaml"
            or path == ".dockerignore"
            or path == "backend/Dockerfile"
            or path == "frontend/Dockerfile"
            or path == "frontend/nginx.conf"
            or path.startswith("frontend/docker-entrypoint.d/")
            or path.startswith("deploy/")
        ):
            selected["compose"] = True

        if (
            path in {"README.md", "CONTRIBUTING.md", "Makefile"}
            or path.startswith("docs/")
            or (path.startswith("deploy/") and path.endswith(".md"))
            or path in {"tools/check_doc_links.py", "tools/test_check_doc_links.py"}
        ):
            selected["docs"] = True

        if path == ".github/workflows/ci.yml":
            workflow_changed = True

    available = _available_areas(Path(repo_root))
    if workflow_changed:
        selected = dict(available)
    else:
        selected = {
            area: selected[area] and available[area]
            for area in AREAS
        }

    return selected


def _append_outputs(path: Path, selected: dict[str, bool]) -> None:
    with path.open("a", encoding="utf-8") as output:
        for area in AREAS:
            output.write(f"{area}={'true' if selected[area] else 'false'}\n")


def _append_summary(path: Path, selected: dict[str, bool]) -> None:
    labels = {
        "backend": "Backend",
        "frontend": "Frontend",
        "protocol": "Bundle protocol",
        "compose": "Compose",
        "docs": "Documentation",
    }
    with path.open("a", encoding="utf-8") as summary:
        summary.write("### Область CI\n\n")
        summary.write("| Область | Запуск |\n")
        summary.write("| --- | --- |\n")
        for area in AREAS:
            value = "true" if selected[area] else "false"
            summary.write(f"| {labels[area]} | {value} |\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths-file", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--summary-file", type=Path)
    args = parser.parse_args()

    changed_paths = args.paths_file.read_text(encoding="utf-8").splitlines()
    selected = classify_paths(changed_paths, args.repo_root)

    if args.github_output is not None:
        _append_outputs(args.github_output, selected)
    if args.summary_file is not None:
        _append_summary(args.summary_file, selected)

    for area in AREAS:
        print(f"{area}={'true' if selected[area] else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
