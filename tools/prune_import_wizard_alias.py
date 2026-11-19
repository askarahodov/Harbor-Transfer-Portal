#!/usr/bin/env python3
"""Remove one duplicate branch alias whose exact commit was merged as PR #89.

Dry-run by default. --apply is permitted only on the main push created by
merging this one-time cleanup PR. All checks fail closed before deletion.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-import-wizard-alias-once"
ENABLE = "issue-513-import-wizard-alias"

TARGET = "feat/p5-2-import-wizard-review-checkpoint"
TARGET_SHA = "2f20064692b395c91d84872c2683f0f88d86d1ad"
TARGET_TREE = "46b63d63910c8a17cc4ea6e039e66766c2446cab"
MERGED_PR = 89
MERGED_PR_SHA = "b7fe381d85f7926bd930abecd1f3371a94227899"
CLOSED_DUPLICATE_PR = 94


def github(method: str, path: str):
    result = subprocess.run(
        ["gh", "api", "--method", method, f"repos/{REPO}/{path}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"GitHub {method} {path} failed: {result.stderr.strip()}"
        )
    if method == "DELETE":
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid GitHub JSON for {path}") from exc


def pages(endpoint: str):
    rows = []
    for page in range(1, 30):
        separator = "&" if "?" in endpoint else "?"
        part = github("GET", f"{endpoint}{separator}per_page=100&page={page}")
        if not isinstance(part, list):
            raise RuntimeError(f"Expected array from {endpoint}")
        rows.extend(part)
        if len(part) < 100:
            return rows
    raise RuntimeError(f"Pagination limit reached for {endpoint}; refusing")


def encoded(name: str) -> str:
    return quote(name, safe="/")


def check_ref(name: str, expected_sha: str):
    ref = github("GET", f"git/ref/heads/{encoded(name)}")
    if (
        ref.get("ref") != f"refs/heads/{name}"
        or (ref.get("object") or {}).get("sha") != expected_sha
    ):
        raise RuntimeError(f"Ref missing or SHA changed: {name}")
    branch = github("GET", f"branches/{encoded(name)}")
    if (
        branch.get("protected")
        or (branch.get("commit") or {}).get("sha") != expected_sha
    ):
        raise RuntimeError(f"Branch protected or HEAD changed: {name}")


def audit():
    open_heads = {
        (pull.get("head") or {}).get("ref")
        for pull in pages("pulls?state=open")
        if (
            ((pull.get("head") or {}).get("repo") or {}).get("full_name")
            == REPO
        )
    }
    if TARGET in open_heads or TARGET == "main":
        raise RuntimeError("Target is protected by an open PR or is main")

    check_ref(TARGET, TARGET_SHA)

    merged = github("GET", f"pulls/{MERGED_PR}")
    merged_head = merged.get("head") or {}
    if not (
        merged.get("state") == "closed"
        and merged.get("merged_at")
        and (merged.get("base") or {}).get("ref") == "main"
        and merged.get("merge_commit_sha") == MERGED_PR_SHA
        and merged_head.get("sha") == TARGET_SHA
        and (merged_head.get("repo") or {}).get("full_name") == REPO
    ):
        raise RuntimeError(f"PR #{MERGED_PR} no longer proves exact main merge")

    duplicate = github("GET", f"pulls/{CLOSED_DUPLICATE_PR}")
    duplicate_head = duplicate.get("head") or {}
    if not (
        duplicate.get("state") == "closed"
        and not duplicate.get("merged_at")
        and (duplicate.get("base") or {}).get("ref") == "main"
        and duplicate_head.get("sha") == TARGET_SHA
        and (duplicate_head.get("repo") or {}).get("full_name") == REPO
    ):
        raise RuntimeError(
            f"Closed duplicate PR #{CLOSED_DUPLICATE_PR} no longer matches"
        )

    commit = github("GET", f"git/commits/{TARGET_SHA}")
    if (commit.get("tree") or {}).get("sha") != TARGET_TREE:
        raise RuntimeError("Target tree SHA changed")

    print(
        f"PASS {TARGET}: exact SHA/tree is the same commit as merged PR "
        f"#{MERGED_PR}; closed duplicate PR #{CLOSED_DUPLICATE_PR}; "
        "no open PR uses the alias"
    )


def apply_guard():
    required = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": REPO,
        "BRANCH_PRUNE_ENABLE": ENABLE,
    }
    for key, expected in required.items():
        if os.environ.get(key) != expected:
            raise RuntimeError(f"Refusing destructive mode: {key} mismatch")

    checkout = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if checkout != os.environ.get("GITHUB_SHA"):
        raise RuntimeError("Checkout SHA does not match the push event")


def audit_own_merged_pr() -> str:
    merge_sha = os.environ["GITHUB_SHA"]
    related = github("GET", f"commits/{merge_sha}/pulls")
    matches = [
        pull
        for pull in related
        if pull.get("merged_at")
        and pull.get("merge_commit_sha") == merge_sha
        and (pull.get("base") or {}).get("ref") == "main"
        and (pull.get("head") or {}).get("ref") == OWN
        and ((pull.get("head") or {}).get("repo") or {}).get("full_name")
        == REPO
    ]
    if len(matches) != 1:
        raise RuntimeError("Cannot identify exactly one merged cleanup PR")
    own_sha = (matches[0].get("head") or {}).get("sha")
    if not own_sha:
        raise RuntimeError("Cleanup PR head SHA unavailable")
    if any(
        (pull.get("head") or {}).get("ref") == OWN
        and ((pull.get("head") or {}).get("repo") or {}).get("full_name")
        == REPO
        for pull in pages("pulls?state=open")
    ):
        raise RuntimeError("Cleanup branch still belongs to an open PR")
    check_ref(OWN, own_sha)
    return own_sha


def main():
    args = sys.argv[1:]
    if args not in ([], ["--apply"]):
        raise RuntimeError("Only --apply is supported")
    apply = args == ["--apply"]
    if apply:
        apply_guard()

    audit()
    if not apply:
        print("DRY RUN: no refs deleted")
        return 0

    own_sha = audit_own_merged_pr()
    check_ref(TARGET, TARGET_SHA)
    check_ref(OWN, own_sha)

    github("DELETE", f"git/refs/heads/{encoded(TARGET)}")
    print(f"DELETED reviewed alias: {TARGET}")

    check_ref(OWN, own_sha)
    github("DELETE", f"git/refs/heads/{encoded(OWN)}")
    print("DELETED one-time cleanup branch; all other refs untouched")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        raise SystemExit(1)
