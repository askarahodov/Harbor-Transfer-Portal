#!/usr/bin/env python3
"""One-shot cleanup of the superseded P7.3 application-baseline snapshot branch."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-superseded-p7-baseline-branch-once"
ENABLE = "issue-513-p7-baseline-snapshot"
AUDIT_BASE = "ddda010e37c5c4164f7d9a4b8e10ace2574df3de"

TARGET = {
    "branch": "feat/p7-3-ci-baseline",
    "sha": "6299086a43998322f4c826096801490c7a094d4f",
    "expected_ahead": 25,
    "expected_files": {
        ".dockerignore",
        ".env.example",
        "Makefile",
        "README.md",
        "backend/Dockerfile",
        "compose.yaml",
        "deploy/README.md",
        "deploy/smoke-compose.sh",
        "docs/adr/ADR-008-frontend-ui-kit.md",
        "docs/decisions.md",
        "frontend/Dockerfile",
        "frontend/docker-entrypoint.d/40-runtime-config.sh",
        "frontend/eslint.config.js",
        "frontend/index.html",
        "frontend/nginx.conf",
        "frontend/package.json",
        "frontend/public/runtime-config.js",
        "frontend/src/App.vue",
        "frontend/src/api/client.ts",
        "frontend/src/components/ContourBadge.spec.ts",
        "frontend/src/components/ContourBadge.vue",
        "frontend/src/components/PageShell.vue",
        "frontend/src/components/StatePlaceholder.vue",
        "frontend/src/components/StatusBadge.vue",
        "frontend/src/env.d.ts",
        "frontend/src/main.ts",
        "frontend/src/router/index.ts",
        "frontend/src/router/router.spec.ts",
        "frontend/src/stores/runtime.spec.ts",
        "frontend/src/stores/runtime.ts",
        "frontend/src/styles/base.css",
        "frontend/src/styles/tokens.css",
        "frontend/src/views/DashboardView.vue",
        "frontend/src/views/ExportView.vue",
        "frontend/src/views/HistoryView.vue",
        "frontend/src/views/ImportView.vue",
        "frontend/src/views/LoginView.vue",
        "frontend/src/views/SettingsView.vue",
        "frontend/tsconfig.app.json",
        "frontend/tsconfig.json",
        "frontend/tsconfig.node.json",
        "frontend/vite.config.ts",
    },
    "ci_pr": {
        "number": 39,
        "head_sha": "f8a185601b68f1b319d0c4d82c0da0436268c635",
        "merge_sha": "7a8a430e9e5d774bc5cd1bc7f0d6305293dfd1d1",
        "files": {
            ".github/workflows/ci.yml",
            "README.md",
            "backend/alembic/env.py",
            "backend/alembic/versions/0001_initial.py",
            "backend/app/api/auth.py",
            "backend/app/api/users.py",
            "backend/app/auth/dependencies.py",
            "backend/app/auth/security.py",
            "backend/app/db/base.py",
            "backend/app/db/models.py",
            "backend/app/db/repositories.py",
            "backend/app/domain/operations.py",
            "backend/app/domain/protocol.py",
            "backend/app/services/harbor_client.py",
            "backend/tests/test_auth.py",
            "backend/tests/test_auth_api.py",
            "backend/tests/test_bundle_protocol.py",
            "backend/tests/test_database.py",
            "docs/testing.md",
        },
    },
    "current_main_files": {
        ".dockerignore": "a7501b07b9fd2af4b03745e1873d380bbe715395",
        ".env.example": "3d96493c8b46899b36ed34f36f33df74dd8cef84",
        "Makefile": "2ce96242880a6256da10945f02ba3bbd90481106",
        "README.md": "b54b66c41580843e948a7cfc72402e7e16166eed",
        "backend/Dockerfile": "8a8db116cf08b9f57013138015afcde9980681b7",
        "compose.yaml": "d68b384d97ab355a905e80dc9f4975f1bcbb7398",
        "deploy/README.md": "af455a74dc06729469ef40c458febe8b0b0079f3",
        "deploy/smoke-compose.sh": "9abb32af0ca92d02382f7e9093ba7e64b6ab94ae",
        "docs/adr/ADR-008-frontend-ui-kit.md": "0420d48bdd15f7f24660b57ed87561bffab1ae97",
        "docs/decisions.md": "1c8e19ec46c28a35304704cbd5a3fdbc2512c162",
        "frontend/Dockerfile": "6afa94681edbc3ec2cd21a694e932225668ec555",
        "frontend/docker-entrypoint.d/40-runtime-config.sh": "64f731d383831cbe8080c3ebe859a301eeccda50",
        "frontend/eslint.config.js": "2f4755e0a104f2b253810c1cfa08684ded37b4b7",
        "frontend/index.html": "543d8cd6bc5ac6455e07b919eb2aa4822e19c188",
        "frontend/nginx.conf": "0cc759570cbc41eb0ce90f0ee1073e8df7971871",
        "frontend/package.json": "59c8e98096bbe5bc27985c8b725fa8aa3d3878ff",
        "frontend/public/runtime-config.js": "ba8b094e297ab295b81a15057f27d777ce099c40",
        "frontend/src/App.vue": "64aba7a54760fc8f3bc549bfb9c18c25f474bdc1",
        "frontend/src/api/client.ts": "6d3a4aa269fd7864256245c1615afac77086dc66",
        "frontend/src/components/ContourBadge.spec.ts": "0857757383f5494a2f38eb654c413db3e797d2a7",
        "frontend/src/components/ContourBadge.vue": "21238988f6a8ffd091c6d08b1e6816674bb17fd6",
        "frontend/src/components/PageShell.vue": "14e43ef93224b66f745092f413beaf26d88ddfdd",
        "frontend/src/components/StatePlaceholder.vue": "b98d63de96845f7f77f4816a3f5d57eb567f76b2",
        "frontend/src/components/StatusBadge.vue": "61da97bbc1dd696239317bd55595b64905f1d9ca",
        "frontend/src/env.d.ts": "5c6980309aa53c4ad575e378661462f3f65bf5ff",
        "frontend/src/main.ts": "7441405b60a3fedde8bde312fabe58536a5d0d1b",
        "frontend/src/router/index.ts": "3590a27c0161dddac550d0be6c0d0e795e774dae",
        "frontend/src/router/router.spec.ts": "daaeb0000a2ddc17d818654966296034186ea725",
        "frontend/src/stores/runtime.spec.ts": "f80af8b7f8844640f2e3010e0ea40991d3edcc74",
        "frontend/src/stores/runtime.ts": "a084699d3d13f96886aa39c53fa064cbae90064e",
        "frontend/src/styles/base.css": "687ec2ae37c9edd47aec608f5c030b95c6f3f7ed",
        "frontend/src/styles/tokens.css": "668394cfb47e00deae89b2284ab2af4330658264",
        "frontend/src/views/DashboardView.vue": "1b62f7d2926ff01ca9b25c8865803a858194aa64",
        "frontend/src/views/ExportView.vue": "8dec7ad86b2cda233c5a7b8fe6dc06c3157f2000",
        "frontend/src/views/HistoryView.vue": "0fb3727686a79b66e198a770f0a48a7b6a94ceb0",
        "frontend/src/views/ImportView.vue": "8a3b97059e4ae3e817676527875bf7889beb366d",
        "frontend/src/views/LoginView.vue": "0661a6f7a98a27ddc898adaf7a9a912472ffa6ea",
        "frontend/src/views/SettingsView.vue": "916934a43416d3a669af7818e06574ad5f6fec1b",
        "frontend/tsconfig.app.json": "11935154ad886a9055e0e3797dc6d9841b791a8a",
        "frontend/tsconfig.json": "1ffef600d959ec9e396d5a260bd3f5b927b2cef8",
        "frontend/tsconfig.node.json": "7cbfedf067e793fb3153eb7f57ef60059c47fa3b",
        "frontend/vite.config.ts": "211764e208fc5ab4c1ad18a9ef338cb1f5082d58",
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

    # PR #39 maintains the real path-aware CI baseline. It deliberately leaves
    # issue #26 open for its remaining definition-of-done items; this cleanup
    # does not close or alter that issue.
    ci_pr = TARGET["ci_pr"]
    pr = github("GET", f"pulls/{ci_pr['number']}")
    if not (
        pr.get("state") == "closed"
        and pr.get("merged_at")
        and (pr.get("base") or {}).get("ref") == "main"
        and (pr.get("head") or {}).get("sha") == ci_pr["head_sha"]
        and pr.get("merge_commit_sha") == ci_pr["merge_sha"]
    ):
        raise RuntimeError("CI baseline PR #39 provenance or merge SHA changed")
    if changed_files(ci_pr["number"]) != ci_pr["files"]:
        raise RuntimeError("CI baseline PR #39 changed-file scope drift")
    if github("GET", f"compare/{ci_pr['merge_sha']}...{AUDIT_BASE}").get("behind_by") != 0:
        raise RuntimeError("PR #39 merge is not in pinned main snapshot")

    delta = github("GET", f"compare/main...{TARGET['sha']}")
    if delta.get("ahead_by") != TARGET["expected_ahead"]:
        raise RuntimeError("Target branch unique commit count changed")
    if {item["filename"] for item in delta.get("files") or []} != TARGET["expected_files"]:
        raise RuntimeError("Target branch changed-file scope drift")

    for path, expected_sha in TARGET["current_main_files"].items():
        item = github("GET", f"contents/{path}?ref=main")
        if item.get("sha") != expected_sha:
            raise RuntimeError(f"Current main replacement changed: {path}")

    open_prs = pages("pulls?state=open")
    for item in open_prs:
        h, b = item.get("head") or {}, item.get("base") or {}
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
    print(
        "PASS feat/p7-3-ci-baseline: no PR provenance; exact 25-commit/42-file snapshot is "
        "superseded by current main Docker/Compose/backend/frontend/docs and runtime implementation; "
        "PR #39 CI baseline remains merged; issue #26 remains untouched"
    )
    print("PASS: all 42 current-main file SHAs pinned, exact live SHA, unprotected ref; dry-run only")

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
