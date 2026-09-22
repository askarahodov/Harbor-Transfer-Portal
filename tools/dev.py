#!/usr/bin/env python3
"""Cross-platform developer launcher for Harbor Transfer Portal.

The launcher intentionally uses only the Python standard library and argv-based
subprocess calls so the same commands work from Linux shells and Windows
PowerShell without GNU Make, WSL, Git Bash, or POSIX command substitution.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


class DevCliError(RuntimeError):
    """User-facing developer tooling failure."""


def _display_command(command: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(command))


def _resolve_executable(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise DevCliError(f"required command not found on PATH: {name}")
    return resolved


def _run(
    command: Sequence[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    if dry_run:
        print(f"[dry-run] {_display_command(command)}")
        return subprocess.CompletedProcess(list(command), 0, "", "")

    return subprocess.run(
        list(command),
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=capture_output,
        shell=False,
    )


def _require_env_file() -> None:
    if not ENV_FILE.is_file():
        raise DevCliError(
            "Требуется .env. Скопируйте .env.example в .env и заполните локальные значения."
        )


def _git_revision(*, dry_run: bool = False) -> str:
    if dry_run:
        return "dry-run-revision"
    git = _resolve_executable("git")
    result = _run(
        [git, "rev-parse", "--verify", "HEAD"],
        capture_output=True,
    )
    revision = result.stdout.strip()
    if not revision:
        raise DevCliError("git rev-parse HEAD вернул пустой revision")
    return revision


def _compose_environment(*, dry_run: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    env["PORTAL_VCS_REF"] = _git_revision(dry_run=dry_run)
    return env


def _docker_compose(
    *args: str,
    env: dict[str, str] | None = None,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    docker = _resolve_executable("docker")
    return _run([docker, "compose", *args], env=env, dry_run=dry_run)


def command_doctor(*, dry_run: bool = False) -> int:
    print(f"OS: {platform.system()} {platform.release()}")
    print(f"Python: {platform.python_version()}")
    print(f"Repository: {ROOT}")

    git = "git" if dry_run else _resolve_executable("git")
    docker = "docker" if dry_run else _resolve_executable("docker")
    _run([git, "--version"], dry_run=dry_run)
    _run([docker, "--version"], dry_run=dry_run)
    _run([docker, "compose", "version"], dry_run=dry_run)

    if not dry_run:
        docker_info = _run(
            [docker, "info", "--format", "{{.OSType}}"],
            capture_output=True,
        )
        docker_os = docker_info.stdout.strip().lower()
        if docker_os != "linux":
            raise DevCliError(
                "Harbor Transfer Portal requires Docker in Linux containers mode; "
                f"current Docker OSType is {docker_os or 'unknown'}"
            )
        print("Docker OSType: linux")

    if ENV_FILE.is_file():
        print(".env: OK")
    else:
        print(".env: отсутствует (нужен для build/up/compose-config)")

    if platform.system() == "Windows":
        print("Windows runtime: используйте Docker Desktop в Linux containers mode.")
    else:
        print("Linux runtime: требуется Docker Engine + Docker Compose v2.")

    return 0


def command_build(*, dry_run: bool = False) -> int:
    """Build backend/frontend Docker images from the current source revision."""
    _require_env_file()
    env = _compose_environment(dry_run=dry_run)
    _docker_compose("build", env=env, dry_run=dry_run)
    return 0


def command_up(*, dry_run: bool = False) -> int:
    """Build and force-recreate the local stack from the current revision."""
    _require_env_file()
    env = _compose_environment(dry_run=dry_run)
    _docker_compose(
        "up",
        "-d",
        "--build",
        "--force-recreate",
        env=env,
        dry_run=dry_run,
    )
    return 0


def command_down(*, dry_run: bool = False) -> int:
    _docker_compose("down", dry_run=dry_run)
    return 0


def command_logs(*, dry_run: bool = False) -> int:
    try:
        _docker_compose("logs", "-f", dry_run=dry_run)
    except KeyboardInterrupt:
        return 130
    return 0


def command_compose_config(*, dry_run: bool = False) -> int:
    _require_env_file()
    env = _compose_environment(dry_run=dry_run)
    _docker_compose("config", env=env, dry_run=dry_run)
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="показать команды без их выполнения",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command, help_text in (
        ("doctor", "проверить Git/Docker/Compose и локальную конфигурацию"),
        ("build", "собрать backend/frontend Docker images из текущего Git revision"),
        ("up", "собрать и force-recreate локальный Compose stack"),
        ("down", "остановить локальный Compose stack без удаления volume"),
        ("logs", "показывать логи локального Compose stack"),
        ("compose-config", "проверить итоговую Docker Compose configuration"),
    ):
        subparsers.add_parser(command, help=help_text)

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    commands = {
        "doctor": command_doctor,
        "build": command_build,
        "up": command_up,
        "down": command_down,
        "logs": command_logs,
        "compose-config": command_compose_config,
    }

    try:
        return commands[args.command](dry_run=args.dry_run)
    except DevCliError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as error:
        print(
            f"ERROR: command failed with exit code {error.returncode}: "
            f"{_display_command(error.cmd)}",
            file=sys.stderr,
        )
        return error.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
