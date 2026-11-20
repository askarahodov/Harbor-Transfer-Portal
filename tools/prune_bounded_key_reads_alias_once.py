#!/usr/bin/env python3
"""One-shot cleanup of the superseded P6.2.3 bounded-key-read alias branch."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-bounded-key-reads-alias-once"
ENABLE = "issue-513-bounded-key-reads-alias"
AUDIT_BASE = "ddda010e37c5c4164f7d9a4b8e10ace2574df3de"

TARGET = {
    "branch": "fix/120-bounded-key-reads",
    "sha": "8e97cda1e2700545057e142aa62ec1c459c8bb06",
    "expected_ahead": 3,
    "expected_files": {
        "backend/app/services/bundle_package_service.py",
        "backend/tests/test_bundle_key_material_bounds.py",
        "docs/package-service.md",
    },
    "original_pr": {
        129: {
            "head_sha": "cd3e80b36ef7d260091b5de4829547696ad73801",
            "merge_sha": "803a1f860ec47fb875428d1f4352d0cb5bb579ec",
            "files": {
                "backend/app/services/bundle_package_service.py",
                "backend/tests/test_bundle_package_key_bounds.py",
            },
        },
        530: {
            "head_sha": "0b050ae30b9809968d20ef0f6a01a4dca6a9f9b6",
            "merge_sha": "a18762aae173a8ae2c092588fdaa6c2c48a0f67c",
            "files": {
                "backend/app/services/bundle_package_service.py",
                "backend/tests/test_bundle_package_key_bounds.py",
                "docs/package-service.md",
            },
        },
    },
    "current_main_files": {
        "backend/app/services/bundle_package_service.py": "63d2c8c64b6a175c79bb178f3e9394c220798e5c",
        "backend/tests/test_bundle_package_key_bounds.py": "724f035a43babaacd7daca8e4c93aec6a33ef838",
        "backend/tests/test_bundle_package_service.py": "361733400b903028ed9cf77fe9b149251fd0f8bd",
        "docs/package-service.md": "f2c59e6dd24c0952168164b3334d9f1389fcc451",
        ".env.example": "3d96493c8b46899b36ed34f36f33df74dd8cef84",
        "backend/app/config.py": "5ff8e2c328919e1d6e6de7ed6121a19d00ea9171",
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

def check_issue():
    issue = github("GET", "issues/120")
    if issue.get("state") != "closed" or issue.get("state_reason") != "completed":
        raise RuntimeError("Issue #120 is not completed")

def check_pr(number: int, expected: dict, *, ancestor: str):
    pr = github("GET", f"pulls/{number}")
    if not (
        pr.get("state") == "closed"
        and pr.get("merged_at")
        and (pr.get("base") or {}).get("ref") == "main"
        and (pr.get("head") or {}).get("sha") == expected["head_sha"]
        and pr.get("merge_commit_sha") == expected["merge_sha"]
    ):
        raise RuntimeError(f"Replacement PR #{number} provenance/merge SHA changed")
    if changed_files(number) != expected["files"]:
        raise RuntimeError(f"Replacement PR #{number} changed-file scope drift")
    if github("GET", f"compare/{expected['merge_sha']}...{ancestor}").get("behind_by") != 0:
        raise RuntimeError(f"PR #{number} merge is not in expected main ancestry")

def audit():
    main_ref = github("GET", "git/ref/heads/main")
    main_sha = (main_ref.get("object") or {}).get("sha")
    if not main_sha:
        raise RuntimeError("Cannot read current main SHA")
    if github("GET", f"compare/{AUDIT_BASE}...{main_sha}").get("behind_by") != 0:
        raise RuntimeError("Pinned audit base is not an ancestor of current main")

    check_issue()
    check_pr(129, TARGET["original_pr"][129], ancestor=AUDIT_BASE)
    check_pr(530, TARGET["original_pr"][530], ancestor=main_sha)

    delta = github("GET", f"compare/main...{TARGET['sha']}")
    if delta.get("ahead_by") != TARGET["expected_ahead"]:
        raise RuntimeError("Target branch unique commit count changed")
    if {item["filename"] for item in delta.get("files") or []} != TARGET["expected_files"]:
        raise RuntimeError("Target branch changed-file scope drift")

    # The old branch resolves configured key paths before lstat(), unlike
    # current main. Do not copy that implementation back: PR #129's absolute
    # path + lstat symlink protection is retained, and PR #530 adds limit+1
    # reads to close the post-stat file-growth allocation window.
    for path, expected_sha in TARGET["current_main_files"].items():
        item = github("GET", f"contents/{path}?ref=main")
        if item.get("sha") != expected_sha:
            raise RuntimeError(f"Current bounded-key-read replacement changed: {path}")

    open_prs = pages("pulls?state=open")
    for pr in open_prs:
        head, base = pr.get("head") or {}, pr.get("base") or {}
        if head.get("ref") == TARGET["branch"] and ((head.get("repo") or {}).get("full_name") == REPO):
            raise RuntimeError("An open PR uses target branch as head")
        if base.get("ref") == TARGET["branch"] and ((base.get("repo") or {}).get("full_name") == REPO):
            raise RuntimeError("An open PR uses target branch as base")

    all_prs = pages("pulls?state=all")
    if any(
        (item.get("head") or {}).get("ref") == TARGET["branch"]
        and ((item.get("head") or {}).get("repo") or {}).get("full_name") == REPO
        for item in all_prs
    ):
        raise RuntimeError("Target branch has PR provenance; manual review required")

    check_ref(TARGET["branch"], TARGET["sha"])
    print(
        "PASS fix/120-bounded-key-reads: no PR provenance; issue #120 completed; "
        "PR #129 and #530 merged with exact scopes; main retains lstat/absolute path "
        "symlink protection, pre-read size validation and bounded limit+1 reads"
    )
    print("PASS: exact live SHA, current service/tests/docs/env pins, unprotected ref; dry-run only")

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
