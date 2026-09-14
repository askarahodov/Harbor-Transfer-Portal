from __future__ import annotations

import asyncio
import io
import tarfile
from collections.abc import Mapping
from pathlib import Path

from pydantic import SecretStr

from app.config import Settings
from app.services.helm_oci_service import (
    HelmChartReference,
    HelmCommandResult,
    HelmOciService,
)

DIGEST_OLD = "sha256:" + "a" * 64
DIGEST_NEW = "sha256:" + "b" * 64


class DummySession:
    def get(self, _model: object, _key: object) -> None:
        return None


class FakeRunner:
    def __init__(self, results: list[HelmCommandResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, ...]] = []

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
        del timeout_seconds, output_limit_bytes, env, cwd, stdin_bytes, redact_values
        self.calls.append(argv)
        return self.results.pop(0)


def _write_chart(path: Path) -> None:
    payload = b"apiVersion: v2\nname: sample-app\nversion: 1.2.3\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("sample-app/Chart.yaml")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))


def test_push_allows_explicit_conflict_overwrite_and_reverifies_digest(tmp_path: Path) -> None:
    workspace = tmp_path / "packages"
    workspace.mkdir()
    package = workspace / "sample-app-1.2.3.tgz"
    _write_chart(package)
    settings = Settings(
        harbor_url="https://harbor.local",
        harbor_user="robot$portal",
        harbor_password=SecretStr("synthetic-password"),
        helm_workspace_root=workspace,
        helm_temp_root=tmp_path / "helm-tmp",
    )
    runner = FakeRunner(
        [
            HelmCommandResult(0, "name: sample-app\nversion: 1.2.3\n", ""),
            HelmCommandResult(0, "", ""),
            HelmCommandResult(0, "Pushed", ""),
        ]
    )
    digests = iter([DIGEST_OLD, DIGEST_NEW])
    service = HelmOciService(
        DummySession(),  # type: ignore[arg-type]
        settings,
        runner=runner,
        digest_resolver=lambda _chart: next(digests),
    )
    chart = HelmChartReference("platform/charts", "sample-app", "1.2.3")

    result = asyncio.run(
        service.push_chart(
            package,
            chart,
            source_digest=DIGEST_NEW,
            allow_existing=True,
        )
    )

    assert result.target_digest == DIGEST_NEW
    assert result.digest_matches_source is True
    assert any(len(argv) > 1 and argv[1] == "push" for argv in runner.calls)
