"""Read-only regional Nexus Docker/Helm preflight, run from an authorized network.

No Nexus write APIs, password argv, OCI Helm, credential redirects or TLS bypass.
This is *not* a production GO decision: signed physical handoff / write ACL
must still be accepted and signed off by the regional operator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import yaml

_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_CHART = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\\Z")
_HELM_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,127}\\Z")
_IMAGE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*\Z")
_TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}\Z")
_DIGEST = re.compile(r"(?:sha256:)?[a-f0-9]{64}\Z")
_MAX_INDEX = 4 * 1024 * 1024
_MAX_CHART = 100 * 1024 * 1024
_MAX_VERSIONS = 500


class _UniqueKeysLoader(yaml.SafeLoader):
    """Refuse ambiguous Helm YAML rather than selecting the last mapping key."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        self.flatten_mapping(node)
        mapping: dict = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while reading Helm YAML", node.start_mark,
                    "unhashable mapping key", key_node.start_mark,
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while reading Helm YAML", node.start_mark,
                    "duplicate mapping key", key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _unambiguous_index(payload: bytes, repository: str) -> dict:
    loader = _UniqueKeysLoader(payload)
    try:
        document = loader.get_single_data()
    finally:
        loader.dispose()
    if not isinstance(document, dict) or not isinstance(document.get("entries"), dict):
        raise ValueError("index_invalid")
    seen: set[tuple[str, str]] = set()
    total = 0
    for chart, records in document["entries"].items():
        if not isinstance(chart, str) or not _CHART.fullmatch(chart):
            raise ValueError("index_invalid")
        if not isinstance(records, list) or len(records) > _MAX_VERSIONS:
            raise ValueError("index_invalid")
        for record in records:
            total += 1
            if total > _MAX_VERSIONS:
                raise ValueError("index_invalid")
            if not isinstance(record, dict):
                raise ValueError("index_invalid")
            version = record.get("version")
            digest = record.get("digest")
            urls = record.get("urls")
            if (
                not isinstance(version, str) or not _HELM_VERSION.fullmatch(version)
                or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
                or not isinstance(urls, list) or not urls
                or not isinstance(urls[0], str)
            ):
                raise ValueError("index_invalid")
            filename = f"{chart}-{version}.tgz"
            advertised = urls[0]
            parsed_url = urlsplit(advertised)
            if (
                parsed_url.path not in {
                    filename, "/" + filename,
                    f"/repository/{repository}/{filename}",
                }
                or parsed_url.query or parsed_url.fragment
                or parsed_url.username is not None
                or parsed_url.password is not None
                or "\\\\" in advertised
                or (parsed_url.scheme and parsed_url.scheme not in {"http", "https"})
                or (parsed_url.netloc and parsed_url.path != (
                    f"/repository/{repository}/{filename}"
                ))
            ):
                raise ValueError("index_invalid")
            identity = (chart, version)
            if identity in seen:
                raise ValueError("index_invalid")
            seen.add(identity)
    return document
_ACCEPT_MANIFEST = ", ".join((
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
))


def _valid_origin(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("endpoint_invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https" or not parsed.hostname
        or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
        or parsed.username is not None or parsed.password is not None
    ):
        raise ValueError("endpoint_invalid")
    return value.rstrip("/")


def _name(value: Any, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(code)
    return value


def _credential(entry: dict[str, Any]) -> tuple[str | None, str | None]:
    user = entry.get("username")
    secret = entry.get("password_file")
    if user is None and secret is None:
        return None, None
    if not isinstance(user, str) or not user or not isinstance(secret, str):
        raise ValueError("service_identity_incomplete")
    path = Path(secret)
    if path.is_symlink() or not path.is_file() or (path.stat().st_mode & 0o077):
        raise ValueError("password_file_permissions_invalid")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("password_file_empty")
    return user, value


def _check(name: str, status: str, code: str, http_status: int | None = None) -> dict:
    check: dict[str, Any] = {"check": name, "status": status, "code": code}
    if http_status is not None:
        check["http_status"] = http_status
    return check


def _read_limited(
    client: httpx.Client, url: str, limit: int, *, headers: dict[str, str] | None = None
) -> tuple[int, bytes]:
    with client.stream("GET", url, headers=headers) as response:
        if response.status_code != 200:
            return response.status_code, b""
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError("response_too_large")
        return 200, bytes(data)


def _read_helm(client: httpx.Client, base: str, entry: dict[str, Any], checks: list[dict]) -> None:
    repository = _name(entry.get("repository"), _REPOSITORY, "repository_invalid")
    url = base + "/repository/" + quote(repository) + "/index.yaml"
    status, payload = _read_limited(client, url, _MAX_INDEX)
    if status in {401, 403}:
        checks.append(_check("helm_index", "FAIL", "repository_denied", status))
        return
    if status != 200:
        checks.append(_check("helm_index", "FAIL", "index_unavailable", status))
        return
    try:
        doc = _unambiguous_index(payload, repository)
    except (yaml.YAMLError, UnicodeError, ValueError):
        checks.append(_check("helm_index", "FAIL", "index_invalid"))
        return
    checks.append(_check("helm_index", "PASS", "index_readable", 200))
    chart = entry.get("chart")
    version = entry.get("version")
    if chart is None and version is None:
        checks.append(_check("helm_chart", "UNVERIFIED", "chart_not_selected"))
        return
    chart = _name(chart, _CHART, "chart_invalid")
    version = _name(version, _HELM_VERSION, "version_invalid")
    versions = doc["entries"].get(chart)
    record = next(
        (item for item in versions if item["version"] == version),
        None,
    ) if isinstance(versions, list) else None
    if record is None or not isinstance(record.get("digest"), str) or not _DIGEST.fullmatch(
        record["digest"]
    ):
        checks.append(_check("helm_chart", "FAIL", "chart_or_digest_missing"))
        return
    expected = record["digest"].removeprefix("sha256:")
    if entry.get("expected_sha256") is not None and entry["expected_sha256"] != expected:
        checks.append(_check("helm_chart", "FAIL", "expected_digest_mismatch"))
        return
    urls = record.get("urls")
    filename = f"{chart}-{version}.tgz"
    if not isinstance(urls, list) or not urls or not isinstance(urls[0], str):
        checks.append(_check("helm_chart", "FAIL", "unsafe_chart_url"))
        return
    advertised = urlsplit(urls[0])
    if (
        advertised.path not in {
            filename, "/" + filename, f"/repository/{repository}/{filename}",
        } or advertised.query or advertised.fragment
        or advertised.username is not None or advertised.password is not None
        or "\\" in urls[0]
        or (advertised.scheme and advertised.scheme not in {"http", "https"})
        or (advertised.netloc and advertised.path != f"/repository/{repository}/{filename}")
    ):
        checks.append(_check("helm_chart", "FAIL", "unsafe_chart_url"))
        return
    # Ignore advertised host completely: never send credentials off the approved origin.
    path = base + "/repository/" + quote(repository) + "/" + quote(filename)
    download_status, package = _read_limited(client, path, _MAX_CHART)
    if download_status != 200:
        checks.append(_check("helm_chart", "FAIL", "package_unavailable", download_status))
        return
    if hashlib.sha256(package).hexdigest() != expected:
        checks.append(_check("helm_chart", "FAIL", "package_digest_mismatch"))
        return
    checks.append(_check("helm_chart", "PASS", "package_sha256_verified", 200))


def _read_docker(
    client: httpx.Client, base: str, entry: dict[str, Any], checks: list[dict]
) -> None:
    image = entry.get("image")
    tag = entry.get("tag")
    if image is None and tag is None:
        checks.append(_check("docker_manifest", "UNVERIFIED", "image_not_selected"))
        return
    image = _name(image, _IMAGE, "image_invalid")
    tag = _name(tag, _TAG, "tag_invalid")
    response = client.head(
        f"{base}/v2/{quote(image, safe='/')}/manifests/{quote(tag)}",
        headers={"Accept": _ACCEPT_MANIFEST},
    )
    if response.status_code == 401:
        checks.append(_check("docker_manifest", "UNVERIFIED", "bearer_or_acl_challenge", 401))
        return
    if response.status_code in {403, 404}:
        checks.append(_check("docker_manifest", "FAIL", "manifest_denied_or_missing",
                             response.status_code))
        return
    if response.status_code != 200:
        checks.append(_check("docker_manifest", "FAIL", "manifest_unavailable",
                             response.status_code))
        return
    digest = response.headers.get("Docker-Content-Digest", "")
    if _DIGEST.fullmatch(digest) is None:
        checks.append(_check("docker_manifest", "FAIL", "digest_missing"))
        return
    if entry.get("expected_digest") and entry["expected_digest"] != digest:
        checks.append(_check("docker_manifest", "FAIL", "digest_mismatch"))
        return
    checks.append(_check("docker_manifest", "PASS", "digest_verified", 200))


def _probe(
    entry: dict[str, Any], *,
    client_factory: Any = httpx.Client,
) -> dict[str, Any]:
    # No raw URLs, account names, paths or exception strings in the evidence report.
    record: dict[str, Any] = {
        "id": str(entry.get("id", ""))[:64],
        "contour": entry.get("contour"),
        "format": entry.get("format"),
        "repository": str(entry.get("repository", ""))[:128],
        "kind": entry.get("kind"),
        "checks": [],
    }
    checks = record["checks"]
    try:
        if not isinstance(entry.get("id"), str) or _REPOSITORY.fullmatch(entry["id"]) is None:
            raise ValueError("id_invalid")
        if entry.get("contour") not in {"SOURCE", "TARGET"}:
            raise ValueError("contour_invalid")
        if entry.get("format") not in {"docker", "helm"}:
            raise ValueError("format_invalid")
        if entry.get("kind") not in {"hosted", "proxy", "group"}:
            raise ValueError("kind_invalid")
        _name(entry.get("repository"), _REPOSITORY, "repository_invalid")
        origin = _valid_origin(entry.get("endpoint"))
        ca = entry.get("ca_file")
        if ca is not None and (not isinstance(ca, str) or not Path(ca).is_file()):
            raise ValueError("ca_file_missing")
        username, password = _credential(entry)
        args = {
            "verify": ca if ca else True, "timeout": httpx.Timeout(15),
            "follow_redirects": False, "trust_env": False,
        }
        if entry["format"] == "docker":
            with client_factory(**args) as anonymous:
                greeting = anonymous.get(origin + "/v2/")
            scheme = greeting.headers.get("WWW-Authenticate", "").split(" ", 1)[0].lower()
            if greeting.status_code == 401 and scheme not in {"bearer", "basic"}:
                checks.append(_check("docker_endpoint", "FAIL", "invalid_auth_challenge", 401))
                return record
            if greeting.status_code == 200 and greeting.headers.get(
                "Docker-Distribution-API-Version", ""
            ).lower() != "registry/2.0":
                checks.append(_check("docker_endpoint", "FAIL", "not_docker_registry_v2", 200))
                return record
            if greeting.status_code not in {200, 401}:
                checks.append(_check("docker_endpoint", "FAIL", "v2_endpoint_unavailable",
                                     greeting.status_code))
                return record
            checks.append(_check("docker_endpoint", "PASS", "docker_v2_reachable",
                                 greeting.status_code))
        auth = httpx.BasicAuth(username, password) if username is not None else None
        with client_factory(auth=auth, **args) as client:
            if entry["format"] == "docker":
                response = client.get(origin + "/v2/")
                if response.status_code == 200:
                    checks.append(_check("docker_identity", "PASS", "auth_or_anonymous_ok", 200))
                elif response.status_code == 401 and response.headers.get(
                    "WWW-Authenticate", ""
                ).lower().startswith("bearer"):
                    checks.append(_check("docker_identity", "UNVERIFIED",
                                         "token_realm_needs_portal_probe", 401))
                else:
                    checks.append(_check("docker_identity", "FAIL", "auth_failed",
                                         response.status_code))
                    return record
                _read_docker(client, origin, entry, checks)
            else:
                _read_helm(client, origin, entry, checks)
    except ValueError as exc:
        checks.append(_check("configuration_or_response", "FAIL", str(exc)))
    except (httpx.HTTPError, OSError, UnicodeError):
        checks.append(_check("network_or_tls", "FAIL", "connection_or_ca_failed"))
    return record


def run_document(document: dict[str, Any], *, client_factory: Any = httpx.Client) -> dict:
    if not isinstance(document, dict) or not all(
        isinstance(document.get(key), str) and document[key].strip()
        for key in ("environment", "operator", "change_id", "commit_sha")
    ):
        raise ValueError("approval_and_commit_required")
    if _DIGEST.fullmatch(document["commit_sha"]) is not None:
        # SHA-256 is not a commit identifier; exact 40-char Git SHA expected.
        raise ValueError("commit_sha_invalid")
    if re.fullmatch(r"[a-f0-9]{40}", document["commit_sha"]) is None:
        raise ValueError("commit_sha_invalid")
    probes = document.get("probes")
    if not isinstance(probes, list) or not probes:
        raise ValueError("probes_required")
    if len(probes) > 50:
        raise ValueError("too_many_probes")
    entries = [_probe(x, client_factory=client_factory) for x in probes]
    all_statuses = [
        check["status"] for entry in entries for check in entry["checks"]
    ]
    return {
        "schema": 1, "generated_at": datetime.now(UTC).isoformat(),
        "environment": document["environment"], "operator": document["operator"],
        "change_id": document["change_id"], "commit_sha": document["commit_sha"],
        "entries": entries,
        "preflight_status": (
            "FAIL" if "FAIL" in all_statuses else
            "INCOMPLETE" if "UNVERIFIED" in all_statuses else "PASS"
        ),
        "release_verdict": "NO-GO",
        "release_reason": (
            "Regional signed handoff, write ACL, recovery and approver evidence pending"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Nexus regional preflight")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        document = json.loads(args.config.read_text(encoding="utf-8"))
        result = run_document(document)
        output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        # O_EXCL prevents overwriting operator evidence or following symlinks.
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(output)
    except (ValueError, OSError):
        print(
            "Preflight validation or report creation failed (details suppressed)",
            file=sys.stderr,
        )
        return 2
    print("Sanitized read-only Nexus preflight report created; release remains NO-GO")
    return 0 if result["preflight_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
