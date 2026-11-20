#!/usr/bin/env python3
"""One-shot, fail-closed cleanup of three merged Issue #513 handoff refs.

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
OWN = "chore/513-retire-merged-handoff-refs-once"
ENABLE = "issue-513-retire-handoff-refs"
AUDIT_BASE = "5dba85192a8e5be64bf76779d55286d21c9e472b"

TARGETS = [
    {
        "branch": "chore/513-prune-harbor-profile-aliases-once",
        "sha": "c7e3dd87d22f8b6c7ca1a13a41f6ecbd0eb4e66f",
        "pr": 539,
        "pr_head_sha": "3e27743f0677816da424bb7021030471206750d4",
        "merge_sha": "1871963e825eb96fc969fa22f8f5f950635b43e1",
        "ahead": 5,
        "files": {
            ".github/workflows/prune-harbor-profile-aliases-once.yml",
            "tools/prune_harbor_profile_aliases_once.py",
        },
    },
    {
        "branch": "chore/513-retry-harbor-profile-alias-prune",
        "sha": "d307afd922d4c0fdefc7df3b9248e0d26e29cc32",
        "pr": 541,
        "pr_head_sha": "d307afd922d4c0fdefc7df3b9248e0d26e29cc32",
        "merge_sha": "3d51eba23125b8c1fc340ff1c59c0866f3474513",
        "ahead": 2,
        "files": {
            ".github/workflows/prune-harbor-profile-aliases-once.yml",
            "tools/prune_harbor_profile_aliases_once.py",
        },
    },
    {
        "branch": "chore/513-atomic-ref-cleanup-hardening",
        "sha": "c318059ff40ae2e08749c8f05239aaffe44bf9ee",
        "pr": 543,
        "pr_head_sha": "c318059ff40ae2e08749c8f05239aaffe44bf9ee",
        "merge_sha": "5dba85192a8e5be64bf76779d55286d21c9e472b",
        "ahead": 0,
        "files": set(),
    },
]

CURRENT_MAIN_FILES = {
    ".github/workflows/atomic-ref-cleanup-tests.yml": "8f2a77feba853d545812f4b3a5c1129ebbd0a754",
    ".github/workflows/prune-harbor-profile-aliases-once.yml": "226c1011c471f8cee8ba9f2ec6319ff26ce792a5",
    ".github/workflows/prune-key-management-review-alias-once.yml": "eed79caf00c465c2f6f0419d53e0e776a60d643c",
    "tools/atomic_ref_cleanup.py": "b55be00f95a56db0f6cbf22d88aa7762018bf32f",
    "tools/prune_harbor_profile_aliases_once.py": "009861235189959399246ecad91adfff5cc35a35",
    "tools/prune_key_management_review_alias_once.py": "d8fe879ced62b6ff0cc488870950ab209ba2a377",
    "tools/test_atomic_ref_cleanup.py": "2b14a0ca4706c646f5ad8f0324ff648a8c554f51",
}


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
        print("PASS: all three target refs are already absent; cleanup is a no-op")
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

    main_ref = github("GET", "git/ref/heads/main")
    main_sha = (main_ref.get("object") or {}).get("sha")
    if not main_sha:
        raise RuntimeError("Cannot read current main SHA")
    if github("GET", f"compare/{AUDIT_BASE}...{main_sha}").get("behind_by") != 0:
        raise RuntimeError("Pinned cleanup audit base is not an ancestor of current main")

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
            and base.get("ref") == "main"
            and head.get("ref") == branch
            and head.get("sha") == target["pr_head_sha"]
            and (head.get("repo") or {}).get("full_name") == REPO
            and pr.get("merge_commit_sha") == target["merge_sha"]
        ):
            raise RuntimeError(f"Merged PR #{target['pr']} provenance changed: {branch}")

        if github("GET", f"compare/{target['merge_sha']}...{main_sha}").get("behind_by") != 0:
            raise RuntimeError(f"PR #{target['pr']} merge is not contained in current main")

        provenance = [
            item for item in all_prs
            if (item.get("head") or {}).get("ref") == branch
            and ((item.get("head") or {}).get("repo") or {}).get("full_name") == REPO
        ]
        if len(provenance) != 1 or provenance[0].get("number") != target["pr"]:
            raise RuntimeError(f"Unexpected historical PR provenance for {branch}")

        for item in open_prs:
            h, b = item.get("head") or {}, item.get("base") or {}
            if (
                h.get("ref") == branch
                and ((h.get("repo") or {}).get("full_name") == REPO)
            ):
                raise RuntimeError(f"An open PR uses target branch as head: {branch}")
            if (
                b.get("ref") == branch
                and ((b.get("repo") or {}).get("full_name") == REPO)
            ):
                raise RuntimeError(f"An open PR uses target branch as base: {branch}")

        delta = github("GET", f"compare/main...{target['sha']}")
        if delta.get("ahead_by") != target["ahead"]:
            raise RuntimeError(f"Target unique commit count changed: {branch}")
        if {item["filename"] for item in delta.get("files") or []} != target["files"]:
            raise RuntimeError(f"Target changed-file scope drift: {branch}")

        check_ref(branch, target["sha"])

    for path, expected_sha in CURRENT_MAIN_FILES.items():
        item = github("GET", f"contents/{path}?ref=main")
        if item.get("sha") != expected_sha:
            raise RuntimeError(f"Current atomic-cleanup replacement changed: {path}")

    own_sha = read_ref_sha(OWN)
    if own_sha is None:
        raise RuntimeError("Cleanup PR branch is missing before the compare-and-swap dry-run")
    check_ref(OWN, own_sha)

    refs = [(target["branch"], target["sha"]) for target in TARGETS] + [(OWN, own_sha)]
    print(
        "PASS: PRs #539/#541/#543 are merged into main; all three target refs are "
        "unprotected, unchanged and have no open PR dependencies; #539/#541 have only "
        "the reviewed cleanup-tooling file scope; #543 is fully contained in main"
    )
    print("PASS: exact current-main atomic helper, cleanup scripts, workflows and tests are pinned")
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
    print("DELETED: three exact merged handoff refs and this cleanup ref via atomic CAS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        raise SystemExit(1)
