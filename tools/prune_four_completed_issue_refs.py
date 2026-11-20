#!/usr/bin/env python3
"""One-shot cleanup of four no-PR refs whose issues are complete on main.

Dry-run by default. Destructive mode is restricted to the push to main caused
by merging this exact cleanup PR. All target SHAs, issue states, replacement
merges and changed-file coverage are pinned and revalidated.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-four-completed-issue-refs-once"
ENABLE = "issue-513-four-completed-issue-refs"
AUDIT_BASE = "73389528c984d32b9d4c5e5cf8f6983203f2f60a"

TARGETS = (
    {
        "branch": "feat/184-ui-mode-switcher",
        "sha": "6d085f0e352291289985a2f04bb895335209fb50",
        "issues": (184,),
        "replacement": 204,
        "merge_sha": "1127ca21b0f4914188747fb014bc3558713beb69",
        "files": (
            "frontend/src/components/ModeSwitcher.spec.ts",
            "frontend/src/components/ModeSwitcher.vue",
            "frontend/src/stores/runtime.ts",
        ),
    },
    {
        "branch": "feat/237-semantic-dark-theme",
        "sha": "7277084de962f57bb4dd66bdcbd05f2e4b077633",
        "issues": (237,),
        "replacement": 240,
        "merge_sha": "d64d194ba2f3f1592c36d48d5b5f0d65b1ea0a07",
        "files": (
            "docs/decisions.md",
            "frontend/src/components/ContourBadge.vue",
            "frontend/src/components/HarborProjectCreationPanel.vue",
            "frontend/src/components/ImportDestinationMapping.vue",
            "frontend/src/components/ImportReceiptDestinations.vue",
            "frontend/src/components/KeyManagementPanel.vue",
            "frontend/src/components/ModeSwitcher.vue",
            "frontend/src/components/PageShell.vue",
            "frontend/src/components/StatePlaceholder.vue",
            "frontend/src/styles/base.css",
            "frontend/src/styles/tokens.css",
            "frontend/src/views/ExportView.vue",
            "frontend/src/views/ImportView.vue",
            "frontend/src/views/LoginView.vue",
            "frontend/src/views/SettingsView.vue",
            "frontend/src/views/UsersView.vue",
            "tools/check_design_tokens.py",
            "tools/test_design_tokens.py",
            "tools/test_frontend_system_ui.py",
        ),
    },
    {
        "branch": "fix/220-221-semantic-status-tokens",
        "sha": "6c31b78f437ef7bd644afe74c099f8b4900858a7",
        "issues": (220, 221),
        "replacement": 229,
        "merge_sha": "6aa0c33156936ece66b1221dc238e3080dc596a2",
        "files": (
            "frontend/src/components/StatusBadge.vue",
            "frontend/src/styles/tokens.css",
            "frontend/src/views/LoginView.vue",
        ),
    },
    {
        "branch": "test/p7-3-1-scoped-ci-regression",
        "sha": "946ad9edbe343a5168aa62cc20c9043006ca6c73",
        "issues": (122,),
        "replacement": 123,
        "merge_sha": "f5cabad96c6cffed3804b6ffee3b9cee9f2b377e",
        "files": (
            ".github/workflows/ci.yml",
            "Makefile",
            "docs/testing.md",
            "tools/ci_scope.py",
            "tools/test_ci_scope.py",
        ),
    },
)


def github(method: str, path: str):
    result = subprocess.run(
        ["gh", "api", "--method", method, f"repos/{REPO}/{path}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"GitHub {method} {path} failed: {result.stderr.strip()}"
        )
    if method == "DELETE":
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid GitHub JSON for {path}") from exc


def pages(endpoint: str):
    rows = []
    for page in range(1, 30):
        separator = "&" if "?" in endpoint else "?"
        part = github("GET", f"{endpoint}{separator}per_page=100&page={page}")
        if not isinstance(part, list):
            raise RuntimeError(f"Expected array from {endpoint}")
        rows.extend(part)
        if len(part) < 100:
            return rows
    raise RuntimeError(f"Pagination limit reached for {endpoint}; refusing")


def ref_path(name: str) -> str:
    return quote(name, safe="/")


def check_ref(name: str, expected_sha: str):
    ref = github("GET", f"git/ref/heads/{ref_path(name)}")
    if (
        ref.get("ref") != f"refs/heads/{name}"
        or (ref.get("object") or {}).get("sha") != expected_sha
    ):
        raise RuntimeError(f"Ref missing or SHA changed: {name}")
    branch = github("GET", f"branches/{ref_path(name)}")
    if (
        branch.get("protected")
        or (branch.get("commit") or {}).get("sha") != expected_sha
    ):
        raise RuntimeError(f"Branch protected or HEAD changed: {name}")


def audit():
    all_prs = pages("pulls?state=all")
    open_prs = pages("pulls?state=open")
    for target in TARGETS:
        name = target["branch"]
        sha = target["sha"]
        if name in ("main", OWN):
            raise RuntimeError(f"Forbidden target: {name}")

        if any(
            (pull.get("head") or {}).get("ref") == name
            and ((pull.get("head") or {}).get("repo") or {}).get("full_name")
            == REPO
            for pull in all_prs
        ):
            raise RuntimeError(f"Target unexpectedly has PR provenance: {name}")
        if any(
            (pull.get("base") or {}).get("ref") == name
            for pull in open_prs
        ):
            raise RuntimeError(f"An open PR is based on target: {name}")

        check_ref(name, sha)

        compare = github(
            "GET", f"compare/{AUDIT_BASE}...{sha}"
        )
        actual_files = {
            item["filename"] for item in (compare.get("files") or [])
        }
        expected_files = set(target["files"])
        if actual_files != expected_files:
            raise RuntimeError(
                f"Changed-file scope drift for {name}: "
                f"actual={sorted(actual_files)}, "
                f"expected={sorted(expected_files)}"
            )

        for issue_number in target["issues"]:
            issue = github("GET", f"issues/{issue_number}")
            if not (
                issue.get("state") == "closed"
                and issue.get("state_reason") == "completed"
            ):
                raise RuntimeError(
                    f"Issue #{issue_number} is not completed"
                )

        replacement = github("GET", f"pulls/{target['replacement']}")
        if not (
            replacement.get("state") == "closed"
            and replacement.get("merged_at")
            and (replacement.get("base") or {}).get("ref") == "main"
            and replacement.get("merge_commit_sha") == target["merge_sha"]
        ):
            raise RuntimeError(
                f"Replacement PR #{target['replacement']} is not the pinned main merge"
            )

        replacement_text = (
            (replacement.get("title") or "")
            + "\n"
            + (replacement.get("body") or "")
        )
        if not all(f"#{n}" in replacement_text for n in target["issues"]):
            raise RuntimeError(
                f"Replacement PR #{target['replacement']} does not reference "
                "all completed issues"
            )

        replacement_files = {
            item["filename"]
            for item in pages(f"pulls/{target['replacement']}/files")
        }
        if not expected_files.issubset(replacement_files):
            raise RuntimeError(
                f"Replacement PR #{target['replacement']} does not cover "
                f"the full file scope of {name}"
            )

        ancestry = github(
            "GET", f"compare/{target['merge_sha']}...{AUDIT_BASE}"
        )
        if ancestry.get("behind_by") != 0:
            raise RuntimeError(
                f"Replacement PR #{target['replacement']} merge is not "
                "contained in the pinned main snapshot"
            )

        print(
            f"PASS {name}: exact SHA/file scope; completed issue(s) "
            f"{target['issues']}; merged replacement #{target['replacement']} "
            "covers every changed file"
        )

    print(f"PASS: all {len(TARGETS)} targets validated before any deletion")


def apply_guard():
    required = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": REPO,
        "BRANCH_PRUNE_ENABLE": ENABLE,
    }
    for key, expected in required.items():
        if os.environ.get(key) != expected:
            raise RuntimeError(f"Refusing destructive mode: {key} mismatch")

    checkout = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if checkout != os.environ.get("GITHUB_SHA"):
        raise RuntimeError("Checkout SHA does not match the push event")


def audit_own_merged_pr() -> str:
    merge_sha = os.environ["GITHUB_SHA"]
    related = github("GET", f"commits/{merge_sha}/pulls")
    matches = [
        pull
        for pull in related
        if pull.get("merged_at")
        and pull.get("merge_commit_sha") == merge_sha
        and (pull.get("base") or {}).get("ref") == "main"
        and (pull.get("head") or {}).get("ref") == OWN
        and ((pull.get("head") or {}).get("repo") or {}).get("full_name")
        == REPO
    ]
    if len(matches) != 1:
        raise RuntimeError("Cannot identify exactly one merged cleanup PR")
    own_sha = (matches[0].get("head") or {}).get("sha")
    if not own_sha:
        raise RuntimeError("Cleanup PR head SHA unavailable")
    check_ref(OWN, own_sha)
    return own_sha


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

    own_sha = audit_own_merged_pr()
    for target in TARGETS:
        check_ref(target["branch"], target["sha"])
    check_ref(OWN, own_sha)

    for index, target in enumerate(TARGETS, start=1):
        check_ref(target["branch"], target["sha"])
        github("DELETE", f"git/refs/heads/{ref_path(target['branch'])}")
        print(f"DELETED {index}/{len(TARGETS)}: {target['branch']}")

    check_ref(OWN, own_sha)
    github("DELETE", f"git/refs/heads/{ref_path(OWN)}")
    print("DELETED one-time cleanup branch; all other refs untouched")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        raise SystemExit(1)
