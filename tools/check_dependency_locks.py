#!/usr/bin/env python3
"""Validate committed dependency locks without installing project dependencies."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+")
_PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s]+)$")
_NORMALIZE_RE = re.compile(r"[-_.]+")


def _normalize_name(name: str) -> str:
    return _NORMALIZE_RE.sub("-", name).lower()


def _requirement_name(specification: str) -> str:
    match = _NAME_RE.match(specification.strip())
    if match is None:
        raise ValueError(f"unsupported requirement: {specification!r}")
    return _normalize_name(match.group(0))


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _load_pins(path: Path) -> tuple[dict[str, str], list[str]]:
    pins: dict[str, str] = {}
    errors: list[str] = []
    if not path.is_file():
        return pins, [f"missing lock file: {path.relative_to(path.parents[1])}"]

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN_RE.fullmatch(line)
        if match is None:
            errors.append(f"{path.name}:{line_number}: expected exact name==version pin")
            continue
        name = _normalize_name(match.group(1))
        if name in pins:
            errors.append(f"{path.name}:{line_number}: duplicate package {name}")
            continue
        pins[name] = match.group(2)
    if not pins:
        errors.append(f"{path.name}: lock is empty")
    return pins, errors


def _check_frontend(root: Path) -> list[str]:
    errors: list[str] = []
    package_path = root / "frontend" / "package.json"
    lock_path = root / "frontend" / "package-lock.json"
    if not lock_path.is_file():
        return ["frontend/package-lock.json is missing"]

    try:
        package = _load_json(package_path)
        lock = _load_json(lock_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [f"frontend lock metadata cannot be read: {exc}"]

    if lock.get("lockfileVersion") != 3:
        errors.append("frontend/package-lock.json must use npm lockfileVersion 3")

    packages = lock.get("packages")
    if not isinstance(packages, dict):
        return errors + ["frontend/package-lock.json packages must be an object"]
    root_package = packages.get("")
    if not isinstance(root_package, dict):
        return errors + ["frontend/package-lock.json is missing packages[''] metadata"]

    for field in ("name", "version", "dependencies", "devDependencies"):
        expected = package.get(field, {}) if field.endswith("Dependencies") else package.get(field)
        actual = root_package.get(field, {}) if field.endswith("Dependencies") else root_package.get(field)
        if actual != expected:
            errors.append(f"frontend lock root field {field!r} does not match package.json")

    for location, metadata in packages.items():
        if location == "" or not location.startswith("node_modules/"):
            continue
        if not isinstance(metadata, dict):
            errors.append(f"frontend lock entry {location!r} must be an object")
            continue
        if not isinstance(metadata.get("version"), str):
            errors.append(f"frontend lock entry {location!r} has no exact version")
        resolved = metadata.get("resolved")
        if isinstance(resolved, str) and resolved.startswith("https://registry.npmjs.org/"):
            if not isinstance(metadata.get("integrity"), str):
                errors.append(f"frontend lock entry {location!r} has no integrity hash")
    return errors


def _check_backend(root: Path) -> list[str]:
    errors: list[str] = []
    pyproject_path = root / "backend" / "pyproject.toml"
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"backend/pyproject.toml cannot be read: {exc}"]

    project = pyproject.get("project")
    if not isinstance(project, dict):
        return ["backend/pyproject.toml has no [project] table"]

    runtime_specs = project.get("dependencies", [])
    optional = project.get("optional-dependencies", {})
    dev_specs = optional.get("dev", []) if isinstance(optional, dict) else []
    if not isinstance(runtime_specs, list) or not isinstance(dev_specs, list):
        return ["backend dependency metadata has unexpected shape"]

    try:
        runtime_required = {_requirement_name(str(item)) for item in runtime_specs}
        dev_required = runtime_required | {_requirement_name(str(item)) for item in dev_specs}
    except ValueError as exc:
        return [str(exc)]

    runtime_lock, runtime_errors = _load_pins(root / "backend" / "requirements-runtime.lock")
    dev_lock, dev_errors = _load_pins(root / "backend" / "requirements-dev.lock")
    errors.extend(runtime_errors)
    errors.extend(dev_errors)

    runtime_required.add("hatchling")
    dev_required.add("hatchling")
    for name in sorted(runtime_required - runtime_lock.keys()):
        errors.append(f"backend runtime lock is missing top-level package {name}")
    for name in sorted(dev_required - dev_lock.keys()):
        errors.append(f"backend dev lock is missing top-level package {name}")

    for name, version in sorted(runtime_lock.items()):
        dev_version = dev_lock.get(name)
        if dev_version is None:
            errors.append(f"backend dev lock is missing runtime package {name}")
        elif dev_version != version:
            errors.append(
                f"backend lock mismatch for {name}: runtime={version}, dev={dev_version}"
            )

    local_name = _normalize_name(str(project.get("name", "")))
    if local_name and (local_name in runtime_lock or local_name in dev_lock):
        errors.append("backend locks must not pin the local project itself")
    return errors


def check_repository(root: Path = _REPOSITORY_ROOT) -> list[str]:
    return _check_frontend(root) + _check_backend(root)


def main() -> int:
    errors = check_repository()
    if errors:
        for error in errors:
            print(f"dependency-lock error: {error}", file=sys.stderr)
        return 1
    print("Dependency locks are structurally consistent with package metadata.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
