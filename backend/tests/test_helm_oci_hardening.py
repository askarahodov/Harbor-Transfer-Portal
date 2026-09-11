from __future__ import annotations

import asyncio
import io
import tarfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmCommandResult,
    HelmOciService,
    HelmServiceError,
)

DIGEST = "sha256:" + "a" * 64
TEST_SECRET = "synthetic-helm-credential"


class DummySession:
    def get(self, _model: object, _key: object) -> None:
        return None


class FakeRunner:
    def __init__(
        self,
        results: list[HelmCommandResult],
        *,
        on_call: Callable[[tuple[str, ...]], None] | None = None,
    ) -> None:
        self.results = list(results)
        self.on_call = on_call
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
        env: Mapping[str, str],
        cwd: Path,
        stdin_bytes: bytes | None = None,
        redact_values: tuple[str, ...] = (),
    ) -> HelmCommandResult:
        self.calls.append(
            {
                "argv": argv,
                "env": dict(env),
                "cwd": cwd,
                "stdin": stdin_bytes,
                "timeout": timeout_seconds,
                "limit": output_limit_bytes,
                "redactions": redact_values,
            }
        )
        if self.on_call is not None:
            self.on_call(argv)
        if not self.results:
            raise AssertionError("unexpected Helm command")
        return self.results.pop(0)


def _chart() -> HelmChartReference:
    return HelmChartReference("platform/charts", "sample-app", "1.2.3")


def _settings(tmp_path: Path, *, harbor_url: str = "https://harbor.local") -> Settings:
    workspace = tmp_path / "packages"
    workspace.mkdir(parents=True, exist_ok=True)
    return Settings(
        harbor_url=harbor_url,
        harbor_user="robot$portal",
        harbor_password=SecretStr(TEST_SECRET),
        harbor_verify_tls=True,
        helm_workspace_root=workspace,
        helm_temp_root=tmp_path / "helm-tmp",
        helm_timeout_seconds=1,
        helm_output_limit_bytes=4096,
    )


def _service(
    tmp_path: Path,
    runner: FakeRunner,
    *,
    harbor_url: str = "https://harbor.local",
) -> HelmOciService:
    return HelmOciService(
        DummySession(),  # type: ignore[arg-type]
        _settings(tmp_path, harbor_url=harbor_url),
        runner=runner,
        digest_resolver=lambda _chart: DIGEST,
    )


def _write_chart(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"apiVersion: v2\nname: sample-app\nversion: 1.2.3\n"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("sample-app/Chart.yaml")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))


def test_helm_subprocess_environment_does_not_inherit_portal_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "j" * 40)
    monkeypatch.setenv("HARBOR_PASSWORD", "environment-secret-that-must-not-reach-helm")
    monkeypatch.setenv("PORTAL_INTERNAL_SECRET", "another-secret")
    destination = tmp_path / "packages" / "isolated-env"

    def create_package(argv: tuple[str, ...]) -> None:
        if len(argv) > 1 and argv[1] == "pull":
            _write_chart(destination / "sample-app-1.2.3.tgz")

    runner = FakeRunner(
        [
            HelmCommandResult(0, "", ""),
            HelmCommandResult(0, "", ""),
            HelmCommandResult(0, "name: sample-app\nversion: 1.2.3\n", ""),
        ],
        on_call=create_package,
    )
    asyncio.run(_service(tmp_path, runner).pull_chart(_chart(), destination))

    child_env = runner.calls[0]["env"]
    assert "JWT_SECRET" not in child_env
    assert "HARBOR_PASSWORD" not in child_env
    assert "PORTAL_INTERNAL_SECRET" not in child_env
    assert child_env["HOME"].startswith(str(tmp_path / "helm-tmp"))
    assert child_env["PATH"]


def test_explicit_http_harbor_uses_plain_http_flags(tmp_path: Path) -> None:
    destination = tmp_path / "packages" / "plain-http"

    def create_package(argv: tuple[str, ...]) -> None:
        if len(argv) > 1 and argv[1] == "pull":
            _write_chart(destination / "sample-app-1.2.3.tgz")

    runner = FakeRunner(
        [
            HelmCommandResult(0, "", ""),
            HelmCommandResult(0, "", ""),
            HelmCommandResult(0, "name: sample-app\nversion: 1.2.3\n", ""),
        ],
        on_call=create_package,
    )
    service = _service(tmp_path, runner, harbor_url="http://harbor.local:5000")
    asyncio.run(service.pull_chart(_chart(), destination))

    login_argv = runner.calls[0]["argv"]
    pull_argv = runner.calls[1]["argv"]
    assert "--plain-http" in login_argv
    assert "--plain-http" in pull_argv
    assert "--insecure" not in login_argv
    assert "--insecure-skip-tls-verify" not in pull_argv


def test_special_archive_member_is_rejected_before_helm(tmp_path: Path) -> None:
    package = tmp_path / "packages" / "fifo.tgz"
    package.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(package, "w:gz") as archive:
        info = tarfile.TarInfo("sample-app/pipe")
        info.type = tarfile.FIFOTYPE
        archive.addfile(info)

    runner = FakeRunner([])
    service = _service(tmp_path, runner)
    with pytest.raises(HelmServiceError) as exc:
        asyncio.run(service.validate_package(package, _chart()))

    assert exc.value.code == "helm_package_unsafe_path"
    assert runner.calls == []
