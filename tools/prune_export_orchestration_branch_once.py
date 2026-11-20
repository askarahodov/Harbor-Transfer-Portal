#!/usr/bin/env python3
"""One-shot cleanup of the merged SOURCE export-orchestration branch."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-export-orchestration-branch-once"
ENABLE = "issue-513-export-orchestration-branch"
AUDIT_BASE = "ddda010e37c5c4164f7d9a4b8e10ace2574df3de"

TARGET = {
    "branch": "feat/p4-5-export-orchestration",
    "sha": "734ead37ff37f2aa434a2c994431e55f1cf4c474",
    "original_pr": 80,
    "original_head_sha": "39f544142587a65653ce3faae40c708e5c2f6629",
    "original_merge_sha": "33526abc8a8a798a9ff445adc1dcc0b764993fa1",
    "post_pr_commits": 5,
    "post_pr_files": {
        "README.md",
        "backend/tests/test_exports_api.py",
        "docs/README.md",
        "docs/architecture.md",
        "docs/export-orchestration.md",
    },
    "original_pr_files": {
        "backend/alembic/versions/0005_export_bundle_metadata.py",
        "backend/app/api/exports.py",
        "backend/app/api/harbor.py",
        "backend/app/api/operations.py",
        "backend/app/api/router.py",
        "backend/app/db/models.py",
        "backend/app/domain/artifacts.py",
        "backend/app/main.py",
        "backend/app/schemas/exports.py",
        "backend/app/schemas/harbor.py",
        "backend/app/schemas/operations.py",
        "backend/app/services/export_orchestrator.py",
        "backend/app/services/export_recovery.py",
        "backend/tests/test_export_cancellation.py",
        "backend/tests/test_export_orchestrator.py",
        "backend/tests/test_export_recovery.py",
        "backend/tests/test_exports_api.py",
        "docs/export-orchestration.md",
    },
    "current_main_files": {
        "README.md": "b54b66c41580843e948a7cfc72402e7e16166eed",
        "backend/tests/test_exports_api.py": "434b559c60aeebc50a8380a02e123db78e15cd48",
        "backend/tests/test_export_recovery.py": "b682bb5e92d8f900c3f0287de6bf5a6ca72b58fb",
        "backend/tests/test_export_cancellation.py": "1afd0585847c253755c598d62b008d881c50314e",
        "backend/tests/test_export_orchestrator.py": "b2946a8b2745bb16ca231d7904b3d1ada9a6e2bf",
        "backend/app/services/export_recovery.py": "6d104f4e726b6394fe90ee33a72d3093bd2879a8",
        "docs/README.md": "2c0d9f99742475d58317b648367d7f6b3036ac85",
        "docs/architecture.md": "fefbe08098a9bdcc1dae13654d5094823fa83dc8",
        "docs/export-orchestration.md": "8e3c6722a4f55f54555c67ed390961e5518f9c14",
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

    issue = github("GET", "issues/17")
    if issue.get("state") != "closed" or issue.get("state_reason") != "completed":
        raise RuntimeError("SOURCE export issue #17 is not completed")

    pr = github("GET", f"pulls/{TARGET['original_pr']}")
    head = pr.get("head") or {}
    if not (
        pr.get("state") == "closed"
        and pr.get("merged_at")
        and (pr.get("base") or {}).get("ref") == "main"
        and head.get("ref") == TARGET["branch"]
        and head.get("sha") == TARGET["original_head_sha"]
        and pr.get("merge_commit_sha") == TARGET["original_merge_sha"]
    ):
        raise RuntimeError("Merged PR #80 provenance or merge SHA changed")
    if changed_files(TARGET["original_pr"]) != TARGET["original_pr_files"]:
        raise RuntimeError("Original PR #80 changed-file scope drift")
    if github("GET", f"compare/{TARGET['original_merge_sha']}...{AUDIT_BASE}").get("behind_by") != 0:
        raise RuntimeError("PR #80 merge is not in pinned main snapshot")

    delta = github("GET", f"compare/{TARGET['original_head_sha']}...{TARGET['sha']}")
    if delta.get("ahead_by") != TARGET["post_pr_commits"] or delta.get("behind_by") != 0:
        raise RuntimeError("Post-PR branch history changed")
    if {item["filename"] for item in delta.get("files") or []} != TARGET["post_pr_files"]:
        raise RuntimeError("Post-PR documentation/test scope drift")

    # Main has a richer current SOURCE export document, a larger API test
    # suite, and dedicated recovery/cancellation/orchestrator regression suites.
    # The docs explicitly retain restart recovery, stable errors and the
    # publication/cancellation barriers from the branch's post-merge notes.
    for path, expected_sha in TARGET["current_main_files"].items():
        item = github("GET", f"contents/{path}?ref=main")
        if item.get("sha") != expected_sha:
            raise RuntimeError(f"Current export-orchestration replacement changed: {path}")

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
    if len(target_prs) != 1 or target_prs[0].get("number") != TARGET["original_pr"]:
        raise RuntimeError("Target branch PR provenance changed")

    check_ref(TARGET["branch"], TARGET["sha"])
    print(
        "PASS feat/p4-5-export-orchestration: PR #80 merged and issue #17 completed; "
        "five post-merge commits are docs/tests only and current main retains richer "
        "export docs, API regressions, cancellation barrier and restart recovery coverage"
    )
    print("PASS: exact live SHA, unprotected ref, no open PR dependency; dry-run only")

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
