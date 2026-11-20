#!/usr/bin/env python3
"""One-shot fail-closed cleanup of three refs proven superseded in issue #513.

Dry-run by default. Destructive mode is restricted to the GitHub Actions push
that follows merging this cleanup PR into main. No commits or working trees
are rewritten; only the three pinned branch refs and this cleanup branch are
deleted.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-three-reviewed-refs-once"
ENABLE = "issue-513-three-reviewed-refs"

# branch -> (exact current head, closed unmerged PR, merged replacement PR,
#            exact replacement merge commit)
TARGETS = {
    "refactor/326-import-bundle-storage": (
        "1b1ee693595ef37ece94fde2ef314f8c6d4cd22f",
        331,
        333,
        "3c9d10a13f332a6035147aa1ffd8ad6a785a0423",
    ),
    "fix/nexus-v2-connection-probe-428": (
        "46182be6d438b70aebb7a1914469ae8855c34875",
        438,
        491,
        "719f9b278abb3ddc843a43b7834ebb76dbddbbce",
    ),
    "fix/export-worker-failure-state-422": (
        "62d8f41e5a01113df1218c074ed48a636937cf4e",
        445,
        475,
        "36114fcb7404370aa4ad352d79263d704b37152b",
    ),
}


def github(method: str, path: str):
    command = ["gh", "api", "--method", method, f"repos/{REPO}/{path}"]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(
            f"GitHub {method} {path} failed: {result.stderr.strip()}"
        )
    if method == "DELETE":
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GitHub returned invalid JSON for {path}") from exc


def pages(endpoint: str):
    rows = []
    for page in range(1, 30):
        separator = "&" if "?" in endpoint else "?"
        part = github(
            "GET", f"{endpoint}{separator}per_page=100&page={page}"
        )
        if not isinstance(part, list):
            raise RuntimeError(f"Expected an array from {endpoint}")
        rows.extend(part)
        if len(part) < 100:
            return rows
    raise RuntimeError(f"Pagination limit reached for {endpoint}; refusing")


def ref_path(name: str) -> str:
    return quote(name, safe="/")


def check_ref(name: str, expected_sha: str):
    ref = github("GET", f"git/ref/heads/{ref_path(name)}")
    if (
        ref.get("ref") != f"refs/heads/{name}"
        or (ref.get("object") or {}).get("sha") != expected_sha
    ):
        raise RuntimeError(f"Ref missing or SHA changed: {name}")
    branch = github("GET", f"branches/{ref_path(name)}")
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

    for name, (sha, original_number, replacement_number, replacement_merge) in sorted(
        TARGETS.items()
    ):
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
                f"Replacement PR #{replacement_number} is not the verified "
                f"main merge for {name}"
            )

        print(
            f"PASS {name}: exact HEAD, closed original PR "
            f"#{original_number}, merged replacement PR #{replacement_number}"
        )

    print(f"PASS: all {len(TARGETS)} targets validated before any deletion")


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

    checked_out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if not checked_out or checked_out != os.environ.get("GITHUB_SHA"):
        raise RuntimeError("Refusing destructive mode: checkout SHA mismatch")


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
        raise RuntimeError("Cleanup PR head SHA is unavailable")

    if any(
        (pull.get("head") or {}).get("ref") == OWN
        and ((pull.get("head") or {}).get("repo") or {}).get("full_name")
        == REPO
        for pull in pages("pulls?state=open")
    ):
        raise RuntimeError("Cleanup branch is still used by an open PR")

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

    # Recheck every target together before beginning the destructive sequence.
    for name, (sha, *_rest) in sorted(TARGETS.items()):
        check_ref(name, sha)
    check_ref(OWN, own_sha)

    removed = 0
    for name, (sha, *_rest) in sorted(TARGETS.items()):
        check_ref(name, sha)
        github("DELETE", f"git/refs/heads/{ref_path(name)}")
        removed += 1
        print(f"DELETED {removed}/{len(TARGETS)}: {name}")

    check_ref(OWN, own_sha)
    github("DELETE", f"git/refs/heads/{ref_path(OWN)}")
    print(
        f"DELETED {removed} reviewed refs and the one-time cleanup branch; "
        "all other refs untouched"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        raise SystemExit(1)
