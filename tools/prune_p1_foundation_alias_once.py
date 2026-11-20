#!/usr/bin/env python3
"""One-shot cleanup of the merged P1.1 foundation branch and its superseded env rename."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-p1-foundation-alias-once"
ENABLE = "issue-513-p1-foundation-alias"
AUDIT_BASE = "96620e2fa00784def2fed4d0bf076c60d10f6f5e"

TARGET = {
    "branch": "feat/p1-1-repository-foundation",
    "sha": "64b8f1f43b12416ccb53478a3b29c597cecf450c",
    "original_pr": 22,
    "original_head_sha": "68eb61149d37ea34338de50c73f060ca8917d646",
    "original_merge_sha": "4de150cd48c63693de8e02b707fa5e3dde673c9a",
    "issue": 2,
    "post_merge_commit": "64b8f1f43b12416ccb53478a3b29c597cecf450c",
    "post_merge_file": ".env.example",
    "current_env_sha": "3d96493c8b46899b36ed34f36f33df74dd8cef84",
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

def check_ref(name: str, expected_sha: str):
    encoded = quote(name, safe="/")
    ref = github("GET", f"git/ref/heads/{encoded}")
    if ref.get("ref") != f"refs/heads/{name}" or (ref.get("object") or {}).get("sha") != expected_sha:
        raise RuntimeError(f"Ref missing or SHA changed: {name}")
    details = github("GET", f"branches/{encoded}")
    if details.get("protected") or (details.get("commit") or {}).get("sha") != expected_sha:
        raise RuntimeError(f"Branch protected or HEAD changed: {name}")

def audit():
    main_ref = github("GET", "git/ref/heads/main")
    main_sha = (main_ref.get("object") or {}).get("sha")
    if not main_sha:
        raise RuntimeError("Cannot read current main SHA")
    if github("GET", f"compare/{AUDIT_BASE}...{main_sha}").get("behind_by") != 0:
        raise RuntimeError("Pinned audit base is not an ancestor of current main")

    issue = github("GET", f"issues/{TARGET['issue']}")
    if not (issue.get("state") == "closed" and issue.get("state_reason") == "completed"):
        raise RuntimeError("Foundation issue #2 is not completed")

    pr = github("GET", f"pulls/{TARGET['original_pr']}")
    head = pr.get("head") or {}
    if not (
        pr.get("state") == "closed"
        and pr.get("merged_at")
        and (pr.get("base") or {}).get("ref") == "main"
        and head.get("ref") == TARGET["branch"]
        and head.get("sha") == TARGET["original_head_sha"]
        and ((head.get("repo") or {}).get("full_name") in (None, REPO))
        and pr.get("merge_commit_sha") == TARGET["original_merge_sha"]
    ):
        raise RuntimeError("Merged PR #22 provenance or merge SHA changed")
    ancestry = github("GET", f"compare/{TARGET['original_merge_sha']}...{AUDIT_BASE}")
    if ancestry.get("behind_by") != 0:
        raise RuntimeError("PR #22 merge is not in pinned main snapshot")

    delta = github("GET", f"compare/{TARGET['original_head_sha']}...{TARGET['sha']}")
    if delta.get("ahead_by") != 1 or delta.get("behind_by") != 0:
        raise RuntimeError("Branch post-merge history is no longer exactly one commit")
    files = delta.get("files") or []
    if len(files) != 1 or files[0].get("filename") != TARGET["post_merge_file"]:
        raise RuntimeError("Post-merge delta is not the expected single .env.example change")
    if delta.get("commits", [{}])[0].get("sha") != TARGET["post_merge_commit"]:
        raise RuntimeError("Post-merge commit identity changed")

    env = github("GET", "contents/.env.example?ref=main")
    if env.get("sha") != TARGET["current_env_sha"]:
        raise RuntimeError("Current main .env.example changed since audit")
    open_prs = pages("pulls?state=open")
    for item in open_prs:
        h, b = item.get("head") or {}, item.get("base") or {}
        if h.get("ref") == TARGET["branch"] and ((h.get("repo") or {}).get("full_name") == REPO):
            raise RuntimeError("An open PR uses target branch as head")
        if b.get("ref") == TARGET["branch"] and ((b.get("repo") or {}).get("full_name") == REPO):
            raise RuntimeError("An open PR uses target branch as base")

    check_ref(TARGET["branch"], TARGET["sha"])
    print(
        "PASS feat/p1-1-repository-foundation: PR #22 merged to main; issue #2 completed; "
        "only post-merge commit renames legacy HTP env names, superseded by current .env.example"
    )
    print("PASS: exact live SHA, current replacement file, no open PR dependency; dry-run only")

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
