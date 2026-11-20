#!/usr/bin/env python3
"""One-shot cleanup of the closed duplicate P6.2.2 transfer-policy branch."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-superseded-transfer-policies-branch-once"
ENABLE = "issue-513-superseded-transfer-policies"
AUDIT_BASE = "26d5dacc2b842d497205cad528b36e3c2e20fdbe"

TARGET = {
    "branch": "feat/p6-2-2-transfer-policies",
    "sha": "3636d3e55d1a35956df69b8c3bfcad3f97772a25",
    "original_pr": 113,
    "original_head_sha": "a153c0b8afc4011668acace0c62bf83f6242e5aa",
    "replacement_pr": 112,
    "replacement_head_sha": "11b42e44f8c7aae147c46adeec22a764af985902",
    "replacement_merge_sha": "67f5273bcd54f91cf55d0f7b500c7cc9626a38d6",
    "unique_post_pr_file": "docs/admin-transfer-policies.md",
    "unique_post_pr_commit": "3636d3e55d1a35956df69b8c3bfcad3f97772a25",
    "replacement_doc": "docs/transfer-policies.md",
    "replacement_doc_sha": "0fd6b7d09444809cd162721df20e057035f5db79",
    "required_current_files": {
        "backend/app/api/router.py": "58f544b270f85232b7b34c48c3329c851db94bc0",
        "backend/app/main.py": "5b14160c1a3639427e5249b30835e879aef79dfc",
        "backend/app/api/transfer_settings.py": "ac06b098756c5046ff03e56851239f497b3406b9",
        "backend/app/schemas/transfer_policy.py": "df7778ecb1dcad037833cb4034b479805038379c",
        "backend/app/services/transfer_policy.py": "b91b92995aa95e99a8819c5f331334be78a92271",
        "backend/app/services/import_preview_projection.py": "23eefded29aca647d878d0bf06785883cd1ff053",
        "backend/tests/test_transfer_settings_api.py": "5656e0b33d2ca0b70d9faadb12c3a182ef67a4ef",
        "backend/tests/test_transfer_settings_discovery.py": "be54bed00a99328fa540fa35446329fae296e61e",
        "docs/transfer-policies.md": "0fd6b7d09444809cd162721df20e057035f5db79",
        "frontend/src/views/SettingsView.vue": "916934a43416d3a669af7818e06574ad5f6fec1b",
    },
    "original_pr_files": {
        "backend/app/api/router.py",
        "backend/app/api/settings.py",
        "backend/app/main.py",
        "backend/app/schemas/settings.py",
        "backend/app/services/import_preview_projection.py",
        "backend/app/services/transfer_settings.py",
        "backend/tests/test_transfer_settings_api.py",
        "backend/tests/test_transfer_settings_discovery.py",
        "docs/admin-transfer-policies.md",
        "frontend/src/views/SettingsView.spec.ts",
        "frontend/src/views/SettingsView.vue",
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

def check_original_pr():
    number = TARGET["original_pr"]
    pr = github("GET", f"pulls/{number}")
    head = pr.get("head") or {}
    if not (
        pr.get("state") == "closed"
        and not pr.get("merged_at")
        and (pr.get("base") or {}).get("ref") == "main"
        and head.get("ref") == TARGET["branch"]
        and head.get("sha") == TARGET["original_head_sha"]
        and ((head.get("repo") or {}).get("full_name") in (None, REPO))
    ):
        raise RuntimeError(f"Original PR #{number} state/provenance changed")
    if changed_files(number) != TARGET["original_pr_files"]:
        raise RuntimeError("Original PR #113 changed-file scope drift")

def check_replacement():
    number = TARGET["replacement_pr"]
    pr = github("GET", f"pulls/{number}")
    if not (
        pr.get("state") == "closed"
        and pr.get("merged_at")
        and (pr.get("base") or {}).get("ref") == "main"
        and (pr.get("head") or {}).get("sha") == TARGET["replacement_head_sha"]
        and pr.get("merge_commit_sha") == TARGET["replacement_merge_sha"]
    ):
        raise RuntimeError("Replacement PR #112 is not the expected merged-to-main change")
    if TARGET["replacement_doc"] not in changed_files(number):
        raise RuntimeError("Replacement PR #112 does not contain transfer policy docs")
    ancestry = github("GET", f"compare/{TARGET['replacement_merge_sha']}...{AUDIT_BASE}")
    if ancestry.get("behind_by") != 0:
        raise RuntimeError("Replacement PR #112 merge is not in pinned main snapshot")

def check_superseding_main_files():
    for path, expected_sha in TARGET["required_current_files"].items():
        item = github("GET", f"contents/{path}?ref=main")
        if item.get("sha") != expected_sha:
            raise RuntimeError(f"Current main file changed or is missing: {path}")
    doc = github("GET", f"contents/{TARGET['replacement_doc']}?ref=main")
    if doc.get("sha") != TARGET["replacement_doc_sha"]:
        raise RuntimeError("Current transfer policy documentation changed")
    projection = github("GET", "contents/backend/app/services/import_preview_projection.py?ref=main")
    if projection.get("sha") != TARGET["required_current_files"]["backend/app/services/import_preview_projection.py"]:
        raise RuntimeError("Physical incoming archive-size implementation changed")

def audit():
    main_ref = github("GET", "git/ref/heads/main")
    main_sha = (main_ref.get("object") or {}).get("sha")
    if not main_sha:
        raise RuntimeError("Cannot read current main SHA")
    ancestry = github("GET", f"compare/{AUDIT_BASE}...{main_sha}")
    if ancestry.get("behind_by") != 0:
        raise RuntimeError("Pinned audit base is not an ancestor of current main")

    open_prs = pages("pulls?state=open")
    for pr in open_prs:
        head, base = pr.get("head") or {}, pr.get("base") or {}
        if head.get("ref") == TARGET["branch"] and ((head.get("repo") or {}).get("full_name") == REPO):
            raise RuntimeError("An open PR uses target branch as head")
        if base.get("ref") == TARGET["branch"] and ((base.get("repo") or {}).get("full_name") == REPO):
            raise RuntimeError("An open PR uses target branch as base")

    check_ref(TARGET["branch"], TARGET["sha"])
    check_original_pr()
    check_replacement()

    delta = github("GET", f"compare/{TARGET['original_head_sha']}...{TARGET['sha']}")
    if delta.get("ahead_by") != 1 or delta.get("behind_by") != 0:
        raise RuntimeError("Target branch no longer has exactly one post-PR commit")
    files = delta.get("files") or []
    if len(files) != 1 or files[0].get("filename") != TARGET["unique_post_pr_file"]:
        raise RuntimeError("Post-PR commit changed-file scope drift")
    if delta.get("commits", [{}])[0].get("sha") != TARGET["unique_post_pr_commit"]:
        raise RuntimeError("Post-PR commit identity changed")

    check_superseding_main_files()
    print(
        "PASS feat/p6-2-2-transfer-policies: exact live SHA; PR #113 closed-unmerged; "
        "PR #112 merged to main; all current replacement files and physical incoming "
        "archive-size implementation verified; only post-PR commit is obsolete legacy docs"
    )
    print("PASS: no open PR dependency; branch unprotected and unchanged; dry-run only")

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
