#!/usr/bin/env python3
"""One-shot cleanup of the superseded P6.2.3 key-management review alias."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

from atomic_ref_cleanup import delete_refs

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-key-management-review-alias-once"
ENABLE = "issue-513-key-management-review-alias"
AUDIT_BASE = "ddda010e37c5c4164f7d9a4b8e10ace2574df3de"

TARGET = {
    "branch": "review/p6-2-3-key-management",
    "sha": "666241ca6be3853c2ea6bc5526021e5d3c52de61",
    "expected_ahead": 15,
    "expected_files": {
        ".env.example",
        "backend/app/api/key_settings.py",
        "backend/app/api/router.py",
        "backend/app/config.py",
        "backend/app/schemas/keys.py",
        "backend/app/services/key_management.py",
        "backend/app/services/key_material.py",
        "backend/tests/test_key_management_api.py",
        "docs/README.md",
        "docs/admin-key-management.md",
        "docs/key-management.md",
        "frontend/src/components/KeyManagementPanel.spec.ts",
        "frontend/src/components/KeyManagementPanel.vue",
        "frontend/src/views/SettingsView.vue",
    },
    "issues": {23: "completed", 110: "completed", 118: "completed", 121: "completed"},
    "replacement_prs": {
        117: {
            "head_sha": "1127cdc32c36662c3504ebf14662b821385cfe3e",
            "merge_sha": "e585df912f78278aaa5d4ff6e465a5ac950e14be",
            "files": {
                ".env.example",
                "backend/app/api/key_settings.py",
                "backend/app/api/router.py",
                "backend/app/config.py",
                "backend/app/schemas/keys.py",
                "backend/app/services/key_management.py",
                "backend/app/services/key_material.py",
                "backend/tests/test_key_management_api.py",
                "docs/README.md",
                "docs/key-management.md",
                "frontend/src/components/KeyManagementPanel.spec.ts",
                "frontend/src/components/KeyManagementPanel.vue",
                "frontend/src/views/SettingsView.vue",
            },
        },
        119: {
            "head_sha": "cb9f28b2bbe8ce73fca01d1eb84eb08210d3c02a",
            "merge_sha": "630f350a6d7409c559933383e7270f88ba382be6",
            "files": {
                "backend/app/services/key_management.py",
                "backend/tests/test_key_management_atomic_commit.py",
            },
        },
        125: {
            "head_sha": "11622a17453783e92e863c354fb980341d9f290f",
            "merge_sha": "936247f5c5ad52303adf767ef6f248f22178707b",
            "files": {
                "backend/app/api/key_settings.py",
                "backend/app/schemas/keys.py",
                "backend/app/services/key_management.py",
                "backend/tests/test_key_management_api.py",
                "backend/tests/test_key_management_hardening.py",
                "docs/key-management.md",
                "frontend/src/components/KeyManagementPanel.spec.ts",
                "frontend/src/components/KeyManagementPanel.vue",
            },
        },
    },
    "current_main_files": {
        ".env.example": "3d96493c8b46899b36ed34f36f33df74dd8cef84",
        "backend/app/api/key_settings.py": "54b12b920308e1ef9c99e32cb68645835db42c14",
        "backend/app/api/router.py": "58f544b270f85232b7b34c48c3329c851db94bc0",
        "backend/app/config.py": "5ff8e2c328919e1d6e6de7ed6121a19d00ea9171",
        "backend/app/schemas/keys.py": "2b91217efb19c02fd94091666ea0dfbc5c769c8e",
        "backend/app/services/key_management.py": "718ee95cb784efcce64b951e0fcc7a1cc3b2b994",
        "backend/app/services/key_material.py": "21703dd6fde443ed1778de8558d5b5052f99d698",
        "backend/tests/test_key_management_api.py": "0b3bd67e4b9732d72150166e42536ceca746c635",
        "backend/tests/test_key_management_atomic_commit.py": "8fbbf7edf44b37e8eb0b3eb8f8c23458ba73d3d3",
        "backend/tests/test_key_management_hardening.py": "bc41da6ccbfcd40eed2b4ae07cb8f11d8351f74f",
        "docs/README.md": "2c0d9f99742475d58317b648367d7f6b3036ac85",
        "docs/key-management.md": "b8aa1e082d7cd18d51245bef353ba396382d8bda",
        "docs/admin-guide.md": "db2669bbff616a58705e54fb3ec6bfc35794c76a",
        "frontend/src/components/KeyManagementPanel.spec.ts": "f30ef35d66ea57826718b30eac2be0f4047195c0",
        "frontend/src/components/KeyManagementPanel.vue": "08985d2f6addf4802de29ccafc29dae8c0fdc44f",
        "frontend/src/views/SettingsView.vue": "916934a43416d3a669af7818e06574ad5f6fec1b",
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

def read_ref_sha(name: str) -> str | None:
    encoded = quote(name, safe="/")
    try:
        ref = github("GET", f"git/ref/heads/{encoded}")
    except RuntimeError as error:
        if "404" in str(error) or "Not Found" in str(error):
            return None
        raise
    if ref.get("ref") != f"refs/heads/{name}":
        raise RuntimeError(f"Unexpected ref response for {name}")
    sha = (ref.get("object") or {}).get("sha")
    if not sha:
        raise RuntimeError(f"Ref has no object SHA: {name}")
    return sha

def target_already_absent() -> bool:
    sha = read_ref_sha(TARGET["branch"])
    if sha is None:
        print("PASS: target ref is already absent; one-shot cleanup is a no-op")
        return True
    if sha != TARGET["sha"]:
        raise RuntimeError("Target ref moved since audit")
    return False

def audit():
    if target_already_absent():
        return False
    main_ref = github("GET", "git/ref/heads/main")
    main_sha = (main_ref.get("object") or {}).get("sha")
    if not main_sha:
        raise RuntimeError("Cannot read current main SHA")
    if github("GET", f"compare/{AUDIT_BASE}...{main_sha}").get("behind_by") != 0:
        raise RuntimeError("Pinned audit base is not an ancestor of current main")

    for number, reason in TARGET["issues"].items():
        issue = github("GET", f"issues/{number}")
        if issue.get("state") != "closed" or issue.get("state_reason") != reason:
            raise RuntimeError(f"Key-management issue #{number} is not completed")

    for number, expected in TARGET["replacement_prs"].items():
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
        if github("GET", f"compare/{expected['merge_sha']}...{AUDIT_BASE}").get("behind_by") != 0:
            raise RuntimeError(f"Replacement PR #{number} merge is not in pinned main snapshot")

    delta = github("GET", f"compare/main...{TARGET['sha']}")
    if delta.get("ahead_by") != TARGET["expected_ahead"]:
        raise RuntimeError("Target branch unique commit count changed")
    if {item["filename"] for item in delta.get("files") or []} != TARGET["expected_files"]:
        raise RuntimeError("Target branch changed-file scope drift")

    for path, expected_sha in TARGET["current_main_files"].items():
        item = github("GET", f"contents/{path}?ref=main")
        if item.get("sha") != expected_sha:
            raise RuntimeError(f"Current key-management replacement changed: {path}")

    open_prs = pages("pulls?state=open")
    for pr in open_prs:
        h, b = pr.get("head") or {}, pr.get("base") or {}
        if h.get("ref") == TARGET["branch"] and ((h.get("repo") or {}).get("full_name") == REPO):
            raise RuntimeError("An open PR uses target branch as head")
        if b.get("ref") == TARGET["branch"] and ((b.get("repo") or {}).get("full_name") == REPO):
            raise RuntimeError("An open PR uses target branch as base")

    all_prs = pages("pulls?state=all")
    if any(
        (item.get("head") or {}).get("ref") == TARGET["branch"]
        and ((item.get("head") or {}).get("repo") or {}).get("full_name") == REPO
        for item in all_prs
    ):
        raise RuntimeError("Target branch has PR provenance; manual review required")

    check_ref(TARGET["branch"], TARGET["sha"])
    own_sha = read_ref_sha(OWN)
    if own_sha is None:
        raise RuntimeError("Cleanup branch is missing before the compare-and-swap dry-run")
    check_ref(OWN, own_sha)
    print(delete_refs(REPO, [(TARGET["branch"], TARGET["sha"]), (OWN, own_sha)], dry_run=True))
    print(
        "PASS review/p6-2-3-key-management: no PR provenance; issues #23/#110/#118/#121 completed; "
        "PRs #117/#119/#125 merged; current main retains managed SOURCE/TARGET key API, "
        "atomic rotation/replacement, hardening tests, UI and full docs"
    )
    print("PASS: exact live SHA, 15-commit/14-path delta, current replacement SHAs; dry-run only")
    return True

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
    should_delete = audit()
    if not should_delete:
        return 0
    if not apply:
        print("DRY RUN: no refs deleted")
        return 0

    own_sha = own_merged_pr()
    refs = [(TARGET["branch"], TARGET["sha"]), (OWN, own_sha)]
    for name, expected_sha in refs:
        check_ref(name, expected_sha)
    print(delete_refs(REPO, refs, dry_run=False))
    print("DELETED: exact target ref and cleanup ref via atomic compare-and-swap")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        raise SystemExit(1)
