#!/usr/bin/env python3
"""One-shot cleanup of two superseded Harbor-profile operation-binding aliases."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from urllib.parse import quote

from atomic_ref_cleanup import delete_refs

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-finalize-harbor-profile-alias-prune"
ENABLE = "issue-513-harbor-profile-aliases"
AUDIT_BASE = "ddda010e37c5c4164f7d9a4b8e10ace2574df3de"

TARGETS = [
    {
        "branch": "feat/harbor-profile-binding-current",
        "sha": "bf243d047993b6c5d67e4599be757083b3ac0b22",
        "ahead": 23,
        "files": {
            "backend/alembic/versions/0010_operation_harbor_profile.py",
            "backend/app/api/exports.py",
            "backend/app/api/harbor.py",
            "backend/app/api/harbor_projects.py",
            "backend/app/api/imports.py",
            "backend/app/api/operations.py",
            "backend/app/db/models.py",
            "backend/app/schemas/exports.py",
            "backend/app/schemas/harbor.py",
            "backend/app/schemas/operations.py",
            "backend/app/services/correlated_operation_manager.py",
            "backend/app/services/export_artifact_materializer.py",
            "backend/app/services/export_orchestrator.py",
            "backend/app/services/harbor_destination_validator.py",
            "backend/app/services/harbor_profiles.py",
            "backend/app/services/helm_oci_service.py",
            "backend/app/services/import_destination_plan.py",
            "backend/app/services/import_helm_service.py",
            "backend/app/services/import_orchestrator.py",
            "backend/app/services/import_preview_projection.py",
            "backend/app/services/import_retry.py",
            "backend/app/services/operation_manager.py",
            "backend/app/services/skopeo_service.py",
        },
    },
    {
        "branch": "feat/operation-bound-harbor-profiles",
        "sha": "c1d85e88310aa4d268e886e379e329b0864af2fb",
        "ahead": 49,
        "files": {
            "backend/alembic/versions/0010_harbor_profile_binding.py",
            "backend/app/api/exports.py",
            "backend/app/api/harbor.py",
            "backend/app/api/harbor_projects.py",
            "backend/app/api/imports.py",
            "backend/app/api/operations.py",
            "backend/app/api/settings.py",
            "backend/app/db/models.py",
            "backend/app/schemas/exports.py",
            "backend/app/schemas/harbor.py",
            "backend/app/schemas/operations.py",
            "backend/app/services/export_artifact_materializer.py",
            "backend/app/services/export_orchestrator.py",
            "backend/app/services/harbor_destination_validator.py",
            "backend/app/services/harbor_operation_profile.py",
            "backend/app/services/harbor_profiles.py",
            "backend/app/services/helm_oci_service.py",
            "backend/app/services/import_destination_plan.py",
            "backend/app/services/import_helm_service.py",
            "backend/app/services/import_orchestrator.py",
            "backend/app/services/import_preview_projection.py",
            "backend/app/services/import_retry.py",
            "backend/app/services/operation_manager.py",
            "backend/app/services/skopeo_service.py",
            "backend/tests/test_export_orchestrator.py",
            "backend/tests/test_harbor_api.py",
            "backend/tests/test_harbor_profiles_api.py",
            "backend/tests/test_import_destination_plan.py",
            "backend/tests/test_import_orchestrator.py",
            "backend/tests/test_import_retry.py",
            "backend/tests/test_operations_api.py",
            "frontend/src/api/exports.ts",
            "frontend/src/api/imports.spec.ts",
            "frontend/src/api/imports.ts",
            "frontend/src/components/HarborProfilesPanel.vue",
            "frontend/src/stores/exportWizard.spec.ts",
            "frontend/src/stores/exportWizard.ts",
            "frontend/src/stores/importWizard.spec.ts",
            "frontend/src/stores/importWizard.ts",
            "frontend/src/views/ExportView.spec.ts",
            "frontend/src/views/ExportView.vue",
            "frontend/src/views/ImportView.spec.ts",
            "frontend/src/views/ImportView.vue",
            "tools/ci_scope.py",
            "tools/test_ci_scope.py",
        },
    },
]

REPLACEMENT_PR = {
    "number": 372,
    "head_sha": "024094761f1b02aa0ab728a1b00ac2eab114edd9",
    "merge_sha": "52e904f630521113360e154661bc162b69a78886",
    "required_files": {
        "backend/alembic/versions/0010_operation_harbor_profile.py",
        "backend/app/api/operations.py",
        "backend/app/db/models.py",
        "backend/app/schemas/operations.py",
        "backend/app/services/harbor_profiles.py",
        "backend/app/services/operation_manager.py",
        "backend/app/services/export_orchestrator.py",
        "backend/app/services/skopeo_service.py",
    },
}

ISSUES = (352, 356, 360)

CURRENT_MAIN_FILES = {
    "backend/alembic/versions/0010_operation_harbor_profile.py": "677f64c5bd49de5d75e84fceb4e3aac95ad9840e",
    "backend/app/api/exports.py": "93d50dda4633769ecb1fb2950fbe6f43244a1466",
    "backend/app/api/harbor.py": "5d55a114ca5faebf3ac9756a2f92e980b9b44d44",
    "backend/app/api/harbor_projects.py": "4f14bd7a00e6092538058d4cc493d1e8c5f9b653",
    "backend/app/api/imports.py": "ff46aca85e8ff6058ee9b7057c2d5ca4f5f03641",
    "backend/app/api/operations.py": "31229cf99b268377deb8dd01ebb063b618628535",
    "backend/app/api/settings.py": "48ab917cffab84c4c02217065629ba4339c9354f",
    "backend/app/db/models.py": "bafc93cb969238fd6259426a183026674573e3b5",
    "backend/app/schemas/exports.py": "ed35432f66fd4bcc20dcb9793c6fe8000c6e9fc0",
    "backend/app/schemas/harbor.py": "551fc46b733980ce2185e6b49b5ec7b32b765188",
    "backend/app/schemas/operations.py": "fa119d9af6004627f70e81b290260d37b86b35b4",
    "backend/app/services/export_artifact_materializer.py": "20764faf49e88cc7cb1c197574ca1c35906ce1f6",
    "backend/app/services/export_orchestrator.py": "23ceca9263c296ad5fff2b621dda6933bd3c8e9a",
    "backend/app/services/harbor_destination_validator.py": "f73ac37b5f0bc8af4de0604b9a090788241bac58",
    "backend/app/services/harbor_profiles.py": "22999b85b55065d461ecb2cf0158141651ce1c97",
    "backend/app/services/helm_oci_service.py": "4feb498db75e402b40ddae20fbc56136f5595bec",
    "backend/app/services/import_destination_plan.py": "0728bb47da23d24191ad1d85c99785a998482ac8",
    "backend/app/services/import_helm_service.py": "e6058e775a8926490eed70d7ce81e569c6b43809",
    "backend/app/services/import_orchestrator.py": "d087549a5d0fab628d8d63b5d1d31ef346ba897e",
    "backend/app/services/import_preview_projection.py": "23eefded29aca647d878d0bf06785883cd1ff053",
    "backend/app/services/import_retry.py": "fa10a0dea9109a2daab6f2583fa0fca4fd171322",
    "backend/app/services/operation_manager.py": "da9f2516b4da02a9399381733f69bac567f64ac0",
    "backend/app/services/skopeo_service.py": "5208bef10d727ae9637a3a3ed9c234a97e736b8f",
    "backend/tests/test_export_orchestrator.py": "b2946a8b2745bb16ca231d7904b3d1ada9a6e2bf",
    "backend/tests/test_harbor_api.py": "d3c13303570b69f57537202e974c3d32daea066f",
    "backend/tests/test_harbor_profiles_api.py": "84b6bb79daecc158a73ba413bb218ff46cd8a9a8",
    "backend/tests/test_import_destination_plan.py": "3d40a88da293d726c7b19080225505cfc1bf763e",
    "backend/tests/test_import_orchestrator.py": "2997f732ccfe2c4fa2c7df77848d01e1c67991ac",
    "backend/tests/test_import_retry.py": "9959d462a08d3d0923a74fa13bd8766345e58a9e",
    "backend/tests/test_operations_api.py": "8e469d8edfac207a999ba5621e17eb7a7d4f95b6",
    "frontend/src/api/exports.ts": "d79b795010a7d557b549303b57627891da8e8200",
    "frontend/src/api/imports.ts": "99e8a1cf9f2723e6bd3b6ae934cbebc43c86089d",
    "frontend/src/api/imports.spec.ts": "f95b12d2dc544fa61eb96d547fe12cc3aa733918",
    "frontend/src/components/HarborProfilesPanel.vue": "3e35a94cde197b0e333ddbe204b674e20cf9001a",
    "frontend/src/stores/exportWizard.spec.ts": "9300eed6b76b65aaf9289912156c9666491920a2",
    "frontend/src/stores/exportWizard.ts": "98685b86c255a0b732f01fa587a858b1720f9723",
    "frontend/src/stores/importWizard.spec.ts": "722635a0f8b2785500671b40a6d59ad0c9d069d5",
    "frontend/src/stores/importWizard.ts": "2560539d3f2042ad8cf24a8e72e164e552dd1900",
    "frontend/src/views/ExportView.spec.ts": "568df497fb3439bf56db2b783d807329a6e1cf13",
    "frontend/src/views/ExportView.vue": "8dec7ad86b2cda233c5a7b8fe6dc06c3157f2000",
    "frontend/src/views/ImportView.spec.ts": "cc20b4a52463f93b2e3692cabb18d87d77b4498e",
    "frontend/src/views/ImportView.vue": "8a3b97059e4ae3e817676527875bf7889beb366d",
    "tools/ci_scope.py": "25e0fc76d9c9ea133a76ea98ca4e4043c88133cd",
    "tools/test_ci_scope.py": "68c582a935ea0349edc70824a2c1db85f53d7dc3",
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

def targets_already_absent() -> bool:
    shas = [read_ref_sha(target["branch"]) for target in TARGETS]
    if all(sha is None for sha in shas):
        print("PASS: all target refs are already absent; one-shot cleanup is a no-op")
        return True
    if any(sha is None for sha in shas):
        raise RuntimeError("Only part of the target ref set is absent; refusing partial cleanup")
    for target, sha in zip(TARGETS, shas):
        if sha != target["sha"]:
            raise RuntimeError(f"Target ref moved since audit: {target['branch']}")
    return False

def audit():
    if targets_already_absent():
        return False
    main_ref = github("GET", "git/ref/heads/main")
    main_sha = (main_ref.get("object") or {}).get("sha")
    if not main_sha:
        raise RuntimeError("Cannot read current main SHA")
    if github("GET", f"compare/{AUDIT_BASE}...{main_sha}").get("behind_by") != 0:
        raise RuntimeError("Pinned audit base is not an ancestor of current main")

    for number in ISSUES:
        issue = github("GET", f"issues/{number}")
        if issue.get("state") != "closed" or issue.get("state_reason") != "completed":
            raise RuntimeError(f"Harbor profile issue #{number} is not completed")

    pr = github("GET", f"pulls/{REPLACEMENT_PR['number']}")
    if not (
        pr.get("state") == "closed"
        and pr.get("merged_at")
        and (pr.get("base") or {}).get("ref") == "main"
        and (pr.get("head") or {}).get("sha") == REPLACEMENT_PR["head_sha"]
        and pr.get("merge_commit_sha") == REPLACEMENT_PR["merge_sha"]
    ):
        raise RuntimeError("Replacement PR #372 provenance or merge SHA changed")
    if not REPLACEMENT_PR["required_files"].issubset(changed_files(REPLACEMENT_PR["number"])):
        raise RuntimeError("PR #372 no longer covers the current operation-profile surface")
    if github("GET", f"compare/{REPLACEMENT_PR['merge_sha']}...{AUDIT_BASE}").get("behind_by") != 0:
        raise RuntimeError("PR #372 merge is not in pinned main snapshot")

    open_prs = pages("pulls?state=open")
    all_prs = pages("pulls?state=all")
    for target in TARGETS:
        delta = github("GET", f"compare/main...{target['sha']}")
        if delta.get("ahead_by") != target["ahead"]:
            raise RuntimeError(f"Target unique commit count changed: {target['branch']}")
        if {item["filename"] for item in delta.get("files") or []} != target["files"]:
            raise RuntimeError(f"Target changed-file scope drift: {target['branch']}")

        if any(
            (item.get("head") or {}).get("ref") == target["branch"]
            and ((item.get("head") or {}).get("repo") or {}).get("full_name") == REPO
            for item in all_prs
        ):
            raise RuntimeError(f"Target branch has PR provenance: {target['branch']}")

        for item in open_prs:
            h, b = item.get("head") or {}, item.get("base") or {}
            if h.get("ref") == target["branch"] and ((h.get("repo") or {}).get("full_name") == REPO):
                raise RuntimeError(f"An open PR uses target branch as head: {target['branch']}")
            if b.get("ref") == target["branch"] and ((b.get("repo") or {}).get("full_name") == REPO):
                raise RuntimeError(f"An open PR uses target branch as base: {target['branch']}")

        check_ref(target["branch"], target["sha"])

    for path, expected_sha in CURRENT_MAIN_FILES.items():
        item = github("GET", f"contents/{path}?ref=main")
        if item.get("sha") != expected_sha:
            raise RuntimeError(f"Current operation-profile replacement changed: {path}")

    print(
        "PASS both Harbor profile aliases: no PR provenance; issues #352/#356/#360 completed; "
        "PR #372 merged; 23-commit and 49-commit branches are replaced by the current "
        "immutable operation-profile snapshot, profile-aware API/workers, import/export flows, "
        "CI-scope tooling, frontend and regressions"
    )
    print("PASS: exact live SHAs, exact delta scopes, all 45 current-main file SHAs pinned; dry-run only")
    own_sha = read_ref_sha(OWN)
    if own_sha is None:
        raise RuntimeError("Cleanup branch is missing before the compare-and-swap dry-run")
    check_ref(OWN, own_sha)
    refs = [(target["branch"], target["sha"]) for target in TARGETS] + [(OWN, own_sha)]
    print(delete_refs(REPO, refs, dry_run=True))
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
    # The push event can arrive before the commits/{sha}/pulls association is indexed.
    # Retry that read-only lookup; never weaken the exact merge/head checks.
    for attempt in range(10):
        related = github("GET", f"commits/{merge_sha}/pulls")
        matches = [
            pr for pr in related
            if pr.get("merged_at")
            and pr.get("merge_commit_sha") == merge_sha
            and (pr.get("base") or {}).get("ref") == "main"
            and (pr.get("head") or {}).get("ref") == OWN
            and ((pr.get("head") or {}).get("repo") or {}).get("full_name") in (None, REPO)
        ]
        if len(matches) == 1 and (matches[0].get("head") or {}).get("sha"):
            return matches[0]["head"]["sha"]
        if attempt < 9:
            time.sleep(3)
    raise RuntimeError("Cannot identify exactly one merged cleanup PR after bounded retries")

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
    refs = [(target["branch"], target["sha"]) for target in TARGETS] + [(OWN, own_sha)]
    for name, expected_sha in refs:
        check_ref(name, expected_sha)
    print(delete_refs(REPO, refs, dry_run=False))
    print("DELETED: both exact target refs and cleanup ref via atomic compare-and-swap")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        raise SystemExit(1)
