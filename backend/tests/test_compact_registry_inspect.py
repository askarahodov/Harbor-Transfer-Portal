"""Compact Nexus/Harbor Skopeo inspect regression for very large RepoTags."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.harbor_settings import EffectiveHarborSettings
from app.services.nexus_service import (
    CommandResult as NexusCommandResult,
)
from app.services.nexus_service import (
    ImageReference as NexusImageReference,
)
from app.services.nexus_service import (
    NexusService,
    NexusServiceError,
)
from app.services.nexus_service import (
    _drain_limited as drain_nexus,
)
from app.services.skopeo_service import (
    CommandResult as HarborCommandResult,
)
from app.services.skopeo_service import (
    ImageReference as HarborImageReference,
)
from app.services.skopeo_service import (
    SkopeoService,
    SkopeoServiceError,
)
from app.services.skopeo_service import (
    _drain_limited as drain_harbor,
)

DIGEST = "sha256:" + "a" * 64
FORMAT = (
    '{"Digest":{{json .Digest}},"Architecture":{{json .Architecture}},'
    '"Os":{{json .Os}}}'
)
# Mimics the operator's image with a huge tag list; the default inspect
# payload is more than 65,536 bytes, while these three fields are tiny.
LEGACY_INSPECT = json.dumps({
    "Digest": DIGEST,
    "Architecture": "amd64",
    "Os": "linux",
    "RepoTags": [f"long-tag-{i:05d}" for i in range(7000)],
})
assert len(LEGACY_INSPECT.encode("utf-8")) > 65_536


class CompactInspectRunner:
    def __init__(self, *, nexus: bool) -> None:
        self.nexus = nexus
        self.calls: list[tuple[str, ...]] = []
        self.limits: list[int] = []

    async def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
        redact_values: tuple[str, ...] = (),
    ):
        self.calls.append(argv)
        self.limits.append(output_limit_bytes)
        assert argv[:2] == ("skopeo", "inspect")
        assert "--no-tags" in argv
        assert argv[argv.index("--format") + 1] == FORMAT
        assert not any("password" in item.casefold() for item in argv)
        assert timeout_seconds > 0
        output = json.dumps({
            "Digest": DIGEST,
            "Architecture": "amd64",
            "Os": "linux",
        })
        assert len(output.encode()) < output_limit_bytes
        result = NexusCommandResult if self.nexus else HarborCommandResult
        return result(0, output, "")


def _service(tmp_path: Path, *, nexus: bool):
    settings = MagicMock()
    settings.skopeo_binary = "skopeo"
    settings.skopeo_timeout_seconds = 12
    settings.skopeo_output_limit_bytes = 4096
    settings.skopeo_payload_root = tmp_path
    settings.skopeo_temp_root = tmp_path / "tmp"
    settings.nexus_url = "https://registry.example.test"
    settings.nexus_user = None
    settings.nexus_password = None
    settings.nexus_password_file = None
    settings.nexus_verify_tls = True
    settings.nexus_ca_file = None

    runner = CompactInspectRunner(nexus=nexus)
    cls = NexusService if nexus else SkopeoService
    service = cls(MagicMock(), settings, runner=runner)
    if not nexus:
        service._resolve_harbor = MagicMock(return_value=EffectiveHarborSettings(
            url="https://registry.example.test",
            username=None,
            password=None,
            verify_tls=True,
            ca_file=None,
        ))
    image = (
        NexusImageReference("node", "18-alpine")
        if nexus else HarborImageReference("node", "18-alpine")
    )
    return service, image, runner


@pytest.mark.asyncio
@pytest.mark.parametrize("nexus", [True, False], ids=["nexus", "harbor"])
async def test_compact_inspect_works_for_large_tag_repositories(
    tmp_path: Path, nexus: bool,
) -> None:
    service, image, runner = _service(tmp_path, nexus=nexus)
    source = await service.inspect_image(image)
    assert source.digest == DIGEST
    assert source.architecture == "amd64"
    assert source.os == "linux"

    target = await service.inspect_target(image, expected_digest=DIGEST)
    assert target.digest == DIGEST
    assert target.state.value == "same_digest"

    # OCI inspection is reused by SOURCE package and TARGET import.
    oci = await service._inspect_oci(tmp_path / "layout")
    assert oci.digest == DIGEST
    assert len(runner.calls) == 3
    assert runner.limits == [4096] * 3
    assert runner.calls[0][-1] == "docker://registry.example.test/node:18-alpine"
    assert runner.calls[1][-1] == "docker://registry.example.test/node:18-alpine"
    assert runner.calls[2][-1] == f"oci:{tmp_path / 'layout'}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drain,error",
    [(drain_nexus, NexusServiceError), (drain_harbor, SkopeoServiceError)],
    ids=["nexus", "harbor"],
)
async def test_truncated_legacy_inspect_is_typed_and_does_not_leak_tags(
    drain, error,
) -> None:
    stream = asyncio.StreamReader()
    stream.feed_data(LEGACY_INSPECT.encode("utf-8"))
    stream.feed_eof()
    output = await drain(stream, 65_536)
    assert output.endswith("[output truncated]")
    assert len(output) < len(LEGACY_INSPECT)
    service = NexusService if error is NexusServiceError else SkopeoService
    with pytest.raises(error) as exc:
        service._parse_inspection(output)
    assert exc.value.code == (
        "nexus_output_truncated" if error is NexusServiceError
        else "skopeo_output_truncated"
    )
    assert "long-tag-" not in str(exc.value)
    assert "invalid_output" not in exc.value.code


@pytest.mark.parametrize(
    "service,error",
    [(NexusService, NexusServiceError), (SkopeoService, SkopeoServiceError)],
)
def test_compact_inspect_rejects_missing_or_malformed_digest(service, error) -> None:
    with pytest.raises(error) as exc:
        service._parse_inspection('{"Digest": "not-a-digest", "Architecture": "amd64"}')
    assert exc.value.code.endswith("invalid_output")
