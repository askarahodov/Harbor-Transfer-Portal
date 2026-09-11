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
    AsyncioHelmCommandRunner,
    HelmChartReference,
    HelmCommandResult,
    HelmOciService,
    HelmServiceError,
    HelmTargetState,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
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
                "timeout": timeout_seconds,
                "limit": output_limit_bytes,
                "env": dict(env),
                "cwd": cwd,
                "stdin": stdin_bytes,
                "redactions": redact_values,
            }
        )
        if self.on_call is not None:
            self.on_call(argv)
        if not self.results:
            raise AssertionError("unexpected Helm command")
        return self.results.pop(0)


def _settings(tmp_path: Path, *, verify_tls: bool = True, ca_file: Path | None = None) -> Settings:
    workspace = tmp_path / "packages"
    workspace.mkdir(parents=True, exist_ok=True)
    return Settings(
        harbor_url="https://harbor.local",
        harbor_user="robot$portal",
        harbor_password=SecretStr(TEST_SECRET),
        harbor_verify_tls=verify_tls,
        harbor_ca_file=ca_file,
        helm_workspace_root=workspace,
        helm_temp_root=tmp_path / "helm-tmp",
        helm_timeout_seconds=0.05,
        helm_output_limit_bytes=32,
    )


def _service(
    tmp_path: Path,
    runner: FakeRunner,
    digests: list[str | None],
    *,
    verify_tls: bool = True,
    ca_file: Path | None = None,
) -> HelmOciService:
    values = iter(digests)
    return HelmOciService(
        DummySession(),  # type: ignore[arg-type]
        _settings(tmp_path, verify_tls=verify_tls, ca_file=ca_file),
        runner=runner,
        digest_resolver=lambda _chart: next(values),
    )


def _chart() -> HelmChartReference:
    return HelmChartReference("platform/charts/team", "sample-app", "1.2.3")


def _write_chart(path: Path, *, name: str = "sample-app", version: str = "1.2.3") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"apiVersion: v2\nname: {name}\nversion: {version}\n".encode()
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(f"{name}/Chart.yaml")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))


def test_reference_allows_nested_repository_and_rejects_transport_injection() -> None:
    chart = _chart()
    assert chart.harbor_repository == "platform/charts/team/sample-app"
    with pytest.raises(ValueError):
        HelmChartReference("oci://evil.invalid/x", "sample-app", "1.2.3")
    with pytest.raises(ValueError):
        HelmChartReference("platform/charts", "../escape", "1.2.3")


def test_pull_uses_password_stdin_isolated_homes_custom_ca_and_validates_package(
    tmp_path: Path,
) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("synthetic-ca", encoding="utf-8")
    destination = tmp_path / "packages" / "export-1"

    def create_package(argv: tuple[str, ...]) -> None:
        if len(argv) > 1 and argv[1] == "pull":
            _write_chart(destination / "renamed-by-helm.tgz")

    runner = FakeRunner(
        [
            HelmCommandResult(0, "Login Succeeded", ""),
            HelmCommandResult(0, "Pulled", ""),
            HelmCommandResult(0, "name: sample-app\nversion: 1.2.3\n", ""),
        ],
        on_call=create_package,
    )
    service = _service(tmp_path, runner, [DIGEST_A], ca_file=ca)

    result = asyncio.run(service.pull_chart(_chart(), destination))

    assert result.source_digest == DIGEST_A
    assert result.package.path.name == "renamed-by-helm.tgz"
    assert len(result.package.sha256) == 64
    login = runner.calls[0]
    pull = runner.calls[1]
    assert login["argv"][:4] == ("helm", "registry", "login", "harbor.local")
    assert "--password-stdin" in login["argv"]
    assert TEST_SECRET not in " ".join(login["argv"])
    assert login["stdin"] == (TEST_SECRET + "\n").encode()
    assert TEST_SECRET in login["redactions"]
    assert "--ca-file" in login["argv"]
    assert pull["argv"][:3] == (
        "helm",
        "pull",
        "oci://harbor.local/platform/charts/team/sample-app",
    )
    assert "--version" in pull["argv"]
    assert "--destination" in pull["argv"]
    assert "--ca-file" in pull["argv"]
    for key in ("HELM_CONFIG_HOME", "HELM_CACHE_HOME", "HELM_DATA_HOME", "HELM_REGISTRY_CONFIG"):
        assert key in login["env"]
        assert str(tmp_path / "helm-tmp") in login["env"][key]


def test_explicit_tls_disable_is_propagated_without_silent_fallback(tmp_path: Path) -> None:
    destination = tmp_path / "packages" / "export-2"

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
    service = _service(tmp_path, runner, [DIGEST_A], verify_tls=False)
    asyncio.run(service.pull_chart(_chart(), destination))

    assert "--insecure" in runner.calls[0]["argv"]
    assert "--insecure-skip-tls-verify" in runner.calls[1]["argv"]


def test_metadata_mismatch_is_rejected(tmp_path: Path) -> None:
    package = tmp_path / "packages" / "sample-app-1.2.3.tgz"
    _write_chart(package)
    runner = FakeRunner([HelmCommandResult(0, "name: other\nversion: 1.2.3\n", "")])
    service = _service(tmp_path, runner, [])

    with pytest.raises(HelmServiceError) as exc:
        asyncio.run(service.validate_package(package, _chart()))

    assert exc.value.code == "helm_chart_metadata_mismatch"


def test_unsafe_archive_member_is_rejected_before_helm(tmp_path: Path) -> None:
    package = tmp_path / "packages" / "unsafe.tgz"
    with tarfile.open(package, "w:gz") as archive:
        payload = b"bad"
        info = tarfile.TarInfo("../escape")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    runner = FakeRunner([])
    service = _service(tmp_path, runner, [])

    with pytest.raises(HelmServiceError) as exc:
        asyncio.run(service.validate_package(package, _chart()))

    assert exc.value.code == "helm_package_unsafe_path"
    assert runner.calls == []


def test_target_inspection_reports_absent_same_and_conflict(tmp_path: Path) -> None:
    runner = FakeRunner([])
    absent = asyncio.run(
        _service(tmp_path, runner, [None]).inspect_target(
            _chart(),
            expected_digest=DIGEST_A,
        )
    )
    same = asyncio.run(
        _service(tmp_path, runner, [DIGEST_A]).inspect_target(
            _chart(),
            expected_digest=DIGEST_A,
        )
    )
    conflict = asyncio.run(
        _service(tmp_path, runner, [DIGEST_B]).inspect_target(
            _chart(),
            expected_digest=DIGEST_A,
        )
    )
    assert absent.state == HelmTargetState.ABSENT
    assert same.state == HelmTargetState.SAME_DIGEST
    assert conflict.state == HelmTargetState.CONFLICTING_DIGEST


def test_push_refuses_existing_target_before_running_helm(tmp_path: Path) -> None:
    package = tmp_path / "packages" / "sample-app-1.2.3.tgz"
    _write_chart(package)
    runner = FakeRunner([])
    service = _service(tmp_path, runner, [DIGEST_B])

    with pytest.raises(HelmServiceError) as exc:
        asyncio.run(service.push_chart(package, _chart(), source_digest=DIGEST_A))

    assert exc.value.code == "helm_target_exists"
    assert runner.calls == []


def test_push_validates_package_pushes_to_local_harbor_and_rechecks_target(tmp_path: Path) -> None:
    package = tmp_path / "packages" / "sample-app-1.2.3.tgz"
    _write_chart(package)
    runner = FakeRunner(
        [
            HelmCommandResult(0, "name: sample-app\nversion: 1.2.3\n", ""),
            HelmCommandResult(0, "", ""),
            HelmCommandResult(0, "Pushed", ""),
        ]
    )
    service = _service(tmp_path, runner, [None, DIGEST_B])

    result = asyncio.run(service.push_chart(package, _chart(), source_digest=DIGEST_A))

    assert result.target_digest == DIGEST_B
    assert result.digest_matches_source is False
    push = runner.calls[2]["argv"]
    assert push[:3] == ("helm", "push", str(package.resolve()))
    assert push[3] == "oci://harbor.local/platform/charts/team"


def test_command_error_is_sanitized_and_secret_is_not_in_argv(tmp_path: Path) -> None:
    destination = tmp_path / "packages" / "export-fail"
    runner = FakeRunner([HelmCommandResult(1, "", f"unauthorized: {TEST_SECRET}")])
    service = _service(tmp_path, runner, [DIGEST_A])

    with pytest.raises(HelmServiceError) as exc:
        asyncio.run(service.pull_chart(_chart(), destination))

    assert exc.value.code == "helm_auth_failed"
    assert TEST_SECRET not in str(exc.value)
    assert TEST_SECRET not in " ".join(runner.calls[0]["argv"])


def test_workspace_escape_is_rejected(tmp_path: Path) -> None:
    runner = FakeRunner([])
    service = _service(tmp_path, runner, [])
    with pytest.raises(HelmServiceError) as exc:
        asyncio.run(service.pull_chart(_chart(), tmp_path / "outside"))
    assert exc.value.code == "helm_workspace_path_outside_root"


def test_asyncio_runner_uses_exec_redacts_and_bounds_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def scenario() -> HelmCommandResult:
        class Process:
            def __init__(self) -> None:
                self.stdout = asyncio.StreamReader()
                self.stderr = asyncio.StreamReader()
                self.stdin = None
                self.returncode = 0
                self.stdout.feed_data((TEST_SECRET + "x" * 100).encode())
                self.stdout.feed_eof()
                self.stderr.feed_eof()

            async def wait(self) -> int:
                return 0

            def kill(self) -> None:
                raise AssertionError("kill not expected")

        async def fake_exec(*argv: str, **kwargs: Any) -> Process:
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return Process()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        return await AsyncioHelmCommandRunner().run(
            ("helm", "version"),
            timeout_seconds=1,
            output_limit_bytes=16,
            env={"PATH": "/bin"},
            cwd=Path("/tmp"),
            redact_values=(TEST_SECRET,),
        )

    result = asyncio.run(scenario())
    assert captured["argv"] == ("helm", "version")
    assert "shell" not in captured["kwargs"]
    assert TEST_SECRET not in result.stdout
    assert "[output truncated]" in result.stdout


def test_asyncio_runner_timeout_and_cancellation_kill_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run_case(cancel: bool) -> bool:
        killed = False

        class Process:
            def __init__(self) -> None:
                self.stdout = asyncio.StreamReader()
                self.stderr = asyncio.StreamReader()
                self.stdin = None
                self.returncode = None
                self.done = asyncio.Event()

            async def wait(self) -> int:
                await self.done.wait()
                return -9

            def kill(self) -> None:
                nonlocal killed
                killed = True
                self.returncode = -9
                self.stdout.feed_eof()
                self.stderr.feed_eof()
                self.done.set()

        process = Process()

        async def fake_exec(*_argv: str, **_kwargs: Any) -> Process:
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        task = asyncio.create_task(
            AsyncioHelmCommandRunner().run(
                ("helm", "pull"),
                timeout_seconds=0.01 if not cancel else 10,
                output_limit_bytes=32,
                env={},
                cwd=Path("/tmp"),
            )
        )
        if cancel:
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(HelmServiceError) as exc:
                await task
            assert exc.value.code == "helm_timeout"
        return killed

    assert asyncio.run(run_case(False)) is True
    assert asyncio.run(run_case(True)) is True
