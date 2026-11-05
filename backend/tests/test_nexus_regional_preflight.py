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
    assert report["entries"][0]["checks"][-1]["code"] == "unsafe_chart_url"
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
