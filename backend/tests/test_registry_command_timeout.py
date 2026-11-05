"""Timeout must terminate helper processes, not leave operation RUNNING for hours."""

import asyncio
import sys
import time
from pathlib import Path

import pytest

from app.services.nexus_service import AsyncioCommandRunner as NexusRunner
from app.services.nexus_service import NexusServiceError
from app.services.skopeo_service import AsyncioCommandRunner as SkopeoRunner
from app.services.skopeo_service import SkopeoServiceError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runner_type", "error_type"),
    [(NexusRunner, NexusServiceError), (SkopeoRunner, SkopeoServiceError)],
)
async def test_timeout_kills_subprocess_group_and_closes_inherited_pipes(
    tmp_path: Path, runner_type, error_type
) -> None:
    # Child inherits stderr/stdout pipes; killing only the parent would leave
    # _drain_limited waiting for the child's pipe handles until its sleep ends.
    command = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "time.sleep(60)"
    )
    runner = runner_type(tmp_path)
    start = time.monotonic()
    with pytest.raises(error_type) as caught:
        await asyncio.wait_for(
            runner.run(
                (sys.executable, "-c", command),
                timeout_seconds=0.3,
                output_limit_bytes=4096,
            ),
            timeout=5.0,
        )
    assert caught.value.code.endswith("_timeout")
    assert time.monotonic() - start < 5.0
