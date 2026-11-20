#!/usr/bin/env python3
"""One-shot removal of the superseded Harbor CA/enabled lifecycle branch."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-harbor-profile-ca-lifecycle-duplicate-once"
ENABLE = "issue-513-harbor-profile-ca-lifecycle"
AUDIT_BASE = "0b219a96af5123fdbb700eae6cbc97073e7cb2cc"

TARGET = {
    "branch": "feat/harbor-profile-ca-enabled",
    "sha": "53375e65b478118af95b75ecf3960bd3b0c04934",
    "original_prs": (381, 382),
    "replacement_pr": 380,
    "issue": 379,
    "original_files": {
        "docs/admin-guide.md",
        "docs/frontend.md",
        "frontend/src/components/HarborProfilesPanel.spec.ts",
        "frontend/src/components/HarborProfilesPanel.vue",
    },
    "replacement_files": {
        "docs/harbor-profiles.md",
        "docs/settings.md",
        "frontend/src/components/HarborProfilesPanel.spec.ts",
        "frontend/src/components/HarborProfilesPanel.vue",
    },
}

def github(method: str, path: str):
    result = subprocess.run(
        ["gh", "api", "--method", method, f"repos/{REPO}/{path}"],
        text=True, capture_output=True, check=False,
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

def changed_files(pr: int) -> set[str]:
    return {item["filename"] for item in pages(f"pulls/{pr}/files")}

def check_ref(name: str, expected_sha: str):
    encoded = quote(name, safe="/")
    ref = github("GET", f"git/ref/heads/{encoded}")
    if ref.get("ref") != f"refs/heads/{name}" or (ref.get("object") or {}).get("sha") != expected_sha:
        raise RuntimeError(f"Ref missing or SHA changed: {name}")
    details = github("GET", f"branches/{encoded}")
    if details.get("protected") or (details.get("commit") or {}).get("sha") != expected_sha:
        raise RuntimeError(f"Branch protected or HEAD changed: {name}")

def check_original_pr(number: int):
    pr = github("GET", f"pulls/{number}")
    head = pr.get("head") or {}
    if not (
        pr.get("state") == "closed"
        and not pr.get("merged_at")
        and (pr.get("base") or {}).get("ref") == "main"
        and head.get("ref") == TARGET["branch"]
        and head.get("sha") == TARGET["sha"]
        and ((head.get("repo") or {}).get("full_name") in (None, REPO))
    ):
        raise RuntimeError(f"Original PR #{number} state/provenance changed")
    if changed_files(number) != TARGET["original_files"]:
        raise RuntimeError(f"Original PR #{number} changed-file scope drift")

def check_replacement():
    number = TARGET["replacement_pr"]
    pr = github("GET", f"pulls/{number}")
    if not (
        pr.get("state") == "closed"
        and pr.get("merged_at")
        and (pr.get("base") or {}).get("ref") == "main"
        and (pr.get("head") or {}).get("ref") == "feat/harbor-profile-admin-lifecycle"
        and (pr.get("head") or {}).get("sha") == "9227601742d84f00ae99f1788efcfc8e6057ccd0"
        and pr.get("merge_commit_sha") == "a40eb00d45c04bae78c93241212d012f123db37c"
    ):
        raise RuntimeError("Replacement PR #380 is not the expected merged-to-main change")
    files = changed_files(number)
    if not TARGET["replacement_files"].issubset(files):
        raise RuntimeError("Replacement PR #380 changed-file scope is incomplete")
    ancestry = github("GET", f"compare/{pr['merge_commit_sha']}...{AUDIT_BASE}")
    if ancestry.get("behind_by") != 0:
        raise RuntimeError("Replacement PR #380 merge is not in pinned main snapshot")

def audit():
    main_ref = github("GET", "git/ref/heads/main")
    main_sha = (main_ref.get("object") or {}).get("sha")
    if not main_sha:
        raise RuntimeError("Cannot read current main SHA")
    ancestry = github("GET", f"compare/{AUDIT_BASE}...{main_sha}")
    if ancestry.get("behind_by") != 0:
        raise RuntimeError("Pinned audit base is not an ancestor of current main")

    issue = github("GET", f"issues/{TARGET['issue']}")
    if not (issue.get("state") == "closed" and issue.get("state_reason") == "completed"):
        raise RuntimeError("Harbor profile lifecycle issue #379 is not completed")

    open_prs = pages("pulls?state=open")
    for pr in open_prs:
        head, base = pr.get("head") or {}, pr.get("base") or {}
        if head.get("ref") == TARGET["branch"] and ((head.get("repo") or {}).get("full_name") == REPO):
            raise RuntimeError("An open PR uses target branch as head")
        if base.get("ref") == TARGET["branch"] and ((base.get("repo") or {}).get("full_name") == REPO):
            raise RuntimeError("An open PR uses target branch as base")

    check_ref(TARGET["branch"], TARGET["sha"])
    for number in TARGET["original_prs"]:
        check_original_pr(number)
    check_replacement()
    print(
        f"PASS {TARGET['branch']}: exact live SHA; PRs #381/#382 are closed-unmerged; "
        "issue #379 is completed; replacement #380 is merged to main; no open PR dependency"
    )
    print("PASS: branch is unprotected and unchanged; no refs deleted in dry-run")

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
        ["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True
    ).stdout.strip()
    if checkout != os.environ.get("GITHUB_SHA"):
        raise RuntimeError("Checkout SHA does not match push event")

def own_merged_pr() -> str:
    merge_sha = os.environ["GITHUB_SHA"]
    related = github("GET", f"commits/{merge_sha}/pulls")
    matches = [
        pr for pr in related
        if pr.get("merged_at")
        and pr.get("merge_commit_sha") == merge_sha
        and (pr.get("base") or {}).get("ref") == "main"
        and (pr.get("head") or {}).get("ref") == OWN
        and ((pr.get("head") or {}).get("repo") or {}).get("full_name") in (None, REPO)
    ]
    if len(matches) != 1 or not (matches[0].get("head") or {}).get("sha"):
        raise RuntimeError("Cannot identify exactly one merged cleanup PR")
    return matches[0]["head"]["sha"]

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

    own_sha = own_merged_pr()
    check_ref(TARGET["branch"], TARGET["sha"])
    check_ref(OWN, own_sha)
    check_ref(TARGET["branch"], TARGET["sha"])
    github("DELETE", f"git/refs/heads/{quote(TARGET['branch'], safe='/')}")
    print(f"DELETED: {TARGET['branch']}")
    check_ref(OWN, own_sha)
    github("DELETE", f"git/refs/heads/{quote(OWN, safe='/')}")
    print("DELETED: one-shot cleanup branch; all other refs untouched")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        raise SystemExit(1)
