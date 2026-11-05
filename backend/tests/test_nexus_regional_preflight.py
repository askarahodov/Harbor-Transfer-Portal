"""Read-only regional preflight: multi-repo ACL, transport, redaction and fail-closed."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest
import yaml

from integration.nexus_regional_preflight import _probe, run_document

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
