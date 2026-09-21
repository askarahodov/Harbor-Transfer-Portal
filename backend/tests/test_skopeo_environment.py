import asyncio
import os
from pathlib import Path

import pytest

from app.services.skopeo_service import AsyncioCommandRunner


@pytest.mark.parametrize(
    "name",
    [
        "JWT_SECRET",
        "HARBOR_PASSWORD",
        "REGISTRY_AUTH_FILE",
        "DOCKER_CONFIG",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "CONTAINERS_REGISTRIES_CONF",
        "CONTAINERS_POLICY",
    ],
)
def test_skopeo_subprocess_environment_does_not_inherit_portal_or_registry_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    monkeypatch.setenv(name, f"forbidden-{name.lower()}")
    captured: dict[str, object] = {}

    class Process:
        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.returncode = 0

        async def wait(self) -> int:
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        environment = kwargs["env"]
        cwd = Path(kwargs["cwd"])
        captured["modes"] = {
            key: os.stat(value).st_mode & 0o777
            for key, value in environment.items()
            if key in {"HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "TMPDIR"}
        }
        captured["cwd_mode"] = os.stat(cwd).st_mode & 0o777
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    asyncio.run(
        AsyncioCommandRunner(tmp_path).run(
            ("skopeo", "inspect", "docker://harbor.local/team/app:latest"),
            timeout_seconds=1,
            output_limit_bytes=4096,
        )
    )

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert name not in environment
    assert set(environment).issubset(
        {
            "PATH",
            "HOME",
            "XDG_CONFIG_HOME",
            "XDG_CACHE_HOME",
            "XDG_RUNTIME_DIR",
            "TMPDIR",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
        }
    )
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "TMPDIR"):
        assert Path(environment[key]).is_relative_to(tmp_path)
    assert Path(kwargs["cwd"]).is_relative_to(tmp_path)
    assert captured["cwd_mode"] == 0o700
    assert captured["modes"] == {
        "HOME": 0o700,
        "XDG_CONFIG_HOME": 0o700,
        "XDG_CACHE_HOME": 0o700,
        "XDG_RUNTIME_DIR": 0o700,
        "TMPDIR": 0o700,
    }


def test_skopeo_relative_executable_is_resolved_before_isolated_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    startup_dir = tmp_path / "backend"
    startup_dir.mkdir()
    monkeypatch.chdir(startup_dir)
    captured: dict[str, object] = {}

    class Process:
        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.stdout.feed_eof()
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

    temp_root = tmp_path / "exec"
    asyncio.run(
        AsyncioCommandRunner(temp_root).run(
            ("./vendor/skopeo", "--version"),
            timeout_seconds=1,
            output_limit_bytes=4096,
        )
    )

    args = captured["args"]
    kwargs = captured["kwargs"]
    assert isinstance(args, tuple)
    assert isinstance(kwargs, dict)
    assert args[0] == str(startup_dir / "vendor" / "skopeo")
    assert Path(kwargs["cwd"]).is_relative_to(temp_root)
