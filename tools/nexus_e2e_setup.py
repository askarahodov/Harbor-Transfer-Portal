#!/usr/bin/env python3
"""Provision a disposable Nexus Repository for real CI registry tests.

Requires the workflow's isolated local Docker container and never touches
external/production registries. The initial admin password is only written to
a chmod-0600 file, never logged.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8081"
API = BASE + "/service/rest/v1"
NEXUS_CONTAINER = "htp-nexus-e2e"


def request(method: str, path: str, password: str, payload: object | None = None):
    auth = base64.b64encode(f"admin:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = Request(API + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=15) as response:
            content = response.read()
            return json.loads(content) if content else None
    except HTTPError as exc:
        # Credentials are deliberately absent from errors and logs.
        detail = exc.read(1200).decode("utf-8", errors="replace")
        raise RuntimeError(f"Nexus {method} {path}: HTTP {exc.code}: {detail}") from None


def initial_password() -> str | None:
    result = subprocess.run(
        ("docker", "exec", NEXUS_CONTAINER, "cat", "/nexus-data/admin.password"),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def wait_ready(timeout: float = 900) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        password = initial_password()
        if password:
            try:
                request("GET", "/security/realms/active", password)
                return password
            except (RuntimeError, URLError, OSError, TimeoutError):
                pass
        time.sleep(4)
    raise TimeoutError("Nexus did not become API-ready before deadline")


def provision(password: str) -> None:
    realms = request("GET", "/security/realms/active", password)
    if not isinstance(realms, list):
        raise RuntimeError(f"Unexpected realms response type: {type(realms).__name__}")
    if "DockerToken" not in realms:
        request("PUT", "/security/realms/active", password, [*realms, "DockerToken"])

    request(
        "POST", "/repositories/docker/hosted", password,
        {
            "name": "htp-e2e",
            "online": True,
            "storage": {
                "blobStoreName": "default",
                "strictContentTypeValidation": True,
                "writePolicy": "allow",
            },
            "cleanup": {"policyNames": []},
            "component": {"proprietaryComponents": True},
            "docker": {
                "v1Enabled": False,
                "forceBasicAuth": True,
                "httpPort": 8082,
            },
        },
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            req = Request("http://127.0.0.1:8082/v2/", method="GET")
            with urlopen(req, timeout=4) as response:
                if response.status == 200:
                    break
        except HTTPError as exc:
            if exc.code == 401:  # authentication challenge is expected
                break
        except (URLError, OSError, TimeoutError):
            pass
        time.sleep(2)
    else:
        raise TimeoutError("Nexus Docker connector on port 8082 did not become ready")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--password-file", required=True, type=Path)
    args = parser.parse_args()
    password = wait_ready()
    args.password_file.write_text(password, encoding="utf-8")
    args.password_file.chmod(0o600)
    print("::add-mask::" + password)
    provision(password)
    print("Legacy Nexus Docker Bearer Token realm and hosted Docker v2 registry ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
