from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.services.harbor_settings import EffectiveHarborSettings
from app.services.skopeo_service import (
    CommandResult,
    ImageReference,
    SkopeoService,
    SkopeoServiceError,
)

TEST_USERNAME = "robot$transfer"
TEST_CREDENTIAL = "super-secret-runtime-password"


class DummySession:
    pass


class FakeHarborSettings:
    def resolve(self) -> EffectiveHarborSettings:
        return EffectiveHarborSettings(
            url="https://harbor.local",
            username=TEST_USERNAME,
            password=TEST_CREDENTIAL,
            verify_tls=True,
            ca_file=None,
        )


class OneShotRunner:
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.redact_values: tuple[str, ...] = ()

    async def run(
        self,
        _argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
        redact_values: tuple[str, ...] = (),
    ) -> CommandResult:
        assert timeout_seconds > 0
        assert output_limit_bytes >= 4096
        self.redact_values = redact_values
        # Deliberately return raw stderr to verify SkopeoService performs
        # defense-in-depth redaction independently from AsyncioCommandRunner.
        return self.result


def _service(tmp_path: Path, result: CommandResult) -> tuple[SkopeoService, OneShotRunner]:
    runner = OneShotRunner(result)
    settings = Settings(
        _env_file=None,
        harbor_url="https://harbor.local",
        harbor_user=TEST_USERNAME,
        harbor_password=SecretStr(TEST_CREDENTIAL),
        skopeo_payload_root=tmp_path,
        skopeo_temp_root=tmp_path / "skopeo-tmp",
        skopeo_timeout_seconds=10,
        skopeo_output_limit_bytes=8192,
    )
    service = SkopeoService(
        DummySession(),  # type: ignore[arg-type]
        settings,
        runner=runner,
    )
    service.harbor_settings = FakeHarborSettings()  # type: ignore[assignment]
    return service, runner


def test_generic_command_failure_persists_bounded_redacted_diagnostic(tmp_path: Path) -> None:
    stderr = (
        f"copy exploded for {TEST_USERNAME}:{TEST_CREDENTIAL}\n"
        + "diagnostic "
        + "x" * 900
    )
    service, runner = _service(tmp_path, CommandResult(1, "", stderr))

    with pytest.raises(SkopeoServiceError) as caught:
        asyncio.run(service.inspect_image(ImageReference("team/app", "latest")))

    error = caught.value
    assert error.code == "skopeo_command_failed"
    assert TEST_USERNAME not in error.message
    assert TEST_CREDENTIAL not in error.message
    assert "[REDACTED]" in error.message
    assert "\n" not in error.message
    diagnostic = error.message.removeprefix("Skopeo завершился с ошибкой: ")
    assert len(diagnostic) <= 512
    assert diagnostic.endswith("...")
    assert TEST_USERNAME in runner.redact_values
    assert TEST_CREDENTIAL in runner.redact_values


def test_registry_connect_timeout_gets_actionable_code_without_raw_upload_url(
    tmp_path: Path,
) -> None:
    stderr = (
        'writing blob: Patch "https://harbor.local/v2/team/app/blobs/uploads/abc'
        '?_state=opaque-upload-state": dial tcp 192.0.2.10:443: i/o timeout'
    )
    service, _runner = _service(tmp_path, CommandResult(1, "", stderr))

    with pytest.raises(SkopeoServiceError) as caught:
        asyncio.run(service.inspect_image(ImageReference("team/app", "latest")))

    error = caught.value
    assert error.code == "skopeo_registry_connect_timeout"
    assert error.message == (
        "Skopeo не смог установить сетевое соединение с локальным Harbor: "
        "истекло время ожидания"
    )
    assert "_state=" not in error.message
    assert "/blobs/uploads/" not in error.message


@pytest.mark.parametrize(
    "stderr",
    [
        "dial tcp 192.0.2.10:443: connect: connection refused",
        "dial tcp 192.0.2.10:443: connect: no route to host",
        "dial tcp 192.0.2.10:443: connect: network is unreachable",
    ],
)
def test_registry_connection_failure_gets_stable_code(
    tmp_path: Path,
    stderr: str,
) -> None:
    service, _runner = _service(tmp_path, CommandResult(1, "", stderr))

    with pytest.raises(SkopeoServiceError) as caught:
        asyncio.run(service.inspect_image(ImageReference("team/app", "latest")))

    error = caught.value
    assert error.code == "skopeo_registry_connection_failed"
    assert error.message == "Skopeo не смог подключиться к локальному Harbor по сети"


def test_preserve_digest_failure_gets_actionable_code_and_safe_detail(tmp_path: Path) -> None:
    stderr = (
        "copying image: Manifest must be converted but we cannot modify it: "
        f"Instructed to preserve digests; credential={TEST_CREDENTIAL}"
    )
    service, _runner = _service(tmp_path, CommandResult(1, "", stderr))

    with pytest.raises(SkopeoServiceError) as caught:
        asyncio.run(service.inspect_image(ImageReference("team/app", "release-1")))

    error = caught.value
    assert error.code == "skopeo_digest_preservation_failed"
    assert "не смог сохранить OCI digest" in error.message
    assert "Instructed to preserve digests" in error.message
    assert TEST_CREDENTIAL not in error.message
    assert "[REDACTED]" in error.message
