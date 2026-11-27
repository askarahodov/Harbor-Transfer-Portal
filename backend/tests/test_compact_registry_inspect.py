"""Harbor-only regression: compact Skopeo inspect survives very large RepoTags."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.services.harbor_settings import EffectiveHarborSettings
from app.services.skopeo_service import (
    _COMPACT_INSPECT_FORMAT,
    CommandResult,
    ImageReference,
    SkopeoService,
    SkopeoServiceError,
    _drain_limited,
)

DIGEST = "sha256:" + "a" * 64
OUTPUT_LIMIT_BYTES = 65_536
# Mimics an image with a huge tag list: the default inspect payload exceeds
# the configured output limit, while the compact projection stays tiny.
LEGACY_INSPECT = json.dumps(
    {
        "Digest": DIGEST,
        "Architecture": "amd64",
        "Os": "linux",
        "MediaType": "application/vnd.oci.image.manifest.v1+json",
        "RepoTags": [f"long-tag-{i:05d}" for i in range(7000)],
    }
)
assert len(LEGACY_INSPECT.encode("utf-8")) > OUTPUT_LIMIT_BYTES


class DummySession:
    pass


class FakeHarborSettings:
    def __init__(self, resolved: EffectiveHarborSettings) -> None:
        self.resolved = resolved

    def resolve(self) -> EffectiveHarborSettings:
        return self.resolved


class CompactInspectRunner:
    """Emulates Skopeo: compact output only when the fix flags are present."""

    def __init__(self, output_limit_bytes: int = OUTPUT_LIMIT_BYTES) -> None:
        self.output_limit_bytes = output_limit_bytes
        self.calls: list[tuple[str, ...]] = []

    async def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
        redact_values: tuple[str, ...] = (),
    ) -> CommandResult:
        self.calls.append(argv)
        assert argv[:2] == ("skopeo", "inspect")
        assert timeout_seconds > 0
        assert not any("password" in item.casefold() for item in argv)
        compact = (
            "--no-tags" in argv
            and "--format" in argv
            and argv[argv.index("--format") + 1] == _COMPACT_INSPECT_FORMAT
        )
        if compact:
            output = json.dumps(
                {"Digest": DIGEST, "Architecture": "amd64", "Os": "linux"}
            )
            assert len(output.encode("utf-8")) < output_limit_bytes
        else:
            raw = LEGACY_INSPECT.encode("utf-8")[:output_limit_bytes]
            output = raw.decode("utf-8", errors="replace") + "\n[output truncated]"
        return CommandResult(0, output, "")


def _service(
    tmp_path: Path, runner: CompactInspectRunner
) -> SkopeoService:
    settings = Settings(
        harbor_url="https://harbor.local:8443",
        harbor_user="robot$transfer",
        harbor_password=SecretStr("unit-test-credential-value"),
        skopeo_payload_root=tmp_path,
        skopeo_temp_root=tmp_path / "tmp",
        skopeo_timeout_seconds=12,
        skopeo_output_limit_bytes=OUTPUT_LIMIT_BYTES,
    )
    service = SkopeoService(DummySession(), settings, runner=runner)  # type: ignore[arg-type]
    service.harbor_settings = FakeHarborSettings(  # type: ignore[assignment]
        EffectiveHarborSettings(
            url="https://harbor.local:8443",
            username=None,
            password=None,
            verify_tls=True,
            ca_file=None,
        )
    )
    return service


async def _exercise_all_inspect_paths(
    service: SkopeoService, image: ImageReference, tmp_path: Path
) -> None:
    source = await service.inspect_image(image)
    assert source.digest == DIGEST
    assert source.architecture == "amd64"
    assert source.os == "linux"

    target = await service.inspect_target(image, expected_digest=DIGEST)
    assert target.digest == DIGEST
    assert target.state.value == "same_digest"

    oci = await service._inspect_oci(tmp_path / "layout")
    assert oci.digest == DIGEST


def test_all_harbor_inspect_paths_use_compact_flags(tmp_path: Path) -> None:
    runner = CompactInspectRunner()
    service = _service(tmp_path, runner)
    image = ImageReference("node", "18-alpine")

    asyncio.run(_exercise_all_inspect_paths(service, image, tmp_path))

    assert len(runner.calls) == 3
    for argv in runner.calls:
        assert "--no-tags" in argv
        assert argv[argv.index("--format") + 1] == _COMPACT_INSPECT_FORMAT
    assert runner.calls[0][-1] == "docker://harbor.local:8443/node:18-alpine"
    assert runner.calls[1][-1] == "docker://harbor.local:8443/node:18-alpine"
    assert runner.calls[2][-1] == f"oci:{tmp_path / 'layout'}"


def test_legacy_inspect_without_compact_flags_is_truncated() -> None:
    runner = CompactInspectRunner()

    async def scenario() -> CommandResult:
        return await runner.run(
            ("skopeo", "inspect", "docker://harbor.local:8443/node:18-alpine"),
            timeout_seconds=1,
            output_limit_bytes=OUTPUT_LIMIT_BYTES,
        )

    result = asyncio.run(scenario())
    assert result.stdout.endswith("[output truncated]")
    with pytest.raises(SkopeoServiceError) as exc:
        SkopeoService._parse_inspection(result.stdout)
    assert exc.value.code == "skopeo_output_truncated"


def test_truncated_inspect_is_typed_error_and_does_not_leak_tags() -> None:
    async def scenario() -> str:
        stream = asyncio.StreamReader()
        stream.feed_data(LEGACY_INSPECT.encode("utf-8"))
        stream.feed_eof()
        return await _drain_limited(stream, OUTPUT_LIMIT_BYTES)

    output = asyncio.run(scenario())
    assert output.endswith("[output truncated]")
    assert len(output) < len(LEGACY_INSPECT)

    with pytest.raises(SkopeoServiceError) as exc:
        SkopeoService._parse_inspection(output)
    assert exc.value.code == "skopeo_output_truncated"
    assert "long-tag-" not in str(exc.value)
    assert "invalid_output" not in exc.value.code