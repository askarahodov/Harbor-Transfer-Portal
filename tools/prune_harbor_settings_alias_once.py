#!/usr/bin/env python3
"""One-shot cleanup of the superseded P3.3 Harbor-settings branch."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-harbor-settings-alias-once"
ENABLE = "issue-513-harbor-settings-alias"
AUDIT_BASE = "ddda010e37c5c4164f7d9a4b8e10ace2574df3de"

TARGET = {
    "branch": "feat/p3-3-harbor-settings",
    "sha": "95d740a712b112bbbe8a3805488c43fed695e2d0",
    "expected_ahead": 24,
    "expected_files": {
        ".env.example",
        "backend/Dockerfile",
        "backend/alembic/versions/0003_harbor_settings_audit.py",
        "backend/app/api/harbor.py",
        "backend/app/api/router.py",
        "backend/app/api/settings.py",
        "backend/app/config.py",
        "backend/app/db/models.py",
        "backend/app/schemas/settings.py",
        "backend/app/services/harbor_settings.py",
        "backend/tests/test_harbor_settings_api.py",
        "deploy/README.md",
        "docs/adr/ADR-005-harbor-tls-secrets.md",
        "docs/decisions.md",
        "docs/harbor-settings.md",
        "frontend/src/api/settings.ts",
        "frontend/src/views/SettingsView.spec.ts",
        "frontend/src/views/SettingsView.vue",
    },
    "original_pr": {
        "number": 44,
        "head_sha": "3d01ff082f7c79b0fe8a8e917825b473f5df26af",
        "merge_sha": "7caa418b45de1ce0cc2eff6846422c068b5a217b",
        "files": {
            ".env.example",
            "backend/alembic/versions/0003_harbor_settings_audit.py",
            "backend/app/api/harbor.py",
            "backend/app/api/router.py",
            "backend/app/api/settings.py",
            "backend/app/config.py",
            "backend/app/db/models.py",
            "backend/app/schemas/settings.py",
            "backend/app/services/harbor_client.py",
            "backend/app/services/harbor_settings.py",
            "backend/tests/test_harbor_settings_api.py",
            "docs/adr/ADR-005-harbor-secrets-tls.md",
            "docs/decisions.md",
            "frontend/src/views/SettingsView.spec.ts",
            "frontend/src/views/SettingsView.vue",
        },
    },
    "current_main_files": {
        ".env.example": "3d96493c8b46899b36ed34f36f33df74dd8cef84",
        "backend/Dockerfile": "8a8db116cf08b9f57013138015afcde9980681b7",
        "backend/alembic/versions/0003_harbor_settings_audit.py": "19c7d1274d40db398cf35a6513b4047d711bda8d",
        "backend/app/api/harbor.py": "5d55a114ca5faebf3ac9756a2f92e980b9b44d44",
        "backend/app/api/router.py": "58f544b270f85232b7b34c48c3329c851db94bc0",
        "backend/app/api/settings.py": "48ab917cffab84c4c02217065629ba4339c9354f",
        "backend/app/config.py": "5ff8e2c328919e1d6e6de7ed6121a19d00ea9171",
        "backend/app/db/models.py": "bafc93cb969238fd6259426a183026674573e3b5",
        "backend/app/schemas/settings.py": "56948aa0cbca9e7d63e702f2deb524d2b1a04b7d",
        "backend/app/services/harbor_client.py": "989c0386f2cbe3309dd0ded0ea909a1616d42f70",
        "backend/app/services/harbor_settings.py": "65c4424a8b6ac258667f34952a552548d42c53b4",
        "backend/tests/test_harbor_settings_api.py": "d6dfb567ac255777b0bcbb53f2f531de0d02c975",
        "deploy/README.md": "af455a74dc06729469ef40c458febe8b0b0079f3",
        "docs/adr/ADR-005-harbor-secrets-tls.md": "57d3ef8fac1c9408d45ab7feec8e1d6670f3495b",
        "docs/decisions.md": "1c8e19ec46c28a35304704cbd5a3fdbc2512c162",
        "docs/settings.md": "9ae2b1cd98ec176aa2f4191e6fdbd518d43f6670",
        "frontend/src/api/client.ts": "6d3a4aa269fd7864256245c1615afac77086dc66",
        "frontend/src/views/SettingsView.spec.ts": "1b85f8ca6a9195d91034d8fbdcc2a38ed019f0c0",
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

def audit():
    main_ref = github("GET", "git/ref/heads/main")
    main_sha = (main_ref.get("object") or {}).get("sha")
    if not main_sha:
        raise RuntimeError("Cannot read current main SHA")
    if github("GET", f"compare/{AUDIT_BASE}...{main_sha}").get("behind_by") != 0:
        raise RuntimeError("Pinned audit base is not an ancestor of current main")

    issue = github("GET", "issues/12")
    if issue.get("state") != "closed" or issue.get("state_reason") != "completed":
        raise RuntimeError("Parent Harbor settings issue #12 is not completed")

    expected = TARGET["original_pr"]
    pr = github("GET", f"pulls/{expected['number']}")
    head = pr.get("head") or {}
    if not (
        pr.get("state") == "closed"
        and pr.get("merged_at")
        and (pr.get("base") or {}).get("ref") == "main"
        and head.get("sha") == expected["head_sha"]
        and pr.get("merge_commit_sha") == expected["merge_sha"]
    ):
        raise RuntimeError("Replacement PR #44 provenance or merge SHA changed")
    if changed_files(expected["number"]) != expected["files"]:
        raise RuntimeError("Replacement PR #44 changed-file scope drift")
    if github("GET", f"compare/{expected['merge_sha']}...{AUDIT_BASE}").get("behind_by") != 0:
        raise RuntimeError("PR #44 merge is not in pinned main snapshot")

    delta = github("GET", f"compare/main...{TARGET['sha']}")
    if delta.get("ahead_by") != TARGET["expected_ahead"]:
        raise RuntimeError("Target branch unique commit count changed")
    if {item["filename"] for item in delta.get("files") or []} != TARGET["expected_files"]:
        raise RuntimeError("Target branch changed-file scope drift")

    # Current main moved the original one-purpose Settings API into the
    # maintained settings surface, folded Harbor settings into SettingsView,
    # and renamed/expanded the docs. Pin all maintained replacements.
    for path, expected_sha in TARGET["current_main_files"].items():
        item = github("GET", f"contents/{path}?ref=main")
        if item.get("sha") != expected_sha:
            raise RuntimeError(f"Current Harbor-settings replacement changed: {path}")

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
        "PASS feat/p3-3-harbor-settings: no PR provenance; issue #12 completed; "
        "PR #44 merged; all 24 unique commits' 18-file settings surface is replaced "
        "by current API/service/config/UI/docs and tests"
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
