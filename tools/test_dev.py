from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import dev


class DevCliTests(unittest.TestCase):
    def test_up_uses_current_revision_and_force_recreate_without_shell(self) -> None:
        calls: list[tuple[list[str], dict[str, str] | None, bool]] = []

        def fake_run(command, *, cwd=dev.ROOT, env=None, capture_output=False, dry_run=False):
            calls.append((list(command), env, capture_output))
            if command[-3:] == ["rev-parse", "--verify", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, "abc123\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch.object(dev, "ENV_FILE", Path(__file__)),
            patch.object(dev, "_resolve_executable", side_effect=lambda name: name),
            patch.object(dev, "_run", side_effect=fake_run),
        ):
            self.assertEqual(dev.command_up(), 0)

        compose_call = calls[-1]
        self.assertEqual(
            compose_call[0],
            ["docker", "compose", "up", "-d", "--build", "--force-recreate"],
        )
        self.assertEqual(compose_call[1]["PORTAL_VCS_REF"], "abc123")

    def test_build_uses_compose_build_with_revision_identity(self) -> None:
        calls: list[tuple[list[str], dict[str, str] | None]] = []

        def fake_run(command, *, cwd=dev.ROOT, env=None, capture_output=False, dry_run=False):
            calls.append((list(command), env))
            if command[-3:] == ["rev-parse", "--verify", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, "def456\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch.object(dev, "ENV_FILE", Path(__file__)),
            patch.object(dev, "_resolve_executable", side_effect=lambda name: name),
            patch.object(dev, "_run", side_effect=fake_run),
        ):
            self.assertEqual(dev.command_build(), 0)

        self.assertEqual(calls[-1][0], ["docker", "compose", "build"])
        self.assertEqual(calls[-1][1]["PORTAL_VCS_REF"], "def456")

    def test_dry_run_does_not_execute_subprocess(self) -> None:
        with (
            patch.object(dev, "ENV_FILE", Path(__file__)),
            patch.object(dev, "_resolve_executable", side_effect=lambda name: name),
            patch("subprocess.run") as run,
        ):
            self.assertEqual(dev.command_up(dry_run=True), 0)

        run.assert_not_called()

    def test_doctor_rejects_windows_container_mode(self) -> None:
        def fake_run(command, *, cwd=dev.ROOT, env=None, capture_output=False, dry_run=False):
            if command[-3:] == ["info", "--format", "{{.OSType}}"]:
                return subprocess.CompletedProcess(command, 0, "windows\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch.object(dev, "_resolve_executable", side_effect=lambda name: name),
            patch.object(dev, "_run", side_effect=fake_run),
        ):
            with self.assertRaises(dev.DevCliError):
                dev.command_doctor()

    def test_doctor_accepts_linux_container_mode(self) -> None:
        def fake_run(command, *, cwd=dev.ROOT, env=None, capture_output=False, dry_run=False):
            if command[-3:] == ["info", "--format", "{{.OSType}}"]:
                return subprocess.CompletedProcess(command, 0, "linux\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch.object(dev, "_resolve_executable", side_effect=lambda name: name),
            patch.object(dev, "_run", side_effect=fake_run),
        ):
            self.assertEqual(dev.command_doctor(), 0)

    def test_setup_creates_secret_and_never_overwrites_existing_env(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / ".env.example"
            target = root / ".env"
            template.write_text("PORTAL_CONTOUR=SOURCE\n# JWT_SECRET=\n", encoding="utf-8")
            with (
                patch.object(dev, "ENV_FILE", target),
                patch.object(dev, "EXAMPLE_ENV_FILE", template),
            ):
                self.assertEqual(dev.command_setup(), 0)
                original = target.read_text(encoding="utf-8")
                secret = next(
                    line.split("=", 1)[1] for line in original.splitlines()
                    if line.startswith("JWT_SECRET=")
                )
                self.assertGreaterEqual(len(secret), 32)
                self.assertEqual(dev.command_setup(), 0)
                self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_setup_missing_example_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(dev, "ENV_FILE", root / ".env"),
                patch.object(dev, "EXAMPLE_ENV_FILE", root / "missing.example"),
            ):
                with self.assertRaises(dev.DevCliError):
                    dev.command_setup()

    def test_missing_env_build_fails_but_up_runs_setup_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / ".env.example"
            target = root / ".env"
            template.write_text("PORTAL_CONTOUR=SOURCE\n", encoding="utf-8")
            with (
                patch.object(dev, "ENV_FILE", target),
                patch.object(dev, "EXAMPLE_ENV_FILE", template),
                patch.object(dev, "_compose_environment", return_value={"PORTAL_VCS_REF": "abc"}),
                patch.object(dev, "_docker_compose") as compose,
            ):
                with self.assertRaises(dev.DevCliError):
                    dev.command_build()
                self.assertEqual(dev.command_up(), 0)
                self.assertTrue(target.is_file())
                self.assertIn("JWT_SECRET=", target.read_text(encoding="utf-8"))
                compose.assert_called_once()

    def test_hostless_run_scripts_do_not_require_python_or_make(self) -> None:
        shell = (dev.ROOT / "run.sh").read_text(encoding="utf-8")
        cmd = (dev.ROOT / "run.cmd").read_text(encoding="utf-8")
        for script in (shell, cmd):
            self.assertIn("docker compose up -d --build --force-recreate", script)
            self.assertIn(".env.example", script)
            self.assertNotIn("python", script.lower())
            self.assertNotIn("make ", script.lower())

    def test_launcher_uses_argv_subprocess_without_shell(self) -> None:
        with patch("subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(["git", "--version"], 0, "", "")
            dev._run(["git", "--version"])

        _, kwargs = run.call_args
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["cwd"], dev.ROOT)

    def test_powershell_entrypoint_delegates_to_cross_platform_cli(self) -> None:
        script = (dev.ROOT / "dev.ps1").read_text(encoding="utf-8")
        self.assertIn("tools\\dev.py", script)
        self.assertIn("Get-Command py", script)
        self.assertIn("Get-Command python", script)
        self.assertNotIn("wsl", script.lower())

    def test_makefile_delegates_compose_lifecycle_to_python_cli(self) -> None:
        makefile = (dev.ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("python3 tools/dev.py up", makefile)
        self.assertIn("python3 tools/dev.py down", makefile)
        self.assertIn("python3 tools/dev.py logs", makefile)
        self.assertIn("python3 tools/dev.py compose-config", makefile)

    def test_environment_is_copied_before_revision_is_added(self) -> None:
        with (
            patch.object(dev, "_git_revision", return_value="abc"),
            patch.dict(os.environ, {"PRESERVED": "yes"}, clear=True),
        ):
            env = dev._compose_environment()

        self.assertEqual(env["PRESERVED"], "yes")
        self.assertEqual(env["PORTAL_VCS_REF"], "abc")


if __name__ == "__main__":
    unittest.main()
