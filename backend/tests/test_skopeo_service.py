import asyncio
import os
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.services.harbor_settings import EffectiveHarborSettings
from app.services.skopeo_service import (
    AsyncioCommandRunner,
    CommandResult,
    ImageReference,
    SkopeoPhase,
    SkopeoService,
    SkopeoServiceError,
    TargetState,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
TEST_CREDENTIAL = "unit-test-credential-value"


class DummySession:
    pass


class FakeHarborSettings:
    def __init__(self, resolved: EffectiveHarborSettings) -> None:
        self.resolved = resolved

    def resolve(self) -> EffectiveHarborSettings:
        return self.resolved


class FakeRunner:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = results
        self.calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
        self.copy_callback = None

    async def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
        redact_values: tuple[str, ...] = (),
    ) -> CommandResult:
        assert timeout_seconds > 0
        assert output_limit_bytes >= 4096
        self.calls.append((argv, redact_values))
        if len(argv) > 1 and argv[1] == "copy" and self.copy_callback is not None:
            self.copy_callback(argv)
        if not self.results:
            raise AssertionError(f"unexpected Skopeo call: {argv}")
        return self.results.pop(0)


def _settings(tmp_path: Path, *, ca_file: Path | None = None) -> Settings:
    return Settings(
        harbor_url="https://harbor.local:8443",
        harbor_user="robot$transfer",
        harbor_password=SecretStr(TEST_CREDENTIAL),
        harbor_ca_file=ca_file,
        skopeo_payload_root=tmp_path,
        skopeo_temp_root=tmp_path / "tmp",
        skopeo_timeout_seconds=12,
        skopeo_output_limit_bytes=8192,
    )


def _harbor(tmp_path: Path, *, ca_file: Path | None = None, verify_tls: bool = True):
    return EffectiveHarborSettings(
        url="https://harbor.local:8443",
        username="robot$transfer",
        password=TEST_CREDENTIAL,
        verify_tls=verify_tls,
        ca_file=ca_file,
    )


def _inspection(digest: str) -> CommandResult:
    return CommandResult(0, f'{{"Digest":"{digest}","Architecture":"amd64","Os":"linux"}}', "")


def _service(
    tmp_path: Path,
    runner: FakeRunner,
    *,
    ca_file: Path | None = None,
    verify_tls: bool = True,
    progress=None,
) -> SkopeoService:
    service = SkopeoService(
        DummySession(),  # type: ignore[arg-type]
        _settings(tmp_path, ca_file=ca_file),
        runner=runner,
        progress=progress,
    )
    service.harbor_settings = FakeHarborSettings(  # type: ignore[assignment]
        _harbor(tmp_path, ca_file=ca_file, verify_tls=verify_tls)
    )
    return service


def _write_oci_layout(path: Path) -> None:
    (path / "blobs" / "sha256").mkdir(parents=True, exist_ok=True)
    (path / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}', encoding="utf-8")
    (path / "index.json").write_text('{"schemaVersion":2,"manifests":[]}', encoding="utf-8")


def test_image_reference_rejects_transport_and_unsafe_repository() -> None:
    with pytest.raises(ValueError):
        ImageReference("docker://evil.example/team/app", "latest")
    with pytest.raises(ValueError):
        ImageReference("team/../app", "latest")
    with pytest.raises(ValueError):
        ImageReference("team/app", "bad tag")

    digest_ref = ImageReference("team/nested-app", DIGEST_A)
    assert digest_ref.reference == DIGEST_A


def test_export_builds_exact_secure_argv_and_preserves_digest(tmp_path: Path) -> None:
    ca_file = tmp_path / "harbor-ca.pem"
    ca_file.write_text("synthetic-ca", encoding="utf-8")
    runner = FakeRunner([_inspection(DIGEST_A), CommandResult(0, "", ""), _inspection(DIGEST_A)])

    def materialize_layout(argv: tuple[str, ...]) -> None:
        transport = argv[-1]
        assert transport.startswith("oci:") and transport.endswith(":image")
        _write_oci_layout(Path(transport[len("oci:") : -len(":image")]))

    runner.copy_callback = materialize_layout
    phases: list[SkopeoPhase] = []
    service = _service(
        tmp_path,
        runner,
        ca_file=ca_file,
        progress=lambda event: phases.append(event.phase),
    )
    image = ImageReference("team/nested-app", "release-1")
    destination = tmp_path / "outgoing" / "image-1"

    result = asyncio.run(service.export_image(image, destination))

    assert result.source_digest == DIGEST_A
    assert result.payload_digest == DIGEST_A
    assert phases == [
        SkopeoPhase.INSPECTING_SOURCE,
        SkopeoPhase.EXPORTING,
        SkopeoPhase.EXPORTED,
    ]

    inspect_argv, inspect_redactions = runner.calls[0]
    auth_file = inspect_argv[inspect_argv.index("--authfile") + 1]
    cert_dir = inspect_argv[inspect_argv.index("--cert-dir") + 1]
    assert inspect_argv == (
        "skopeo",
        "inspect",
        "--authfile",
        auth_file,
        "--tls-verify=true",
        "--cert-dir",
        cert_dir,
        "docker://harbor.local:8443/team/nested-app:release-1",
    )
    assert TEST_CREDENTIAL in inspect_redactions

    copy_argv, copy_redactions = runner.calls[1]
    src_auth_file = copy_argv[copy_argv.index("--src-authfile") + 1]
    src_cert_dir = copy_argv[copy_argv.index("--src-cert-dir") + 1]
    assert copy_argv == (
        "skopeo",
        "copy",
        "--all",
        "--preserve-digests",
        "--src-authfile",
        src_auth_file,
        "--src-tls-verify=true",
        "--src-cert-dir",
        src_cert_dir,
        "docker://harbor.local:8443/team/nested-app:release-1",
        f"oci:{destination.resolve()}:image",
    )
    assert TEST_CREDENTIAL in copy_redactions
    assert TEST_CREDENTIAL not in " ".join(copy_argv)
    assert os.stat(src_auth_file).st_mode & 0o777 == 0o600
    assert Path(src_cert_dir, "ca.crt").is_file()


def test_explicit_tls_disable_is_reflected_without_ca_fallback(tmp_path: Path) -> None:
    runner = FakeRunner([_inspection(DIGEST_A)])
    service = _service(tmp_path, runner, verify_tls=False)

    asyncio.run(service.inspect_image(ImageReference("team/app", "latest")))

    argv, _ = runner.calls[0]
    assert "--tls-verify=false" in argv
    assert "--cert-dir" not in argv


def test_import_verifies_payload_before_push_and_target_after_push(tmp_path: Path) -> None:
    payload = tmp_path / "incoming" / "image"
    _write_oci_layout(payload)
    runner = FakeRunner([_inspection(DIGEST_A), CommandResult(0, "", ""), _inspection(DIGEST_A)])
    service = _service(tmp_path, runner)

    result = asyncio.run(
        service.import_image(
            payload,
            ImageReference("team/app", "release-1"),
            expected_digest=DIGEST_A,
        )
    )

    assert result.verified is True
    assert result.target_digest == DIGEST_A
    copy_argv, _ = runner.calls[1]
    assert copy_argv[0:4] == ("skopeo", "copy", "--all", "--preserve-digests")
    assert copy_argv[-2] == f"oci:{payload.resolve()}:image"
    assert copy_argv[-1] == "docker://harbor.local:8443/team/app:release-1"
    assert "--dest-tls-verify=true" in copy_argv


def test_import_stops_before_push_when_payload_digest_mismatches(tmp_path: Path) -> None:
    payload = tmp_path / "incoming" / "image"
    _write_oci_layout(payload)
    runner = FakeRunner([_inspection(DIGEST_B)])
    service = _service(tmp_path, runner)

    with pytest.raises(SkopeoServiceError) as exc:
        asyncio.run(
            service.import_image(
                payload,
                ImageReference("team/app", "release-1"),
                expected_digest=DIGEST_A,
            )
        )

    assert exc.value.code == "skopeo_payload_digest_mismatch"
    assert len(runner.calls) == 1


def test_post_import_digest_mismatch_is_explicit_failure(tmp_path: Path) -> None:
    payload = tmp_path / "incoming" / "image"
    _write_oci_layout(payload)
    runner = FakeRunner([_inspection(DIGEST_A), CommandResult(0, "", ""), _inspection(DIGEST_B)])
    service = _service(tmp_path, runner)

    with pytest.raises(SkopeoServiceError) as exc:
        asyncio.run(
            service.import_image(
                payload,
                ImageReference("team/app", "release-1"),
                expected_digest=DIGEST_A,
            )
        )

    assert exc.value.code == "skopeo_digest_mismatch"


def test_target_inspection_reports_absent_same_and_conflict(tmp_path: Path) -> None:
    image = ImageReference("team/app", "latest")

    absent_runner = FakeRunner([CommandResult(1, "", "manifest unknown: not found")])
    absent = asyncio.run(_service(tmp_path, absent_runner).inspect_target(image, expected_digest=DIGEST_A))
    assert absent.state == TargetState.ABSENT
    assert absent.digest is None

    same_runner = FakeRunner([_inspection(DIGEST_A)])
    same = asyncio.run(_service(tmp_path, same_runner).inspect_target(image, expected_digest=DIGEST_A))
    assert same.state == TargetState.SAME_DIGEST

    conflict_runner = FakeRunner([_inspection(DIGEST_B)])
    conflict = asyncio.run(
        _service(tmp_path, conflict_runner).inspect_target(image, expected_digest=DIGEST_A)
    )
    assert conflict.state == TargetState.CONFLICTING_DIGEST
    assert conflict.digest == DIGEST_B


def test_command_error_is_sanitized_and_credentials_are_never_in_argv(tmp_path: Path) -> None:
    runner = FakeRunner([CommandResult(1, "", f"unauthorized: {TEST_CREDENTIAL}")])
    service = _service(tmp_path, runner)

    with pytest.raises(SkopeoServiceError) as exc:
        asyncio.run(service.inspect_image(ImageReference("team/app", "latest")))

    assert exc.value.code == "skopeo_auth_failed"
    assert TEST_CREDENTIAL not in str(exc.value)
    argv, redactions = runner.calls[0]
    assert TEST_CREDENTIAL not in " ".join(argv)
    assert TEST_CREDENTIAL in redactions


def test_payload_path_must_stay_inside_configured_root(tmp_path: Path) -> None:
    runner = FakeRunner([])
    service = _service(tmp_path / "allowed", runner)
    outside = tmp_path / "outside"

    with pytest.raises(SkopeoServiceError) as exc:
        asyncio.run(service.export_image(ImageReference("team/app", "latest"), outside))

    assert exc.value.code == "skopeo_payload_path_outside_root"
    assert runner.calls == []


def test_asyncio_runner_uses_exec_redacts_and_bounds_output(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class Process:
        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.stdout.feed_data((TEST_CREDENTIAL + "x" * 100).encode())
            self.stdout.feed_eof()
            self.stderr.feed_data(TEST_CREDENTIAL.encode())
            self.stderr.feed_eof()
            self.returncode = 0

        async def wait(self) -> int:
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(
        AsyncioCommandRunner().run(
            ("skopeo", "inspect", "docker://harbor.local/team/app:latest"),
            timeout_seconds=1,
            output_limit_bytes=32,
            redact_values=(TEST_CREDENTIAL,),
        )
    )

    assert captured["args"] == ("skopeo", "inspect", "docker://harbor.local/team/app:latest")
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert "shell" not in kwargs
    assert TEST_CREDENTIAL not in result.stdout
    assert TEST_CREDENTIAL not in result.stderr
    assert "[output truncated]" in result.stdout


def test_asyncio_runner_timeout_kills_child(monkeypatch: pytest.MonkeyPatch) -> None:
    killed = False

    class Process:
        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.returncode = None
            self.done = asyncio.Event()

        async def wait(self):
            await self.done.wait()
            return self.returncode

        def kill(self) -> None:
            nonlocal killed
            killed = True
            self.returncode = -9
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.done.set()

    process = Process()

    async def fake_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(SkopeoServiceError) as exc:
        asyncio.run(
            AsyncioCommandRunner().run(
                ("skopeo", "inspect", "oci:/tmp/test:image"),
                timeout_seconds=0.001,
                output_limit_bytes=4096,
            )
        )

    assert exc.value.code == "skopeo_timeout"
    assert killed is True


def test_asyncio_runner_cancellation_kills_child(monkeypatch: pytest.MonkeyPatch) -> None:
    killed = False

    class Process:
        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.returncode = None
            self.done = asyncio.Event()

        async def wait(self):
            await self.done.wait()
            return self.returncode

        def kill(self) -> None:
            nonlocal killed
            killed = True
            self.returncode = -9
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.done.set()

    process = Process()

    async def fake_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    async def scenario() -> None:
        task = asyncio.create_task(
            AsyncioCommandRunner().run(
                ("skopeo", "copy", "oci:/a:image", "oci:/b:image"),
                timeout_seconds=10,
                output_limit_bytes=4096,
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert killed is True
