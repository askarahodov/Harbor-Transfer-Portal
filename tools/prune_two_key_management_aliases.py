#!/usr/bin/env python3
"""One-shot cleanup of two superseded trusted-key implementation refs.

The refs are no-PR alternatives for completed issue #121. Their changed-file
sets are fully covered by the merged managed-key implementation sequence
#117/#119/#125. Dry-run by default; --apply is main-push and own-PR guarded.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-two-key-management-aliases-once"
ENABLE = "issue-513-two-key-management-aliases"
AUDIT_BASE = "4dd1cf73869c214c22d8d69c7bb7c884abad03e6"

REPLACEMENTS = {
    117: "e585df912f78278aaa5d4ff6e465a5ac950e14be",
    119: "630f350a6d7409c559933383e7270f88ba382be6",
    125: "936247f5c5ad52303adf767ef6f248f22178707b",
}

TARGETS = (
    {
        "branch": "feat/121-trusted-key-confirm-replace",
        "sha": "e2ef2da2517a24a5627776f9149b072e1a8bc23e",
        "issues": (121,),
        "files": (
            "backend/app/api/key_settings.py",
            "backend/app/schemas/keys.py",
            "backend/app/services/key_management.py",
            "backend/tests/test_key_management_api.py",
            "backend/tests/test_key_management_atomic_commit.py",
            "docs/key-management.md",
            "frontend/src/components/KeyManagementPanel.spec.ts",
            "frontend/src/components/KeyManagementPanel.vue",
        ),
    },
    {
        "branch": "fix/agent2-key-confirm-replace",
        "sha": "1dd23820b006202e8622497614b0df3377f9ba0c",
        "issues": (121,),
        "files": (
            "backend/app/api/key_settings.py",
            "backend/app/schemas/keys.py",
            "backend/app/services/key_management.py",
            "frontend/src/components/KeyManagementPanel.spec.ts",
            "frontend/src/components/KeyManagementPanel.vue",
        ),
    },
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
    all_prs = pages("pulls?state=all")
    open_prs = pages("pulls?state=open")
    replacement_files = set()

    for number, merge_sha in REPLACEMENTS.items():
        pr = github("GET", f"pulls/{number}")
        if not (
            pr.get("state") == "closed"
            and pr.get("merged_at")
            and (pr.get("base") or {}).get("ref") == "main"
            and pr.get("merge_commit_sha") == merge_sha
        ):
            raise RuntimeError(f"Replacement PR #{number} is not the pinned main merge")

        ancestry = github("GET", f"compare/{merge_sha}...{AUDIT_BASE}")
        if ancestry.get("behind_by") != 0:
            raise RuntimeError(
                f"Replacement PR #{number} merge is not in the pinned main snapshot"
            )

        replacement_files.update(
            item["filename"] for item in pages(f"pulls/{number}/files")
        )

    pr125 = github("GET", "pulls/125")
    if "#121" not in ((pr125.get("title") or "") + (pr125.get("body") or "")):
        raise RuntimeError("PR #125 no longer documents issue #121 completion")

    for target in TARGETS:
        name = target["branch"]
        sha = target["sha"]
        if name in ("main", OWN):
            raise RuntimeError(f"Forbidden target: {name}")

        if any(
            (pull.get("head") or {}).get("ref") == name
            and ((pull.get("head") or {}).get("repo") or {}).get("full_name")
            == REPO
            for pull in all_prs
        ):
            raise RuntimeError(f"Target unexpectedly has PR provenance: {name}")
        if any((pull.get("base") or {}).get("ref") == name for pull in open_prs):
            raise RuntimeError(f"An open PR is based on target: {name}")

        check_ref(name, sha)

        compare = github("GET", f"compare/{AUDIT_BASE}...{sha}")
        actual_files = {
            item["filename"] for item in (compare.get("files") or [])
        }
        expected_files = set(target["files"])
        if actual_files != expected_files:
            raise RuntimeError(
                f"Changed-file scope drift for {name}: "
                f"actual={sorted(actual_files)}, expected={sorted(expected_files)}"
            )
        if not expected_files.issubset(replacement_files):
            raise RuntimeError(
                f"Merged #117/#119/#125 do not cover every changed file for {name}"
            )

        for issue_number in target["issues"]:
            issue = github("GET", f"issues/{issue_number}")
            if not (
                issue.get("state") == "closed"
                and issue.get("state_reason") == "completed"
            ):
                raise RuntimeError(f"Issue #{issue_number} is not completed")

        print(
            f"PASS {name}: exact SHA/file scope; completed issue(s) "
            f"{target['issues']}; merged #117/#119/#125 cover every changed file"
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
    for target in TARGETS:
        check_ref(target["branch"], target["sha"])
    check_ref(OWN, own_sha)

    for index, target in enumerate(TARGETS, start=1):
        check_ref(target["branch"], target["sha"])
        github("DELETE", f"git/refs/heads/{quote(target['branch'], safe='/')}")
        print(f"DELETED {index}/{len(TARGETS)}: {target['branch']}")

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
