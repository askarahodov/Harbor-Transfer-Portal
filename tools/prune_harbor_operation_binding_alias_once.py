#!/usr/bin/env python3
"""One-shot cleanup of the superseded Harbor operation-binding alias branch."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-harbor-operation-binding-alias-once"
ENABLE = "issue-513-harbor-operation-binding-alias"
AUDIT_BASE = "ddda010e37c5c4164f7d9a4b8e10ace2574df3de"

TARGET = {
    "branch": "feat/harbor-profile-operation-binding",
    "sha": "10eb2ac7b33f7d4d8fa2838a3ebcb6be68166df7",
    "expected_ahead": 11,
    "expected_files": {
        "backend/alembic/versions/0009_operation_harbor_profile.py",
        "backend/app/api/operations.py",
        "backend/app/db/models.py",
        "backend/app/schemas/operations.py",
        "backend/app/services/export_artifact_materializer.py",
        "backend/app/services/export_orchestrator.py",
        "backend/app/services/harbor_destination_validator.py",
        "backend/app/services/harbor_profiles.py",
        "backend/app/services/helm_oci_service.py",
        "backend/app/services/operation_manager.py",
        "backend/app/services/skopeo_service.py",
    },
    "replacement_pr": {
        "number": 372,
        "head_sha": "024094761f1b02aa0ab728a1b00ac2eab114edd9",
        "merge_sha": "52e904f630521113360e154661bc162b69a78886",
        "required_files": {
            "backend/alembic/versions/0010_operation_harbor_profile.py",
            "backend/app/api/operations.py",
            "backend/app/db/models.py",
            "backend/app/schemas/operations.py",
            "backend/app/services/export_artifact_materializer.py",
            "backend/app/services/export_orchestrator.py",
            "backend/app/services/harbor_destination_validator.py",
            "backend/app/services/harbor_profiles.py",
            "backend/app/services/helm_oci_service.py",
            "backend/app/services/operation_manager.py",
            "backend/app/services/skopeo_service.py",
        },
    },
    "issues": {352: "completed", 356: "completed", 360: "completed"},
    "current_main_files": {
        "backend/alembic/versions/0010_operation_harbor_profile.py": "677f64c5bd49de5d75e84fceb4e3aac95ad9840e",
        "backend/app/api/operations.py": "31229cf99b268377deb8dd01ebb063b618628535",
        "backend/app/db/models.py": "bafc93cb969238fd6259426a183026674573e3b5",
        "backend/app/schemas/operations.py": "fa119d9af6004627f70e81b290260d37b86b35b4",
        "backend/app/services/export_artifact_materializer.py": "20764faf49e88cc7cb1c197574ca1c35906ce1f6",
        "backend/app/services/export_orchestrator.py": "23ceca9263c296ad5fff2b621dda6933bd3c8e9a",
        "backend/app/services/harbor_destination_validator.py": "f73ac37b5f0bc8af4de0604b9a090788241bac58",
        "backend/app/services/harbor_profiles.py": "22999b85b55065d461ecb2cf0158141651ce1c97",
        "backend/app/services/helm_oci_service.py": "4feb498db75e402b40ddae20fbc56136f5595bec",
        "backend/app/services/operation_manager.py": "da9f2516b4da02a9399381733f69bac567f64ac0",
        "backend/app/services/skopeo_service.py": "5208bef10d727ae9637a3a3ed9c234a97e736b8f",
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

    for number, reason in TARGET["issues"].items():
        issue = github("GET", f"issues/{number}")
        if issue.get("state") != "closed" or issue.get("state_reason") != reason:
            raise RuntimeError(f"Harbor profiles issue #{number} is not completed")

    expected = TARGET["replacement_pr"]
    pr = github("GET", f"pulls/{expected['number']}")
    if not (
        pr.get("state") == "closed"
        and pr.get("merged_at")
        and (pr.get("base") or {}).get("ref") == "main"
        and (pr.get("head") or {}).get("sha") == expected["head_sha"]
        and pr.get("merge_commit_sha") == expected["merge_sha"]
    ):
        raise RuntimeError("Replacement PR #372 provenance or merge SHA changed")
    if not expected["required_files"].issubset(changed_files(expected["number"])):
        raise RuntimeError("PR #372 no longer covers the operation-binding replacement surface")
    if github("GET", f"compare/{expected['merge_sha']}...{AUDIT_BASE}").get("behind_by") != 0:
        raise RuntimeError("PR #372 merge is not in pinned main snapshot")

    delta = github("GET", f"compare/main...{TARGET['sha']}")
    if delta.get("ahead_by") != TARGET["expected_ahead"]:
        raise RuntimeError("Target branch unique commit count changed")
    if {item["filename"] for item in delta.get("files") or []} != TARGET["expected_files"]:
        raise RuntimeError("Target branch changed-file scope drift")

    for path, expected_sha in TARGET["current_main_files"].items():
        item = github("GET", f"contents/{path}?ref=main")
        if item.get("sha") != expected_sha:
            raise RuntimeError(f"Current operation-binding replacement changed: {path}")

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
        "PASS feat/harbor-profile-operation-binding: no PR provenance; issues #352/#356/#360 completed; "
        "PR #372 merged; exact 11-commit delta is replaced by the maintained operation-profile migration, "
        "immutable operation snapshots, profile-aware workers and regressions"
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
