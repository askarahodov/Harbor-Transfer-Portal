#!/usr/bin/env python3
"""One-shot cleanup of the merged bounded-key-read implementation branch."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-race-safe-key-read-implementation-once"
ENABLE = "issue-513-race-safe-key-read-implementation"
AUDIT_BASE = "ddda010e37c5c4164f7d9a4b8e10ace2574df3de"

TARGET = {
    "branch": "fix/120-race-safe-key-material-reads",
    "sha": "0b050ae30b9809968d20ef0f6a01a4dca6a9f9b6",
    "pr": 530,
    "pr_head_sha": "0b050ae30b9809968d20ef0f6a01a4dca6a9f9b6",
    "merge_sha": "a18762aae173a8ae2c092588fdaa6c2c48a0f67c",
    "pr_files": {
        "backend/app/services/bundle_package_service.py",
        "backend/tests/test_bundle_package_key_bounds.py",
        "docs/package-service.md",
    },
    "current_main_files": {
        "backend/app/services/bundle_package_service.py": "63d2c8c64b6a175c79bb178f3e9394c220798e5c",
        "backend/tests/test_bundle_package_key_bounds.py": "724f035a43babaacd7daca8e4c93aec6a33ef838",
        "docs/package-service.md": "f2c59e6dd24c0952168164b3334d9f1389fcc451",
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

def audit():
    main_ref = github("GET", "git/ref/heads/main")
    main_sha = (main_ref.get("object") or {}).get("sha")
    if not main_sha:
        raise RuntimeError("Cannot read current main SHA")
    if github("GET", f"compare/{AUDIT_BASE}...{main_sha}").get("behind_by") != 0:
        raise RuntimeError("Pinned audit base is not an ancestor of current main")

    issue = github("GET", "issues/120")
    if issue.get("state") != "closed" or issue.get("state_reason") != "completed":
        raise RuntimeError("Issue #120 is not completed")

    pr = github("GET", f"pulls/{TARGET['pr']}")
    head = pr.get("head") or {}
    if not (
        pr.get("state") == "closed"
        and pr.get("merged_at")
        and (pr.get("base") or {}).get("ref") == "main"
        and head.get("ref") == TARGET["branch"]
        and head.get("sha") == TARGET["pr_head_sha"]
        and pr.get("merge_commit_sha") == TARGET["merge_sha"]
    ):
        raise RuntimeError("Merged PR #530 provenance or merge SHA changed")
    if changed_files(TARGET["pr"]) != TARGET["pr_files"]:
        raise RuntimeError("PR #530 changed-file scope drift")
    if github("GET", f"compare/{TARGET['merge_sha']}...{main_sha}").get("behind_by") != 0:
        raise RuntimeError("PR #530 merge is not in current main ancestry")

    for path, expected_sha in TARGET["current_main_files"].items():
        item = github("GET", f"contents/{path}?ref=main")
        if item.get("sha") != expected_sha:
            raise RuntimeError(f"Current bounded-key-read implementation changed: {path}")

    open_prs = pages("pulls?state=open")
    for item in open_prs:
        h, b = item.get("head") or {}, item.get("base") or {}
        if h.get("ref") == TARGET["branch"] and ((h.get("repo") or {}).get("full_name") == REPO):
            raise RuntimeError("An open PR uses target branch as head")
        if b.get("ref") == TARGET["branch"] and ((b.get("repo") or {}).get("full_name") == REPO):
            raise RuntimeError("An open PR uses target branch as base")

    all_prs = pages("pulls?state=all")
    target_prs = [
        item for item in all_prs
        if (item.get("head") or {}).get("ref") == TARGET["branch"]
        and ((item.get("head") or {}).get("repo") or {}).get("full_name") == REPO
    ]
    if len(target_prs) != 1 or target_prs[0].get("number") != TARGET["pr"]:
        raise RuntimeError("Target branch PR provenance changed")

    check_ref(TARGET["branch"], TARGET["sha"])
    print(
        "PASS fix/120-race-safe-key-material-reads: PR #530 merged; exact feature-branch head "
        "and changed-file scope match; current main contains the same bounded-reader implementation"
    )
    print("PASS: current service/tests/docs pinned, unprotected ref, no open PR dependency; dry-run only")

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
