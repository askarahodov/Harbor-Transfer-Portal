#!/usr/bin/env python3
"""Configure only a disposable Nexus instance on the isolated CI Docker network."""

from __future__ import annotations

import base64
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_ROOT = "http://nexus:8081/service/rest/v1"


def _api(method: str, path: str, password: str, payload: object | None = None) -> bytes:
    authorization = base64.b64encode(f"admin:{password}".encode()).decode("ascii")
    headers = {"Authorization": f"Basic {authorization}"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(
        API_ROOT + path,
        headers=headers,
        data=data,
        method=method,
    )
    with urlopen(request, timeout=15) as response:
        return response.read()


def _await_ready(password: str) -> None:
    for _ in range(120):
        try:
            _api("GET", "/status", password)
            return
        except (HTTPError, URLError, TimeoutError, OSError):
            time.sleep(5)
    raise RuntimeError("Disposable Nexus administration endpoint did not become ready")


def _probe_docker_connector(password: str) -> None:
    basic = base64.b64encode(f"admin:{password}".encode()).decode("ascii")
    for mode, headers in (
        ("unauthenticated", {}),
        ("authenticated", {"Authorization": f"Basic {basic}"}),
    ):
        request = Request("http://nexus:5000/v2/", headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                status = response.status
                challenge = response.headers.get("WWW-Authenticate", "")
                server = response.headers.get("Server", "")
                snippet = response.read(250).decode("utf-8", errors="replace")
        except HTTPError as exc:
            status = exc.code
            challenge = exc.headers.get("WWW-Authenticate", "")
            server = exc.headers.get("Server", "")
            snippet = exc.read(250).decode("utf-8", errors="replace")
        # Do not print passwords or authorization headers.
        print(
            f"REAL NEXUS /v2/ probe ({mode}): HTTP {status}; "
            f"server={server[:80]!r}; challenge={challenge[:150]!r}; "
            f"body={snippet[:200]!r}"
        )


def main() -> None:
    password = os.environ.get("NEXUS_E2E_PASSWORD", "")
    if not password:
        raise RuntimeError("NEXUS_E2E_PASSWORD is required at runtime")

    _await_ready(password)
    realms = json.loads(_api("GET", "/security/realms/active", password))
    if not isinstance(realms, list):
        raise RuntimeError("Nexus returned an invalid active realms payload")
    if "DockerToken" not in realms:
        realms.append("DockerToken")
        _api("PUT", "/security/realms/active", password, realms)
    active_realms = json.loads(_api("GET", "/security/realms/active", password))
    print(f"REAL NEXUS active authentication realms: {active_realms!r}")

    # TEST INSTANCE ONLY: enable read-only anonymous access to let the Docker
    # connector advertise its Bearer challenge. This network has no external
    # routes and the Nexus container/volume are discarded after the job.
    _api(
        "PUT",
        "/security/anonymous",
        password,
        {
            "enabled": True,
            "userId": "anonymous",
            "realmName": "NexusAuthorizingRealm",
        },
    )
    anonymous = json.loads(_api("GET", "/security/anonymous", password))
    if anonymous.get("enabled") is not True:
        raise RuntimeError("Disposable Nexus anonymous read access was not enabled")
    print(f"REAL NEXUS disposable anonymous access enabled: {anonymous['enabled']}")

    hosted_repository = {
        "name": "e2e-docker-hosted",
        "online": True,
        "storage": {
            "blobStoreName": "default",
            "strictContentTypeValidation": True,
            "writePolicy": "allow",
        },
        "cleanup": {"policyNames": []},
        "component": {"proprietaryComponents": False},
        "docker": {
            "v1Enabled": False,
            "forceBasicAuth": False,
            "httpPort": 5000,
        },
    }
    _api("POST", "/repositories/docker/hosted", password, hosted_repository)
    configured = json.loads(
        _api("GET", "/repositories/docker/hosted/e2e-docker-hosted", password)
    )
    print(f"REAL NEXUS Docker forceBasicAuth={configured['docker']['forceBasicAuth']}")
    # A Docker connector can need a short initialization period after the REST POST.
    for attempt in range(20):
        request = Request("http://nexus:5000/v2/")
        try:
            with urlopen(request, timeout=5) as response:
                status = response.status
        except HTTPError as exc:
            status = exc.code
        except (URLError, TimeoutError, OSError):
            status = 0
        if status in (200, 401):
            break
        time.sleep(2)
    _probe_docker_connector(password)
    if status not in (200, 401):
        raise RuntimeError(f"Nexus Docker connector returned unexpected HTTP {status}")
    print("REAL NEXUS: configured Docker hosted registry using the REST API")


if __name__ == "__main__":
    main()
