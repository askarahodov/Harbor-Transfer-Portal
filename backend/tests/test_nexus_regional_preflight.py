"""Read-only regional preflight: multi-repo ACL, transport, redaction and fail-closed."""

from __future__ import annotations

import hashlib
import json
import os

import httpx
import pytest
import yaml

from integration.nexus_regional_preflight import (
    _load_config,
    _probe,
    _publish_report,
    main,
    run_document,
)

HEAD = "c55fc355823306ada9b5f9536fca4508ff675f2d"
DIGEST = "sha256:" + "a" * 64


def _document(probes):
    return {
        "environment": "authorized-test-region",
        "operator": "regional-operator",
        "change_id": "CHG-TEST-123",
        "commit_sha": HEAD,
        "probes": probes,
    }


def _docker(name, *, image="team/app"):
    return {
        "id": name, "contour": "SOURCE", "format": "docker",
        "kind": "hosted", "repository": name,
        "endpoint": f"https://{name}.example.test",
        "username": "svc", "password_file": None,
        "image": image, "tag": "1.0.0", "expected_digest": DIGEST,
    }


def _anonymous_docker(name):
    row = _docker(name)
    row.pop("username")
    row.pop("password_file")
    return row


def test_independent_docker_connector_results_and_no_write_or_harbor():
    seen: list[httpx.Request] = []

    def handler(req):
        seen.append(req)
        if req.url.path == "/v2/":
            return httpx.Response(200, headers={
                "Docker-Distribution-API-Version": "registry/2.0",
            })
        if req.url.host == "docker-denied.example.test":
            return httpx.Response(403)
        return httpx.Response(200, headers={"Docker-Content-Digest": DIGEST})

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert kwargs["verify"] is True
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    report = run_document(_document([
        _anonymous_docker("docker-source"), _anonymous_docker("docker-denied"),
        _anonymous_docker("docker-target"),
    ]), client_factory=factory)
    assert report["release_verdict"] == "NO-GO"
    assert report["preflight_status"] == "FAIL"
    assert [x["checks"][-1]["status"] for x in report["entries"]] == [
        "PASS", "FAIL", "PASS",
    ]
    assert {req.method for req in seen} == {"GET", "HEAD"}
    assert all(req.url.path.startswith("/v2/") for req in seen)
    assert all("harbor" not in str(req.url) for req in seen)


def test_helm_acl_denied_then_allowed_with_digest_and_safe_urls():
    blob = b"chart-payload"
    digest = hashlib.sha256(blob).hexdigest()
    seen: list[httpx.Request] = []

    def handler(req):
        seen.append(req)
        if req.url.path == "/repository/helm-private/index.yaml":
            return httpx.Response(403, text="secret-realm-must-not-leak")
        if req.url.path == "/repository/helm-team/index.yaml":
            return httpx.Response(200, text=yaml.safe_dump({
                "entries": {"demo": [{
                    "version": "1.0.0", "digest": digest,
                    "urls": ["https://irrelevant.example.test/repository/helm-team/demo-1.0.0.tgz"],
                }]}
            }))
        if req.url.path == "/repository/helm-team/demo-1.0.0.tgz":
            return httpx.Response(200, content=blob)
        return httpx.Response(404)

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    base = {
        "contour": "SOURCE", "format": "helm", "kind": "hosted",
        "endpoint": "https://nexus.example.test", "chart": "demo", "version": "1.0.0",
    }
    report = run_document(_document([
        {**base, "id": "private", "repository": "helm-private"},
        {**base, "id": "team", "repository": "helm-team"},
    ]), client_factory=factory)
    assert report["preflight_status"] == "FAIL"
    assert [entry["checks"][-1]["code"] for entry in report["entries"]] == [
        "repository_denied", "package_sha256_verified",
    ]
    assert len(seen) == 3
    assert all(req.url.host == "nexus.example.test" for req in seen)
    assert all(req.method == "GET" for req in seen)
    assert "secret-realm-must-not-leak" not in json.dumps(report)


@pytest.mark.parametrize("endpoint", [
    "http://nexus.example.test", "https://nexus.example.test/evil",
    "https://username:password@nexus.example.test",
    "https://nexus.example.test?token=secret",
])
def test_invalid_endpoint_never_makes_network_calls(endpoint):
    count = {"n": 0}

    def factory(**_kwargs):
        count["n"] += 1
        raise AssertionError("must not open network")

    probe = {**_anonymous_docker("docker-source"), "endpoint": endpoint}
    result = _probe(probe, client_factory=factory)
    assert result["checks"][0]["code"] == "endpoint_invalid"
    assert count["n"] == 0
    assert "password" not in json.dumps(result)


def test_html_200_is_not_a_docker_registry():
    hits: list[str] = []

    def handler(req):
        hits.append(req.url.path)
        return httpx.Response(200, text="<html>Sign in</html>")

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    report = run_document(_document([_anonymous_docker("docker-a")]),
                          client_factory=factory)
    assert report["preflight_status"] == "FAIL"
    assert report["entries"][0]["checks"][0]["code"] == "not_docker_registry_v2"
    assert hits == ["/v2/"]


def test_redirect_and_bad_challenge_fail_closed_without_following():
    urls: list[str] = []

    def handler(req):
        urls.append(str(req.url))
        return httpx.Response(302, headers={
            "Location": "https://attacker.invalid/capture",
            "Set-Cookie": "token=private",
        })

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    report = run_document(_document([_anonymous_docker("docker-source")]),
                          client_factory=factory)
    assert report["preflight_status"] == "FAIL"
    assert report["entries"][0]["checks"][0]["code"] == "v2_endpoint_unavailable"
    assert len(urls) == 1
    assert "attacker.invalid" not in "\n".join(urls)
    assert "private" not in json.dumps(report)


def test_malformed_helm_index_and_foreign_chart_url_fail_without_download():
    seen: list[str] = []

    def handler(req):
        seen.append(req.url.path)
        return httpx.Response(200, text=yaml.safe_dump({"entries": {
            "demo": [{
                "version": "1.0.0", "digest": "a" * 64,
                "urls": ["https://attacker.invalid/evil.tgz"],
            }],
        }}))

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    report = run_document(_document([{
        "id": "helm-a", "contour": "TARGET", "format": "helm",
        "kind": "hosted", "endpoint": "https://nexus.example.test",
        "repository": "helm-team", "chart": "demo", "version": "1.0.0",
    }]), client_factory=factory)
    # The whole index is now validated before selecting a chart or .tgz URL.
    assert report["entries"][0]["checks"][-1]["code"] == "index_invalid"
    assert seen == ["/repository/helm-team/index.yaml"]


def test_no_credentials_in_config_and_missing_change_blocked():
    with pytest.raises(ValueError, match="approval_and_commit_required"):
        run_document({"probes": []})
    with pytest.raises(ValueError, match="commit_sha_invalid"):
        run_document({**_document([_anonymous_docker("docker-a")]),
                      "commit_sha": "not-a-sha"})
    assert _probe(_docker("docker-a"))["checks"][0]["code"] == "service_identity_incomplete"


def test_no_secrets_reported_on_transport_failure():
    def factory(**_kwargs):
        raise httpx.ConnectError("password=must-never-leak")

    report = run_document(_document([_anonymous_docker("docker-a")]),
                          client_factory=factory)
    assert report["preflight_status"] == "FAIL"
    assert "must-never-leak" not in json.dumps(report)
    assert report["entries"][0]["checks"][0]["code"] == "connection_or_ca_failed"


@pytest.mark.parametrize("bad_index", [
    # Earlier chart/version gets erased by default PyYAML mapping behavior.
    "entries:\n  demo:\n    - version: 1.0.0\n      digest: "
    + "a" * 64 + "\n      urls: [demo-1.0.0.tgz]\n  demo: []\n",
    # A duplicate checksum conceals a conflicting signed artifact.
    "entries:\n  demo:\n    - version: 1.0.0\n      digest: "
    + "a" * 64 + "\n      digest: " + "b" * 64
    + "\n      urls: [demo-1.0.0.tgz]\n",
    # Duplicate versions are valid YAML but ambiguous as Helm inventory.
    yaml.safe_dump({"entries": {"demo": [
        {"version": "1.0.0", "digest": "a" * 64,
         "urls": ["demo-1.0.0.tgz"]},
        {"version": "1.0.0", "digest": "b" * 64,
         "urls": ["demo-1.0.0.tgz"]},
    ]}}),
    # Malformed versions must not be silently ignored.
    yaml.safe_dump({"entries": {"demo": [
        {"version": "1.0.0", "digest": "invalid",
         "urls": ["demo-1.0.0.tgz"]},
    ]}}),
])
def test_preflight_helm_invalid_index_fails_without_chart_download_and_isolated(
    bad_index,
):
    calls: list[str] = []
    good_payload = b"good-chart"
    good_digest = hashlib.sha256(good_payload).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/repository/helm-bad/index.yaml":
            return httpx.Response(200, text=bad_index)
        if request.url.path == "/repository/helm-good/index.yaml":
            return httpx.Response(200, text=yaml.safe_dump({"entries": {"demo": [{
                "version": "1.0.0", "digest": good_digest,
                "urls": ["demo-1.0.0.tgz"],
            }]}}))
        if request.url.path == "/repository/helm-good/demo-1.0.0.tgz":
            return httpx.Response(200, content=good_payload)
        raise AssertionError("bad repository must not trigger a chart download")

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    base = {
        "contour": "SOURCE", "format": "helm", "kind": "hosted",
        "endpoint": "https://nexus.example.test", "chart": "demo",
        "version": "1.0.0",
    }
    report = run_document(_document([
        {**base, "id": "bad", "repository": "helm-bad"},
        {**base, "id": "good", "repository": "helm-good",
         "expected_sha256": good_digest},
    ]), client_factory=factory)
    assert report["preflight_status"] == "FAIL"
    assert report["release_verdict"] == "NO-GO"
    assert report["entries"][0]["checks"][-1]["code"] == "index_invalid"
    assert report["entries"][1]["checks"][-1]["code"] == "package_sha256_verified"
    assert len(calls) == 3
    assert all("helm-bad/demo" not in url for url in calls)


@pytest.mark.parametrize("invalid_url", [
    "https://external.invalid/stolen.tgz",
    "https://example.invalid/repository/another/chart-2.0.0.tgz",
    "../chart-2.0.0.tgz",
    "chart-2.0.0.tgz?key=secret",
])
def test_preflight_rejects_unsafe_url_in_unselected_chart(invalid_url):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/repository/helm-team/index.yaml":
            return httpx.Response(200, text=yaml.safe_dump({
                "entries": {
                    "demo": [{"version": "1.0.0", "digest": "a" * 64,
                              "urls": ["demo-1.0.0.tgz"]}],
                    "chart": [{"version": "2.0.0", "digest": "b" * 64,
                               "urls": [invalid_url]}],
                },
            }))
        raise AssertionError("invalid catalog must not trigger .tgz download")

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    report = run_document(_document([{
        "id": "helm-team", "contour": "SOURCE", "format": "helm",
        "kind": "hosted", "repository": "helm-team",
        "endpoint": "https://nexus.example.test",
        "chart": "demo", "version": "1.0.0",
    }]), client_factory=factory)
    assert report["preflight_status"] == "FAIL"
    assert report["entries"][0]["checks"][-1]["code"] == "index_invalid"
    assert seen == ["/repository/helm-team/index.yaml"]


def test_preflight_accepts_valid_helm_build_metadata_version():
    body = b"chart bytes"
    digest = hashlib.sha256(body).hexdigest()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/index.yaml"):
            return httpx.Response(200, text=yaml.safe_dump({
                "entries": {"demo": [{
                    "version": "1.2.3+build.4", "digest": digest,
                    "urls": ["demo-1.2.3+build.4.tgz"],
                }]},
            }))
        return httpx.Response(200, content=body)

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    report = run_document(_document([{
        "id": "helm-team", "contour": "SOURCE", "format": "helm",
        "kind": "hosted", "repository": "helm-team",
        "endpoint": "https://nexus.example.test", "chart": "demo",
        "version": "1.2.3+build.4", "expected_sha256": digest,
    }]), client_factory=factory)
    assert report["preflight_status"] == "PASS"
    assert report["entries"][0]["checks"][-1]["code"] == "package_sha256_verified"
    assert len(seen) == 2


@pytest.mark.parametrize("secondary_url", [
    "https://malicious.invalid/repository/elsewhere/demo-1.0.0.tgz",
    "https://[invalid",
    "demo-1.0.0.tgz?secret=leak",
    99,
])
def test_preflight_rejects_untrusted_secondary_chart_urls(secondary_url):
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        assert request.url.path == "/repository/helm-team/index.yaml"
        return httpx.Response(200, text=yaml.safe_dump({
            "entries": {"demo": [{
                "version": "1.0.0", "digest": "a" * 64,
                "urls": ["demo-1.0.0.tgz", secondary_url],
            }]},
        }))

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    report = run_document(_document([{
        "id": "helm-team", "contour": "SOURCE", "format": "helm",
        "kind": "hosted", "endpoint": "https://nexus.example.test",
        "repository": "helm-team", "chart": "demo", "version": "1.0.0",
    }]), client_factory=factory)
    assert report["preflight_status"] == "FAIL"
    assert report["release_verdict"] == "NO-GO"
    assert report["entries"][0]["checks"][-1]["code"] == "index_invalid"
    assert len(requests) == 1
    assert "malicious.invalid" not in requests[0]


@pytest.mark.parametrize("invalid_probe,expected_code", [
    ({"id": "svc-token=secret-do-not-echo", "contour": "SOURCE",
      "format": "helm", "kind": "hosted", "repository": "helm-team"},
     "id_invalid"),
    ({"id": "helm-team", "contour": "SOURCE",
      "format": "helm", "kind": "hosted",
      "repository": "secret/repository?access_token=sensitive"},
     "repository_invalid"),
    ({"id": "helm-team", "contour": "SOURCE",
      "format": {"secret": "secret-do-not-echo"}, "kind": "hosted",
      "repository": "helm-team"},
     "format_invalid"),
    (["secret-do-not-echo"], "probe_invalid"),
])
def test_preflight_never_echoes_invalid_config_in_evidence(
    invalid_probe, expected_code,
):
    network_calls = []

    def factory(**kwargs):
        network_calls.append(kwargs)
        raise AssertionError("invalid probe must not open a client")

    report = run_document(
        _document([invalid_probe]), client_factory=factory,
    )
    entry = report["entries"][0]
    assert report["preflight_status"] == "FAIL"
    assert report["release_verdict"] == "NO-GO"
    assert entry["checks"][0]["code"] == expected_code
    assert "secret-do-not-echo" not in json.dumps(report)
    assert "sensitive" not in json.dumps(report)
    assert not network_calls


@pytest.mark.parametrize("field,unsafe", [
    ("environment", "region?token=secret-do-not-echo"),
    ("operator", "https://credentials.example.test/password"),
    ("change_id", "CHG-1\\nAuthorization: Bearer private"),
    ("environment", "x" * 129),
])
def test_preflight_rejects_unsafe_evidence_header_before_any_probe(field, unsafe):
    attempted = []

    def factory(**kwargs):
        attempted.append(kwargs)
        raise AssertionError("unsafe evidence header must not start requests")

    document = _document([_anonymous_docker("docker-source")])
    document[field] = unsafe
    with pytest.raises(ValueError, match="evidence_identifier_invalid") as exc:
        run_document(document, client_factory=factory)
    assert "secret-do-not-echo" not in str(exc.value)
    assert "Bearer private" not in str(exc.value)
    assert not attempted


def test_preflight_valid_approval_identifiers_remain_in_evidence():
    document = _document([{
        "id": "helm-read", "contour": "SOURCE", "format": "helm",
        "kind": "hosted", "repository": "helm-team",
        "endpoint": "https://nexus.example.test",
    }])
    document.update({
        "environment": "REGION-WEST_2",
        "operator": "svc-acceptance",
        "change_id": "CHG.2026-11-05",
    })

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(403)
        ), **kwargs)

    report = run_document(document, client_factory=factory)
    assert report["preflight_status"] == "FAIL"
    assert report["release_verdict"] == "NO-GO"
    assert report["environment"] == "REGION-WEST_2"
    assert report["operator"] == "svc-acceptance"
    assert report["change_id"] == "CHG.2026-11-05"


@pytest.mark.parametrize("unsafe", ["symlink", "directory", "oversize", "invalid_utf8"])
def test_preflight_cli_rejects_untrusted_config_before_report(
    tmp_path, monkeypatch, capsys, unsafe,
):
    import sys
    config = tmp_path / "preflight.json"
    if unsafe == "symlink":
        actual = tmp_path / "private.json"
        actual.write_text('{"secret":"not-for-logs"}', encoding="utf-8")
        config.symlink_to(actual)
    elif unsafe == "directory":
        config.mkdir()
    elif unsafe == "oversize":
        config.write_bytes(b" " * (1024 * 1024 + 1))
    else:
        config.write_bytes(b"\\xff\\xfe")
        # Actual invalid UTF-8 bytes rather than ASCII escape text.
        config.write_bytes(bytes([255, 254]))
    output = tmp_path / "evidence.json"
    monkeypatch.setattr(sys, "argv", [
        "nexus_regional_preflight", "--config", str(config),
        "--output", str(output),
    ])
    assert main() == 2
    assert not output.exists()
    assert "not-for-logs" not in capsys.readouterr().err
    assert list(tmp_path.glob(".nexus-preflight-*.tmp")) == []


def test_preflight_config_small_regular_file_loads(tmp_path):
    path = tmp_path / "minimal.json"
    path.write_text('{"environment":"REGION-WEST"}', encoding="utf-8")
    assert _load_config(path) == {"environment": "REGION-WEST"}


def test_preflight_evidence_private_atomic_no_overwrite(tmp_path):
    path = tmp_path / "evidence.json"
    _publish_report(path, '{"release_verdict":"NO-GO"}\\n')
    assert path.read_text(encoding="utf-8") == '{"release_verdict":"NO-GO"}\\n'
    assert os.stat(path).st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        _publish_report(path, '{"release_verdict":"GO"}\\n')
    assert '"NO-GO"' in path.read_text(encoding="utf-8")
    assert not list(tmp_path.glob(".nexus-preflight-*.tmp"))


def test_preflight_evidence_does_not_replace_symlink_or_leave_stage(tmp_path):
    target = tmp_path / "protected.json"
    target.write_text("original", encoding="utf-8")
    output = tmp_path / "evidence.json"
    output.symlink_to(target)
    with pytest.raises(FileExistsError):
        _publish_report(output, "tampered")
    assert target.read_text(encoding="utf-8") == "original"
    assert output.is_symlink()
    assert not list(tmp_path.glob(".nexus-preflight-*.tmp"))


def test_preflight_evidence_publish_failure_cleans_staged_file(tmp_path, monkeypatch):
    from integration import nexus_regional_preflight

    def deny_link(*_args, **_kwargs):
        raise OSError("simulated destination filesystem error")

    monkeypatch.setattr(nexus_regional_preflight.os, "link", deny_link)
    output = tmp_path / "evidence.json"
    with pytest.raises(OSError):
        _publish_report(output, "never published")
    assert not output.exists()
    assert not list(tmp_path.glob(".nexus-preflight-*.tmp"))


@pytest.mark.parametrize("invalid_secret", [
    "symlink", "directory", "world_readable", "oversize", "invalid_utf8", "empty",
])
def test_regional_service_secret_is_bounded_and_never_followed_or_reported(
    tmp_path, invalid_secret,
):
    path = tmp_path / "service-password"
    if invalid_secret == "symlink":
        real = tmp_path / "actual-secret"
        real.write_text("private-password-leak", encoding="utf-8")
        real.chmod(0o600)
        path.symlink_to(real)
    elif invalid_secret == "directory":
        path.mkdir(mode=0o700)
    elif invalid_secret == "world_readable":
        path.write_text("private-password-leak", encoding="utf-8")
        path.chmod(0o644)
    elif invalid_secret == "oversize":
        path.write_bytes(b"x" * (16 * 1024 + 1))
        path.chmod(0o600)
    elif invalid_secret == "invalid_utf8":
        path.write_bytes(bytes([255, 254]))
        path.chmod(0o600)
    else:
        path.write_bytes(b"  ")
        path.chmod(0o600)

    seen = []

    def factory(**kwargs):
        seen.append(kwargs)
        raise AssertionError("invalid secret must not start any HTTP requests")

    entry = _docker("docker-source")
    entry["password_file"] = str(path)
    result = _probe(entry, client_factory=factory)
    expected = (
        "password_file_too_large" if invalid_secret == "oversize"
        else "password_file_empty" if invalid_secret == "empty"
        else "password_file_permissions_invalid"
    )
    assert result["checks"][0]["code"] == expected
    assert result["checks"][0]["status"] == "FAIL"
    assert "private-password-leak" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)
    assert not seen


@pytest.mark.parametrize("unsafe_url", [
    "https://[malformed", "https://nexus.example.test:not-a-port",
])
def test_preflight_endpoint_parse_errors_are_redacted_and_block_network(unsafe_url):
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        raise AssertionError("invalid endpoint must not open client")

    entry = _anonymous_docker("docker-source")
    entry["endpoint"] = unsafe_url
    result = _probe(entry, client_factory=factory)
    assert result["checks"][0]["code"] == "endpoint_invalid"
    assert unsafe_url not in json.dumps(result)
    assert not calls


def test_preflight_never_emits_untrusted_parser_exception_text(monkeypatch):
    from integration import nexus_regional_preflight

    def malicious_parser(_url):
        raise ValueError("password=private-secret-in-exception")

    monkeypatch.setattr(nexus_regional_preflight, "urlsplit", malicious_parser)
    result = _probe(_anonymous_docker("docker-source"))
    assert result["checks"][0]["code"] == "endpoint_invalid"
    assert "private-secret-in-exception" not in json.dumps(result)


def test_read_only_search_page_two_uses_separate_identity_and_scoped_repository(
    tmp_path,
):
    """REST Search must not reuse Docker credentials or trust another repo's hit."""
    import base64

    secret = tmp_path / "search-password"
    secret.write_text("search-secret", encoding="utf-8")
    secret.chmod(0o600)
    calls: list[httpx.Request] = []
    search_calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.host == "nexus-ui.example.test":
            search_calls.append(req)
            assert req.method == "GET"
            assert req.url.path == "/service/rest/v1/search"
            assert req.url.params["repository"] == "docker-proxy"
            assert req.url.params["format"] == "docker"
            assert req.url.params["name"] == "baseimage"
            assert req.headers.get("authorization") == (
                "Basic " + base64.b64encode(b"search-reader:search-secret").decode()
            )
            if "continuationToken" not in req.url.params:
                return httpx.Response(200, json={
                    "items": [
                        {"repository": "other-private", "format": "docker",
                         "name": "baseimage", "version": "1.0.0"},
                        *[{"repository": "docker-proxy", "format": "docker",
                           "name": f"other-{i}", "version": "1.0.0"}
                          for i in range(25)],
                    ],
                    "continuationToken": "NEXT+1/==",
                })
            assert req.url.params["continuationToken"] == "NEXT+1/=="
            return httpx.Response(200, json={"items": [
                {"repository": "docker-proxy", "format": "docker",
                 "name": "baseimage", "version": "1.0.0"},
            ], "continuationToken": None})
        assert req.url.host == "docker-proxy.example.test"
        assert req.headers.get("authorization") is None
        if req.url.path == "/v2/":
            return httpx.Response(200, headers={
                "Docker-Distribution-API-Version": "registry/2.0",
            })
        assert req.method == "HEAD"
        return httpx.Response(200, headers={"Docker-Content-Digest": DIGEST})

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert kwargs["verify"] is True
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    probe = _anonymous_docker("docker-proxy")
    probe["image"] = "baseimage"
    probe["search"] = {
        "endpoint": "https://nexus-ui.example.test",
        "repository": "docker-proxy",
        "username": "search-reader",
        "password_file": str(secret),
    }
    report = run_document(_document([probe]), client_factory=factory)
    assert report["preflight_status"] == "PASS"
    assert report["release_verdict"] == "NO-GO"
    assert [x["code"] for x in report["entries"][0]["checks"]] == [
        "docker_v2_reachable", "auth_or_anonymous_ok", "digest_verified",
        "search_image_tag_visible",
    ]
    assert len(search_calls) == 2
    assert all(req.method in {"GET", "HEAD"} for req in calls)
    assert "search-secret" not in json.dumps(report)


@pytest.mark.parametrize("response,expected", [
    (httpx.Response(403, text="private-repo-secret"), "search_access_denied"),
    (httpx.Response(302, headers={
        "Location": "https://attacker.invalid/steal",
    }), "search_unavailable"),
    (httpx.Response(200, text="<html>sign-in</html>"), "search_response_invalid"),
    (httpx.Response(200, json={"items": [
        {"repository": "someone-else", "format": "docker",
         "name": "team/app", "version": "1.0.0"},
        {"repository": "docker-source", "format": "helm",
         "name": "team/app", "version": "1.0.0"},
    ]}), "search_image_tag_not_visible"),
])
def test_search_denial_redirect_invalid_response_and_foreign_repository_isolated(
    response, expected,
):
    urls: list[str] = []

    def handler(req):
        urls.append(str(req.url))
        if req.url.host == "nexus-ui.example.test":
            return response
        if req.url.path == "/v2/":
            return httpx.Response(200, headers={
                "Docker-Distribution-API-Version": "registry/2.0",
            })
        return httpx.Response(200, headers={"Docker-Content-Digest": DIGEST})

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    denied = _anonymous_docker("docker-source")
    denied["search"] = {
        "endpoint": "https://nexus-ui.example.test",
        "repository": "docker-source",
    }
    allowed = _anonymous_docker("docker-allowed")
    report = run_document(_document([denied, allowed]), client_factory=factory)
    assert report["preflight_status"] == "FAIL"
    assert report["entries"][0]["checks"][-1]["code"] == expected
    assert report["entries"][1]["checks"][-1]["code"] == "digest_verified"
    assert "private-repo-secret" not in json.dumps(report)
    assert not any("attacker.invalid" in url for url in urls)


@pytest.mark.parametrize("token,code,calls", [
    ("repeated", "search_cursor_invalid", 2),
    ("unsafe\nHeader: secret", "search_cursor_invalid", 1),
    ("all-pages", "search_page_limit_reached", 8),
])
def test_search_pagination_fail_closed_and_bounded(token, code, calls):
    search_requests: list[httpx.Request] = []

    def handler(req):
        if req.url.host == "nexus-ui.example.test":
            search_requests.append(req)
            cursor = req.url.params.get("continuationToken")
            next_cursor = (
                "first" if cursor is None
                else "first" if token == "repeated"
                else token if token.startswith("unsafe")
                else f"page-{len(search_requests)}"
            )
            return httpx.Response(200, json={
                "items": [], "continuationToken": next_cursor,
            })
        if req.url.path == "/v2/":
            return httpx.Response(200, headers={
                "Docker-Distribution-API-Version": "registry/2.0",
            })
        return httpx.Response(200, headers={"Docker-Content-Digest": DIGEST})

    def factory(**kwargs):
        assert kwargs["trust_env"] is False
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    probe = _anonymous_docker("docker-source")
    probe["search"] = {
        "endpoint": "https://nexus-ui.example.test",
        "repository": "docker-source",
    }
    report = run_document(_document([probe]), client_factory=factory)
    assert report["entries"][0]["checks"][-1]["code"] == code
    assert report["entries"][0]["checks"][-1]["status"] == (
        "UNVERIFIED" if token == "all-pages" else "FAIL"
    )
    assert len(search_requests) == calls
    assert all(r.url.host == "nexus-ui.example.test" for r in search_requests)
    assert "secret" not in json.dumps(report)


@pytest.mark.parametrize("search,expected", [
    ({"endpoint": "https://nexus-ui.example.test",
      "repository": "other-repository"}, "search_repository_mismatch"),
    ({"endpoint": "https://user:password@nexus-ui.example.test",
      "repository": "docker-source"}, "endpoint_invalid"),
    ({"endpoint": "https://nexus-ui.example.test?token=secret",
      "repository": "docker-source"}, "endpoint_invalid"),
    (["unsafe"], "search_config_invalid"),
])
def test_invalid_search_configuration_prevents_all_network_calls(search, expected):
    attempts = []

    def factory(**kwargs):
        attempts.append(kwargs)
        raise AssertionError("invalid search config must not open HTTP client")

    probe = _anonymous_docker("docker-source")
    probe["search"] = search
    report = run_document(_document([probe]), client_factory=factory)
    assert report["preflight_status"] == "FAIL"
    assert report["entries"][0]["checks"][0]["code"] == expected
    assert attempts == []
    assert "password" not in json.dumps(report)
    assert "secret" not in json.dumps(report)
