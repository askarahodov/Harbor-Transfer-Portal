from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from deploy.offline_release import (
    ReleaseError,
    expected_payload_files,
    package_release,
    validate_architecture,
    validate_version,
    verify_release_tree,
)


class OfflineReleaseTest(TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "repo"
        (repo / "deploy").mkdir(parents=True)
        (repo / ".env.example").write_text(
            "PORTAL_CONTOUR=SOURCE\nPORTAL_VERSION=dev\n# JWT_SECRET=\n",
            encoding="utf-8",
        )
        (repo / "deploy/compose-offline.yml").write_text(
            "name: harbor-transfer-portal\nservices: {}\n", encoding="utf-8"
        )
        (repo / "deploy/install.sh").write_text(
            "#!/usr/bin/env sh\nset -eu\n", encoding="utf-8"
        )
        (repo / "deploy/OFFLINE-README.md").write_text("# Offline\n", encoding="utf-8")
        return repo

    def _images(self, root: Path) -> tuple[Path, Path]:
        backend = root / "backend.tar"
        frontend = root / "frontend.tar"
        backend.write_bytes(b"backend-image-archive")
        frontend.write_bytes(b"frontend-image-archive")
        return backend, frontend

    def _package(self, root: Path) -> tuple[Path, Path]:
        repo = self._repo(root)
        backend, frontend = self._images(root)
        output = root / "dist"
        archive = package_release(
            repo_root=repo,
            output_dir=output,
            version="1.0.0-rc.1",
            architecture="amd64",
            source_commit="a" * 40,
            backend_image_tar=backend,
            frontend_image_tar=frontend,
            skopeo_version="skopeo version 1.9.3",
            helm_version="v3.22.0+g123",
            generated_at="2026-09-14T10:00:00+00:00",
        )
        extracted = root / "extracted"
        with tarfile.open(archive, "r:gz") as handle:
            members = handle.getmembers()
            self.assertTrue(all(not member.islnk() and not member.issym() for member in members))
            handle.extractall(extracted, filter="data")
        return archive, extracted / "harbor-transfer-portal-v1.0.0-rc.1-offline-install"

    def _installer_fixture(self, root: Path, *, version: str = "1.0.0") -> tuple[Path, Path]:
        repo = Path(__file__).resolve().parents[1]
        kit = root / "kit"
        (kit / "images").mkdir(parents=True)
        for source, destination in (
            (repo / "deploy/install.sh", kit / "install.sh"),
            (repo / "deploy/compose-offline.yml", kit / "compose.yaml"),
        ):
            destination.write_bytes(source.read_bytes())
        os.chmod(kit / "install.sh", 0o755)
        (kit / ".env.example").write_text(
            "PORTAL_CONTOUR=SOURCE\nPORTAL_HTTP_PORT=8080\nPORTAL_VERSION=dev\n# JWT_SECRET=\n",
            encoding="utf-8",
        )
        (kit / "VERSION").write_text(f"{version}\n", encoding="utf-8")
        (kit / "ARCHITECTURE").write_text("amd64\n", encoding="utf-8")
        (kit / "release.json").write_text("{}\n", encoding="utf-8")
        (kit / "images/backend.tar").write_bytes(b"backend")
        (kit / "images/frontend.tar").write_bytes(b"frontend")

        checksum_files = [
            ".env.example",
            "ARCHITECTURE",
            "README.md",
            "VERSION",
            "compose.yaml",
            "images/backend.tar",
            "images/frontend.tar",
            "install.sh",
            "release.json",
        ]
        (kit / "README.md").write_text("# fixture\n", encoding="utf-8")
        lines = []
        for relative in checksum_files:
            digest = hashlib.sha256((kit / relative).read_bytes()).hexdigest()
            lines.append(f"{digest}  {relative}")
        (kit / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")

        fake_bin = root / "bin"
        fake_bin.mkdir()
        docker = fake_bin / "docker"
        docker.write_text(
            """#!/usr/bin/env sh
set -eu
printf '%s\n' "$*" >> "$DOCKER_LOG"
case "$1" in
  info) exit 0 ;;
  version)
    printf '28.1.1\n'
    exit 0
    ;;
  load)
    printf 'Loaded image fixture\n'
    exit 0
    ;;
  image)
    if [ "${2:-}" = inspect ]; then
      case "$*" in
        *--format*) printf 'amd64\n' ;;
      esac
      exit 0
    fi
    ;;
  inspect)
    printf 'healthy\n'
    exit 0
    ;;
  compose)
    case "$*" in
      "compose version"|"compose version --short")
        printf '2.30.0\n'
        exit 0
        ;;
      *" config -q") exit 0 ;;
      *" up -d --no-build") exit 0 ;;
      *" ps -q backend") printf 'backend-id\n'; exit 0 ;;
      *" ps -q frontend") printf 'frontend-id\n'; exit 0 ;;
      *" exec "*) exit 0 ;;
    esac
    ;;
esac
printf 'unexpected docker invocation: %s\n' "$*" >&2
exit 9
""",
            encoding="utf-8",
        )
        os.chmod(docker, 0o755)
        return kit, fake_bin

    def _run_installer(self, kit: Path, fake_bin: Path, contour: str = "SOURCE"):
        docker_log = kit.parent / "docker.log"
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        env["DOCKER_LOG"] = str(docker_log)
        env["INSTALL_HEALTH_TIMEOUT_SECONDS"] = "10"
        result = subprocess.run(
            [str(kit / "install.sh"), contour],
            cwd=kit,
            env=env,
            text=True,
            capture_output=True,
            timeout=20,
        )
        return result, docker_log

    def test_version_and_architecture_validation(self):
        self.assertEqual(validate_version("1.0.0"), "1.0.0")
        self.assertEqual(validate_version("2.3.4-rc.1"), "2.3.4-rc.1")
        for invalid in ("v1.0.0", "dev", "1.0", "1.0.0+build", "1.0.0;rm", "../1.0.0"):
            with self.subTest(invalid=invalid), self.assertRaises(ReleaseError):
                validate_version(invalid)

        self.assertEqual(validate_architecture("amd64"), "amd64")
        self.assertEqual(validate_architecture("arm64"), "arm64")
        with self.assertRaises(ReleaseError):
            validate_architecture("x86_64")

    def test_package_has_strict_allowlisted_layout_and_metadata(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            (repo / ".env").write_text("JWT_SECRET=top-secret\n", encoding="utf-8")
            (repo / "data").mkdir()
            (repo / "data/private-key.pem").write_text("PRIVATE", encoding="utf-8")
            backend, frontend = self._images(root)

            archive = package_release(
                repo_root=repo,
                output_dir=root / "dist",
                version="1.0.0",
                architecture="amd64",
                source_commit="b" * 40,
                backend_image_tar=backend,
                frontend_image_tar=frontend,
                skopeo_version="skopeo version 1.9.3",
                helm_version="v3.22.0",
                generated_at="2026-09-14T10:00:00+00:00",
            )

            self.assertEqual(archive.name, "harbor-transfer-portal-v1.0.0-offline-install.tar.gz")
            sidecar = Path(f"{archive}.sha256")
            self.assertTrue(sidecar.is_file())
            digest, name = sidecar.read_text(encoding="utf-8").strip().split("  ", 1)
            self.assertEqual(digest, hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertEqual(name, archive.name)
            extract = root / "extract"
            with tarfile.open(archive, "r:gz") as handle:
                names = {member.name for member in handle.getmembers() if member.isfile()}
                handle.extractall(extract, filter="data")

            kit_name = "harbor-transfer-portal-v1.0.0-offline-install"
            self.assertEqual(
                {name.removeprefix(f"{kit_name}/") for name in names}, expected_payload_files()
            )
            kit = extract / kit_name
            metadata = verify_release_tree(kit)
            self.assertEqual(metadata["version"], "1.0.0")
            self.assertEqual(metadata["architecture"], "amd64")
            self.assertEqual(metadata["source_commit"], "b" * 40)
            self.assertFalse((kit / ".env").exists())
            self.assertFalse((kit / "data/private-key.pem").exists())
            self.assertTrue((kit / "install.sh").stat().st_mode & 0o111)

    def test_checksum_tamper_is_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, kit = self._package(root)
            (kit / ".env.example").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ReleaseError, "checksum mismatch"):
                verify_release_tree(kit)

    def test_unexpected_payload_file_is_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, kit = self._package(root)
            (kit / "secret.env").write_text("TOKEN=leak\n", encoding="utf-8")
            with self.assertRaisesRegex(ReleaseError, "release layout mismatch"):
                verify_release_tree(kit)

    def test_metadata_image_checksum_is_enforced(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, kit = self._package(root)
            metadata_path = kit / "release.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["images"][0]["sha256"] = "0" * 64
            metadata_path.write_text(
                json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            lines = []
            for line in (kit / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
                digest, path = line.split("  ", 1)
                if path == "release.json":
                    digest = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
                lines.append(f"{digest}  {path}")
            (kit / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ReleaseError, "image archive checksum mismatch"):
                verify_release_tree(kit)

    def test_installer_clean_install_and_same_version_rerun_preserve_env(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            kit, fake_bin = self._installer_fixture(root)
            result, docker_log = self._run_installer(kit, fake_bin)
            self.assertEqual(result.returncode, 0, result.stderr)
            env_text = (kit / ".env").read_text(encoding="utf-8")
            self.assertIn("PORTAL_VERSION=1.0.0", env_text)
            self.assertIn("PORTAL_CONTOUR=SOURCE", env_text)
            jwt_lines = [line for line in env_text.splitlines() if line.startswith("JWT_SECRET=")]
            self.assertEqual(len(jwt_lines), 1)
            self.assertGreaterEqual(len(jwt_lines[0].split("=", 1)[1]), 64)

            with (kit / ".env").open("a", encoding="utf-8") as handle:
                handle.write("CUSTOM_MARKER=preserve-me\n")
            second, _ = self._run_installer(kit, fake_bin)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("CUSTOM_MARKER=preserve-me", (kit / ".env").read_text(encoding="utf-8"))

            calls = docker_log.read_text(encoding="utf-8")
            self.assertNotRegex(calls, r"(^|\n)compose .* build([ \t]|\n|$)")
            self.assertNotRegex(calls, r"(^|\n)pull([ \t]|\n|$)")
            self.assertIn("load -i images/backend.tar", calls)
            self.assertIn("load -i images/frontend.tar", calls)

    def test_installer_rejects_existing_different_version_before_image_load(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            kit, fake_bin = self._installer_fixture(root)
            (kit / ".env").write_text(
                "PORTAL_VERSION=0.9.0\nPORTAL_CONTOUR=SOURCE\nJWT_SECRET=" + "x" * 64 + "\n",
                encoding="utf-8",
            )
            result, docker_log = self._run_installer(kit, fake_bin)
            self.assertEqual(result.returncode, 2)
            self.assertIn("Use upgrade procedure", result.stderr)
            calls = docker_log.read_text(encoding="utf-8")
            self.assertNotIn("load -i", calls)

    def test_repository_release_boundary_forbids_runtime_build_and_pull(self):
        repo = Path(__file__).resolve().parents[1]
        compose = (repo / "deploy/compose-offline.yml").read_text(encoding="utf-8")
        installer = (repo / "deploy/install.sh").read_text(encoding="utf-8")
        builder = (repo / "deploy/build-offline-kit.sh").read_text(encoding="utf-8")

        self.assertNotIn("build:", compose)
        self.assertEqual(compose.count("pull_policy: never"), 2)
        self.assertIn("name: harbor-transfer-portal", compose)
        self.assertNotRegex(installer, r"docker[ \t]+compose[^\n]*[ \t]+build([ \t]|$)")
        self.assertNotRegex(installer, r"docker[ \t]+(?:compose[^\n]*[ \t]+)?pull([ \t]|$)")
        self.assertIn("docker load -i images/backend.tar", installer)
        self.assertIn("docker load -i images/frontend.tar", installer)
        self.assertIn("up -d --no-build", installer)
        self.assertIn("sha256sum -c SHA256SUMS", installer)
        self.assertIn("docker compose -f compose.yaml build backend frontend", builder)
        self.assertIn("docker save --output", builder)
        self.assertIn("--network none --entrypoint skopeo", builder)
        self.assertIn("--network none --entrypoint helm", builder)

    def test_refuses_empty_or_symlinked_image_archive(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            good = root / "good.tar"
            good.write_bytes(b"ok")
            empty = root / "empty.tar"
            empty.touch()
            with self.assertRaisesRegex(ReleaseError, "must not be empty"):
                package_release(
                    repo_root=repo,
                    output_dir=root / "dist-empty",
                    version="1.0.0",
                    architecture="amd64",
                    source_commit="c" * 40,
                    backend_image_tar=empty,
                    frontend_image_tar=good,
                    skopeo_version="1.9.3",
                    helm_version="v3.22.0",
                )

            link = root / "link.tar"
            link.symlink_to(good)
            with self.assertRaisesRegex(ReleaseError, "regular file"):
                package_release(
                    repo_root=repo,
                    output_dir=root / "dist-link",
                    version="1.0.0",
                    architecture="amd64",
                    source_commit="c" * 40,
                    backend_image_tar=link,
                    frontend_image_tar=good,
                    skopeo_version="1.9.3",
                    helm_version="v3.22.0",
                )
