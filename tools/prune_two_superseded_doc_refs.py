#!/usr/bin/env python3
"""One-shot cleanup of two documentation refs superseded by main-merged PRs.

Dry-run by default. --apply is restricted to the push to main caused by
merging this exact cleanup PR. Exact SHA, PR provenance and changed-file-set
checks are repeated before any ref is deleted.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-two-superseded-doc-refs-once"
ENABLE = "issue-513-two-superseded-doc-refs"

# (branch, exact branch SHA, closed/unmerged PR, merged replacement PR,
#  exact replacement merge commit)
TARGETS = (
    (
        "docs/russian-translation",
        "ef22831d755e4420ff9df3a2d0f7f4b412939fb6",
        33,
        37,
        "bb8488ae2c7d3d130cf410cb41c33c347f1b72b3",
    ),
    (
        "docs/readme-information-architecture",
        "0c9d900dc49f9353a0919419392931a04c63d3ea",
        54,
        77,
        "997bd5ba7141c997b69cf699876279847a36f871",
    ),
)


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


def pull_files(number: int) -> set[str]:
    files = pages(f"pulls/{number}/files")
    return {item["filename"] for item in files}


def check_ref(name: str, expected_sha: str):
    ref = github("GET", f"git/ref/heads/{quote(name, safe='/')}")
    if (
        ref.get("ref") != f"refs/heads/{name}"
        or (ref.get("object") or {}).get("sha") != expected_sha
    ):
        raise RuntimeError(f"Ref missing or SHA changed: {name}")
    branch = github("GET", f"branches/{quote(name, safe='/')}")
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

    for name, sha, original_number, replacement_number, replacement_merge in TARGETS:
        if name == "main" or name == OWN or name in open_heads:
            raise RuntimeError(f"Unsafe candidate or open PR head: {name}")
        check_ref(name, sha)

        original = github("GET", f"pulls/{original_number}")
        original_head = original.get("head") or {}
        if not (
            original.get("state") == "closed"
            and not original.get("merged_at")
            and (original.get("base") or {}).get("ref") == "main"
            and original_head.get("ref") == name
            and original_head.get("sha") == sha
            and (original_head.get("repo") or {}).get("full_name") == REPO
        ):
            raise RuntimeError(
                f"Original PR #{original_number} no longer matches {name}"
            )

        replacement = github("GET", f"pulls/{replacement_number}")
        if not (
            replacement.get("state") == "closed"
            and replacement.get("merged_at")
            and (replacement.get("base") or {}).get("ref") == "main"
            and replacement.get("merge_commit_sha") == replacement_merge
        ):
            raise RuntimeError(
                f"Replacement PR #{replacement_number} is not the pinned main merge"
            )

        original_files = pull_files(original_number)
        replacement_files = pull_files(replacement_number)
        if not original_files or original_files != replacement_files:
            raise RuntimeError(
                f"Changed-file set differs for {name}: "
                f"original={sorted(original_files)}, "
                f"replacement={sorted(replacement_files)}"
            )

        print(
            f"PASS {name}: exact HEAD, closed PR #{original_number}, "
            f"merged replacement #{replacement_number}, identical changed-file set"
        )

    print(f"PASS: all {len(TARGETS)} refs validated before any deletion")


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
    for name, sha, *_rest in TARGETS:
        check_ref(name, sha)
    check_ref(OWN, own_sha)

    for index, (name, sha, *_rest) in enumerate(TARGETS, start=1):
        check_ref(name, sha)
        github("DELETE", f"git/refs/heads/{quote(name, safe='/')}")
        print(f"DELETED {index}/{len(TARGETS)}: {name}")

    check_ref(OWN, own_sha)
    github("DELETE", f"git/refs/heads/{quote(OWN, safe='/')}")
    print("DELETED one-time cleanup branch; all other refs untouched")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        raise SystemExit(1)
