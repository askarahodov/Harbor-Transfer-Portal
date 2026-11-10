#!/usr/bin/env python3
"""Cross-platform developer launcher for Harbor Transfer Portal.

The launcher intentionally uses only the Python standard library and argv-based
subprocess calls so the same commands work from Linux shells and Windows
PowerShell without GNU Make, WSL, Git Bash, or POSIX command substitution.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
EXAMPLE_ENV_FILE = ROOT / ".env.example"


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


def command_setup(*, dry_run: bool = False) -> int:
    """Create a first-run .env atomically; never overwrite an existing file."""
    if ENV_FILE.exists():
        print(".env уже существует: сохраняем без изменений")
        return 0
    if not EXAMPLE_ENV_FILE.is_file():
        raise DevCliError("Не найден .env.example в корне репозитория")
    if dry_run:
        print("[dry-run] create .env from .env.example with a unique JWT_SECRET")
        return 0

    example = EXAMPLE_ENV_FILE.read_text(encoding="utf-8")
    secret = secrets.token_urlsafe(48)
    if re.search(r"(?m)^JWT_SECRET=", example):
        raise DevCliError(".env.example unexpectedly contains an active JWT_SECRET")
    content = example.rstrip("\n") + "\nJWT_SECRET=" + secret + "\n"
    try:
        with ENV_FILE.open("x", encoding="utf-8") as stream:
            stream.write(content)
        if os.name == "posix":
            ENV_FILE.chmod(0o600)
    except FileExistsError:
        print(".env создан другим процессом: сохраняем без изменений")
        return 0
    print(".env создан; JWT_SECRET записан локально, значение не выводится")
    return 0


def _require_env_file() -> None:
    if not ENV_FILE.is_file():
        raise DevCliError(
            "Требуется .env. Выполните python tools/dev.py setup "
            "(или используйте run.sh/run.cmd)."
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
        print(".env: отсутствует (создайте через 'setup' или run.sh/run.cmd)")

    if ENV_FILE.is_file():
        content = ENV_FILE.read_text(encoding="utf-8")
        secret_present = bool(re.search(r"(?m)^JWT_SECRET=.{32,}$", content))
        if not secret_present:
            print(
                "JWT_SECRET отсутствует в .env: backend сохранит случайный секрет "
                "в persistent volume на первом старте."
            )

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
    if not ENV_FILE.is_file():
        command_setup(dry_run=dry_run)
    env = _compose_environment(dry_run=dry_run)
    _docker_compose(
        "up",
        "-d",
        "--build",
        "--force-recreate",
        env=env,
        dry_run=dry_run,
    )
    print("Новая пустая база: откройте http://127.0.0.1:8080/ и смените пароль admin/admin.")
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


def command_bootstrap(*, dry_run: bool = False) -> int:
    """Explicit local first-login probe; never send the default password automatically on up."""
    _require_env_file()
    raw = ENV_FILE.read_text(encoding="utf-8")
    match = re.search(r"(?m)^PORTAL_HTTP_PORT=(\d+)$", raw)
    port = int(match.group(1)) if match else 8080
    if port < 1 or port > 65535:
        raise DevCliError("Invalid PORTAL_HTTP_PORT in .env")
    url = f"http://127.0.0.1:{port}/api/auth/login"
    if dry_run:
        print(f"[dry-run] check initial login on {url}; no request sent")
        return 0
    payload = json.dumps({"username": "admin", "password": "admin"}).encode("utf-8")
    request = Request(
        url, data=payload, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urlopen(request, timeout=8) as response:
            if response.status != 200:
                raise DevCliError(f"Local bootstrap probe returned HTTP {response.status}")
    except HTTPError as exc:
        if exc.code == 401:
            print("admin/admin больше не работает (пароль изменён или вход заблокирован).")
            return 0
        raise DevCliError(f"Local bootstrap probe returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise DevCliError("Portal ещё не отвечает на loopback; проверьте docker compose ps") from exc
    print("Первый вход admin/admin доступен; ОБЯЗАТЕЛЬНО смените пароль в браузере.")
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
        ("setup", "создать .env и случайный JWT_SECRET без перезаписи существующего"),
        ("doctor", "проверить Git/Docker/Compose и локальную конфигурацию"),
        ("bootstrap", "явно проверить временный local admin/admin до его смены"),
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
        "setup": command_setup,
        "doctor": command_doctor,
        "bootstrap": command_bootstrap,
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
