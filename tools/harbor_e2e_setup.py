"""Provision disposable real Harbor projects + scoped robot identities for CI.

The installation is a dedicated HTTPS test Harbor. Never run against production.
Secrets are read from mode-0600 files and only written to mode-0600 files.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import httpx

SOURCE_PROJECT = "harbor-e2e"
TARGET_PROJECT = "harbor-interop-target"


def _read_secret(path: Path) -> str:
    if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("Secret file must be regular, non-symlink, mode 0600")
    secret = path.read_text(encoding="utf-8").strip()
    if not secret:
        raise ValueError("Missing required secret")
    return secret


def _store_secret(path: Path, username: str, password: str) -> None:
    if path.exists():
        raise FileExistsError("Refusing to overwrite robot credential file")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"username": username, "password": password}, handle)


def _create_robot(
    client: httpx.Client,
    project: str,
    name: str,
    actions: list[tuple[str, str]],
    output: Path,
) -> None:
    response = client.post(
        "/api/v2.0/robots",
        json={
            "name": name,
            "level": "project",
            "duration": 1,
            "permissions": [{
                "kind": "project", "namespace": project,
                "access": [
                    {"resource": resource, "action": action}
                    for resource, action in actions
                ],
            }],
        },
    )
    if response.status_code != 201:
        raise ValueError(f"Harbor robot creation failed with HTTP {response.status_code}")
    payload = response.json()
    username, secret = payload.get("name"), payload.get("secret")
    if not isinstance(username, str) or not isinstance(secret, str):
        raise ValueError("Harbor robot credential response invalid")
    _store_secret(output, username, secret)


def configure(
    *,
    url: str,
    ca_file: Path,
    admin_password_file: Path,
    source_robot_file: Path,
    target_robot_file: Path,
) -> None:
    if url != "https://htp-harbor.test:9443":
        raise ValueError("Only the isolated test Harbor endpoint is permitted")
    password = _read_secret(admin_password_file)
    # No redirects, no environment HTTP proxies, HTTPS with explicitly trusted CA.
    with httpx.Client(
        base_url=url,
        verify=str(ca_file),
        auth=httpx.BasicAuth("admin", password),
        timeout=httpx.Timeout(15),
        trust_env=False,
        follow_redirects=False,
    ) as client:
        deadline = time.monotonic() + 180
        while True:
            try:
                response = client.get("/api/v2.0/systeminfo")
                if response.status_code == 200:
                    system = response.json()
                    if not isinstance(system.get("harbor_version"), str):
                        raise ValueError("Endpoint is not Harbor Core API")
                    break
            except httpx.RequestError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError("Harbor Core API not ready")
            time.sleep(3)

        for name in (SOURCE_PROJECT, TARGET_PROJECT):
            response = client.post(
                "/api/v2.0/projects",
                json={"project_name": name, "public": False},
            )
            if response.status_code != 201:
                raise ValueError(f"Harbor project {name} creation HTTP {response.status_code}")
        read_access = [
            ("repository", "pull"),
            ("repository", "list"),
            ("artifact", "read"),
            ("artifact", "list"),
            ("project", "read"),
        ]
        _create_robot(
            client, SOURCE_PROJECT, "htp-source-read", read_access, source_robot_file
        )
        _create_robot(
            client, TARGET_PROJECT, "htp-target-write",
            [*read_access, ("repository", "push")],
            target_robot_file,
        )
        # Use non-admin scoped identity for actual REST access and validate
        # that it cannot browse the other private project.
        source = json.loads(source_robot_file.read_text(encoding="utf-8"))
        target = json.loads(target_robot_file.read_text(encoding="utf-8"))
        for project, identity in (
            (SOURCE_PROJECT, source),
            (TARGET_PROJECT, target),
        ):
            with httpx.Client(
                base_url=url,
                verify=str(ca_file),
                auth=httpx.BasicAuth(identity["username"], identity["password"]),
                follow_redirects=False,
                trust_env=False,
            ) as robot:
                allowed = robot.get(f"/api/v2.0/projects/{project}")
                if allowed.status_code != 200:
                    raise ValueError(
                        f"Harbor robot cannot read own project: HTTP {allowed.status_code}"
                    )
                other = TARGET_PROJECT if project == SOURCE_PROJECT else SOURCE_PROJECT
                denied = robot.get(f"/api/v2.0/projects/{other}")
                if denied.status_code not in (403, 404):
                    raise ValueError("Harbor robot unexpectedly reads another private project")
    print("Real Harbor Core ready: private projects and independently scoped robots verified")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--ca-file", type=Path, required=True)
    parser.add_argument("--admin-password-file", type=Path, required=True)
    parser.add_argument("--source-robot-file", type=Path, required=True)
    parser.add_argument("--target-robot-file", type=Path, required=True)
    opts = parser.parse_args()
    configure(
        url=opts.url, ca_file=opts.ca_file,
        admin_password_file=opts.admin_password_file,
        source_robot_file=opts.source_robot_file,
        target_robot_file=opts.target_robot_file,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
