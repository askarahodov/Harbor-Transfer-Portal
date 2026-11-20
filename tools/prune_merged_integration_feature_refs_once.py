#!/usr/bin/env python3
"""One-shot cleanup of merged feature branches retained by the Nexus RC integration branch.

PR runs audit exact refs and perform an atomic CAS dry-run only. The only
destructive mode is a push to main after this exact cleanup PR is merged.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from urllib.parse import quote

from atomic_ref_cleanup import delete_refs

REPO = "askarahodov/Harbor-Transfer-Portal"
INTEGRATION = "integration/nexus-oss-release-candidate-451"
OWN = "chore/513-retire-merged-integration-feature-refs-once"
ENABLE = "issue-513-retire-merged-integration-refs"
AUDIT_BASE = "8766a7a069c7b56c5534cc9f4385a9ac349e5ae4"

TARGETS = [
    {
        "branch": "feat/nexus-compact-image-selector-20261106",
        "sha": "cab4cb4d0043c1714c95e1f9f08681932704a6b0",
        "pr": 458,
        "pr_head_sha": "cab4cb4d0043c1714c95e1f9f08681932704a6b0",
        "merge_sha": "a8da8af9a705f87f43f950f89bafa05d32f74c8f",
    },
    {
        "branch": "feat/nexus-harbor-parity-version-table-20261106",
        "sha": "b0b709aec87e879ee1e258aa2085307716683e68",
        "pr": 456,
        "pr_head_sha": "b0b709aec87e879ee1e258aa2085307716683e68",
        "merge_sha": "d31a60726602ea97e8063d88267d68637f0a1ae3",
    },
    {
        "branch": "feature/nexus-helm-http-profiles-497",
        "sha": "f12dec3eae94800711e43f7b364c47d1d40dd14a",
        "pr": 499,
        "pr_head_sha": "f12dec3eae94800711e43f7b364c47d1d40dd14a",
        "merge_sha": "8766a7a069c7b56c5534cc9f4385a9ac349e5ae4",
    },
    {
        "branch": "fix/export-unexpected-artifact-attribution-422",
        "sha": "1a3c6561e2103bbb7043bc522781118c9373bf7c",
        "pr": 462,
        "pr_head_sha": "1a3c6561e2103bbb7043bc522781118c9373bf7c",
        "merge_sha": "a13b194048c77043e2e0337c5f95863a6caf0f16",
    },
    {
        "branch": "fix/harbor-target-root-image-token-scope-468",
        "sha": "e6150174875ea6f98f5dfe25f8dab2efc397d207",
        "pr": 468,
        "pr_head_sha": "e6150174875ea6f98f5dfe25f8dab2efc397d207",
        "merge_sha": "c93a2ec886eddab6344c58a14a45926b70e4edce",
    },
    {
        "branch": "fix/import-preview-nexus-root-image-mapping-469",
        "sha": "18b678f1b144769f2e4db9af2294c27e718d2314",
        "pr": 469,
        "pr_head_sha": "18b678f1b144769f2e4db9af2294c27e718d2314",
        "merge_sha": "fe313a47bb09a59354e7767e3ee16eb5fac56e03",
    },
    {
        "branch": "fix/nexus-catalog-search-pagination",
        "sha": "f1cf4c48b759e34f4ce3cd1bace5ebd137f97241",
        "pr": 460,
        "pr_head_sha": "f1cf4c48b759e34f4ce3cd1bace5ebd137f97241",
        "merge_sha": "976ad1e742974121d7711edc85ef6d1409a057d5",
    },
    {
        "branch": "fix/nexus-denied-push-failure-472",
        "sha": "10b13a27e05d231e2da975c0c39e329b319c0ba6",
        "pr": 472,
        "pr_head_sha": "10b13a27e05d231e2da975c0c39e329b319c0ba6",
        "merge_sha": "e8801abc21633a56cab35676c760b5c4d9731077",
    },
    {
        "branch": "fix/nexus-docker-canonical-image-path-467",
        "sha": "fb887a33abbad600f2d9cfbb1ad3eb334ea09cda",
        "pr": 467,
        "pr_head_sha": "fb887a33abbad600f2d9cfbb1ad3eb334ea09cda",
        "merge_sha": "ea36684f5a37a642991b9a6d7e7ddb4fa03ded38",
    },
    {
        "branch": "fix/nexus-group-rest-scope-464",
        "sha": "8ecf69874084aeb5fa8eedeebb3628a1d430ec5c",
        "pr": 464,
        "pr_head_sha": "8ecf69874084aeb5fa8eedeebb3628a1d430ec5c",
        "merge_sha": "4f6bac9bc8dc0387dc034f6a71085495dd2c5ce6",
    },
    {
        "branch": "fix/nexus-helm-export-error-attribution-20261106",
        "sha": "72d3031744e3d0ba9990caee51244528180b8c7a",
        "pr": 452,
        "pr_head_sha": "72d3031744e3d0ba9990caee51244528180b8c7a",
        "merge_sha": "24aca3b7aec97fe6a509412a42ce1bfe4c67f48a",
    },
    {
        "branch": "fix/nexus-helm-target-identity-and-spec-20261106",
        "sha": "ff15ca21a41e8ce92271f1f0bd3798fc6ba19dc4",
        "pr": 453,
        "pr_head_sha": "ff15ca21a41e8ce92271f1f0bd3798fc6ba19dc4",
        "merge_sha": "028adce032fd126273afa96cb0e0d4f6fc1ac348",
    },
    {
        "branch": "fix/nexus-known-image-tag-retry-20261106",
        "sha": "6ad40faffcbe6952f8a4768432e825b215a37af8",
        "pr": 459,
        "pr_head_sha": "6ad40faffcbe6952f8a4768432e825b215a37af8",
        "merge_sha": "c0d84c741d0cc803876189d0feee094e11577678",
    },
    {
        "branch": "fix/nexus-proxy-direct-image-flow-20261106",
        "sha": "6ab55c13b104b9b99d55e81ff1aad35acb5da3bf",
        "pr": 455,
        "pr_head_sha": "6ab55c13b104b9b99d55e81ff1aad35acb5da3bf",
        "merge_sha": "26b3a26a8556ce18c67448915ccc0a13af7e9cad",
    },
    {
        "branch": "fix/nexus-target-preflight-403-acl",
        "sha": "3569015e5129815561c8349ebfe78b11525d262c",
        "pr": 474,
        "pr_head_sha": "3569015e5129815561c8349ebfe78b11525d262c",
        "merge_sha": "73550719a23039e1380ff6d2a197ebd17513ca73",
    },
    {
        "branch": "fix/nexus-target-write-permission-evidence-471",
        "sha": "4735f4af7923f0f741d1b1f9818bab85008a798a",
        "pr": 471,
        "pr_head_sha": "4735f4af7923f0f741d1b1f9818bab85008a798a",
        "merge_sha": "a3189ecfc8f2f0e6b47586870bd64d4fc917d98f",
    },
    {
        "branch": "fix/refresh-nexus-profile-after-edit-463",
        "sha": "5bf170fa4ca3befb329969effb160af2e0c2b2eb",
        "pr": 463,
        "pr_head_sha": "5bf170fa4ca3befb329969effb160af2e0c2b2eb",
        "merge_sha": "7a3d6ab1332c7b6b24935b3e2d6dfdefd989a529",
    },
    {
        "branch": "fix/regional-preflight-docker-search-parity",
        "sha": "9070285d7b8033a5a7cf6e657610b7d2d2a62a2d",
        "pr": 461,
        "pr_head_sha": "9070285d7b8033a5a7cf6e657610b7d2d2a62a2d",
        "merge_sha": "5baef3f43b0d738d692fa95e72c4ebedc6a5e031",
    },
    {
        "branch": "integration/nexus-main-sync-resolved-20261110",
        "sha": "43383e8ebad362e9bca39f5212123ed59066fac9",
        "pr": 480,
        "pr_head_sha": "43383e8ebad362e9bca39f5212123ed59066fac9",
        "merge_sha": "7b32643bb04226b986d98c6ae015311528df01b6",
    },
]


def github(method: str, path: str):
    result = subprocess.run(
        ["gh", "api", "--method", method, f"repos/{REPO}/{path}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"GitHub {method} {path} failed: {result.stderr.strip()}")
    if method == "DELETE":
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid GitHub JSON for {path}") from exc


def pages(endpoint: str):
    rows = []
    for page in range(1, 30):
        sep = "&" if "?" in endpoint else "?"
        part = github("GET", f"{endpoint}{sep}per_page=100&page={page}")
        if not isinstance(part, list):
            raise RuntimeError(f"Expected array from {endpoint}")
        rows.extend(part)
        if len(part) < 100:
            return rows
    raise RuntimeError(f"Pagination limit reached for {endpoint}")


def read_ref_sha(name: str) -> str | None:
    encoded = quote(name, safe="/")
    try:
        ref = github("GET", f"git/ref/heads/{encoded}")
    except RuntimeError as error:
        if "404" in str(error) or "Not Found" in str(error):
            return None
        raise
    if ref.get("ref") != f"refs/heads/{name}":
        raise RuntimeError(f"Unexpected ref response for {name}")
    sha = (ref.get("object") or {}).get("sha")
    if not sha:
        raise RuntimeError(f"Ref has no object SHA: {name}")
    return sha


def check_ref(name: str, expected_sha: str):
    encoded = quote(name, safe="/")
    ref = github("GET", f"git/ref/heads/{encoded}")
    if (
        ref.get("ref") != f"refs/heads/{name}"
        or (ref.get("object") or {}).get("sha") != expected_sha
    ):
        raise RuntimeError(f"Ref missing or SHA changed: {name}")
    details = github("GET", f"branches/{encoded}")
    if details.get("protected") or (details.get("commit") or {}).get("sha") != expected_sha:
        raise RuntimeError(f"Branch protected or HEAD changed: {name}")


def targets_already_absent() -> bool:
    shas = [read_ref_sha(target["branch"]) for target in TARGETS]
    if all(sha is None for sha in shas):
        print("PASS: all 19 target refs are already absent; cleanup is a no-op")
        return True
    if any(sha is None for sha in shas):
        raise RuntimeError("Only part of the target ref set is absent; refusing partial cleanup")
    for target, sha in zip(TARGETS, shas):
        if sha != target["sha"]:
            raise RuntimeError(f"Target ref moved since audit: {target['branch']}")
    return False


def audit() -> bool:
    if targets_already_absent():
        return False

    integration_ref = github("GET", f"git/ref/heads/{quote(INTEGRATION, safe='/')}")
    integration_sha = (integration_ref.get("object") or {}).get("sha")
    if (
        integration_ref.get("ref") != f"refs/heads/{INTEGRATION}"
        or not integration_sha
    ):
        raise RuntimeError("Cannot read current Nexus integration branch SHA")
    if github("GET", f"compare/{AUDIT_BASE}...{integration_sha}").get("behind_by") != 0:
        raise RuntimeError("Pinned integration audit base is not an ancestor of current integration")

    open_prs = pages("pulls?state=open")
    all_prs = pages("pulls?state=all")

    for target in TARGETS:
        branch = target["branch"]
        pr = github("GET", f"pulls/{target['pr']}")
        head = pr.get("head") or {}
        base = pr.get("base") or {}
        if not (
            pr.get("state") == "closed"
            and pr.get("merged_at")
            and base.get("ref") == INTEGRATION
            and head.get("ref") == branch
            and head.get("sha") == target["pr_head_sha"]
            and (head.get("repo") or {}).get("full_name") == REPO
            and pr.get("merge_commit_sha") == target["merge_sha"]
        ):
            raise RuntimeError(f"Merged PR #{target['pr']} provenance changed: {branch}")

        if github("GET", f"compare/{target['merge_sha']}...{integration_sha}").get("behind_by") != 0:
            raise RuntimeError(f"PR #{target['pr']} merge is not contained in {INTEGRATION}")

        provenance = [
            item for item in all_prs
            if (item.get("head") or {}).get("ref") == branch
            and ((item.get("head") or {}).get("repo") or {}).get("full_name") == REPO
        ]
        if len(provenance) != 1 or provenance[0].get("number") != target["pr"]:
            raise RuntimeError(f"Unexpected historical PR provenance for {branch}")

        for item in open_prs:
            h, b = item.get("head") or {}, item.get("base") or {}
            if h.get("ref") == branch and ((h.get("repo") or {}).get("full_name") == REPO):
                raise RuntimeError(f"An open PR uses target branch as head: {branch}")
            if b.get("ref") == branch and ((b.get("repo") or {}).get("full_name") == REPO):
                raise RuntimeError(f"An open PR uses target branch as base: {branch}")

        check_ref(branch, target["sha"])

    own_sha = read_ref_sha(OWN)
    if own_sha is None:
        raise RuntimeError("Cleanup PR branch is missing before the compare-and-swap dry-run")
    check_ref(OWN, own_sha)

    refs = [(target["branch"], target["sha"]) for target in TARGETS] + [(OWN, own_sha)]
    print(
        "PASS: all 19 feature/sync refs have exactly one merged PR into the active "
        "Nexus integration branch; all merge commits are contained, all heads are "
        "unchanged/unprotected, and no open PR uses a target ref"
    )
    print(delete_refs(REPO, refs, dry_run=True))
    return True


def apply_guard():
    expected = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": REPO,
        "BRANCH_PRUNE_ENABLE": ENABLE,
    }
    for key, value in expected.items():
        if os.environ.get(key) != value:
            raise RuntimeError(f"Refusing destructive mode: {key} mismatch")
    checkout = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if checkout != os.environ.get("GITHUB_SHA"):
        raise RuntimeError("Checkout SHA does not match push event")


def own_merged_pr() -> str:
    merge_sha = os.environ["GITHUB_SHA"]
    for attempt in range(10):
        related = github("GET", f"commits/{merge_sha}/pulls")
        matches = [
            pr for pr in related
            if pr.get("merged_at")
            and pr.get("merge_commit_sha") == merge_sha
            and (pr.get("base") or {}).get("ref") == "main"
            and (pr.get("head") or {}).get("ref") == OWN
            and ((pr.get("head") or {}).get("repo") or {}).get("full_name") in (None, REPO)
        ]
        if len(matches) == 1 and (matches[0].get("head") or {}).get("sha"):
            return matches[0]["head"]["sha"]
        if attempt < 9:
            time.sleep(3)
    raise RuntimeError("Cannot identify exactly one merged cleanup PR after bounded retries")


def main():
    args = sys.argv[1:]
    if args not in ([], ["--apply"]):
        raise RuntimeError("Only --apply is supported")
    apply = args == ["--apply"]
    if apply:
        apply_guard()

    should_delete = audit()
    if not should_delete:
        return 0
    if not apply:
        print("DRY RUN: no refs deleted")
        return 0

    own_sha = own_merged_pr()
    refs = [(target["branch"], target["sha"]) for target in TARGETS] + [(OWN, own_sha)]
    for name, expected_sha in refs:
        check_ref(name, expected_sha)
    print(delete_refs(REPO, refs, dry_run=False))
    print("DELETED: 19 exact merged feature/sync refs and this cleanup ref via atomic CAS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        raise SystemExit(1)
