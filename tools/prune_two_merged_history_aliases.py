#!/usr/bin/env python3
"""One-shot cleanup of two branch refs whose post-merge deltas are fully merged elsewhere."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-two-merged-history-aliases-once"
ENABLE = "issue-513-two-merged-history-aliases"
AUDIT_BASE = "068667e442a2317a00930bd70d9ab44ad7048b64"

TARGETS = (
    {
        "branch": "docs/335-post-v1-decomposition-current-state",
        "sha": "e223425127d94726298ef9f70ea5915ea6e14ffd",
        "original_pr": 337,
        "original_head_sha": "28d1bac816dd0bf2e793bae508a35a0e98738726",
        "original_merge_sha": "181d36e6e531f11fc0b68255af2dd20eb8bff37e",
        "ahead": 3,
        "files": (
            "docs/architecture.md",
            "docs/frontend.md",
            "docs/user-guide.md",
        ),
        "replacements": (
            {
                "pr": 339,
                "head_ref": "docs/incoming-signed-handoff-triplet",
                "head_sha": "9ba14c9dacf6758c9c901e0ad7982d414d00a7e0",
                "merge_sha": "49f7e97a7576d1fdb057b651a7c3ce0a0844b7d8",
            },
        ),
    },
    {
        "branch": "feat/agent2-p6-1-2-structured-logging",
        "sha": "0fe6d5e85971b66e6e558f20010019c3e37e39d0",
        "original_pr": 96,
        "original_head_sha": "a6cad81da41348357388246d655f239bed43a226",
        "original_merge_sha": "b59194e425d82e5278481036a684b8084a929fd3",
        "ahead": 3,
        "files": (
            "README.md",
            "backend/app/api/imports.py",
            "backend/app/schemas/imports.py",
            "backend/app/services/import_preview_projection.py",
            "backend/tests/test_import_preview_projection.py",
            "docs/README.md",
            "docs/architecture.md",
            "docs/frontend.md",
            "docs/import-orchestration.md",
            "frontend/src/api/imports.ts",
            "frontend/src/stores/importWizard.spec.ts",
            "frontend/src/stores/importWizard.ts",
            "frontend/src/views/ImportView.spec.ts",
            "frontend/src/views/ImportView.vue",
        ),
        "replacements": (
            {
                "pr": 89,
                "head_ref": "feat/p5-2-import-wizard",
                "head_sha": "2f20064692b395c91d84872c2683f0f88d86d1ad",
                "merge_sha": "b7fe381d85f7926bd930abecd1f3371a94227899",
            },
            {
                "pr": 95,
                "head_ref": "refactor/91-import-preview-worker",
                "head_sha": "e0b52f4523a34f95ddd6bd213046e313e56cc5c8",
                "merge_sha": "add18075044d994a53345b09e86505ad969b68f4",
            },
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


def check_ref(name: str, expected_sha: str):
    encoded = quote(name, safe="/")
    ref = github("GET", f"git/ref/heads/{encoded}")
    if (
        ref.get("ref") != f"refs/heads/{name}"
        or (ref.get("object") or {}).get("sha") != expected_sha
    ):
        raise RuntimeError(f"Ref missing or SHA changed: {name}")
    branch = github("GET", f"branches/{encoded}")
    if (
        branch.get("protected")
        or (branch.get("commit") or {}).get("sha") != expected_sha
    ):
        raise RuntimeError(f"Branch protected or HEAD changed: {name}")


def check_merged_pr(
    number: int,
    *,
    head_ref: str,
    head_sha: str,
    merge_sha: str,
):
    pr = github("GET", f"pulls/{number}")
    if not (
        pr.get("state") == "closed"
        and pr.get("merged_at")
        and (pr.get("base") or {}).get("ref") == "main"
        and (pr.get("head") or {}).get("ref") == head_ref
        and (pr.get("head") or {}).get("sha") == head_sha
        and (
            ((pr.get("head") or {}).get("repo") or {}).get("full_name")
            in (None, REPO)
        )
        and pr.get("merge_commit_sha") == merge_sha
    ):
        actual = {
            "state": pr.get("state"),
            "merged_at": pr.get("merged_at"),
            "base": (pr.get("base") or {}).get("ref"),
            "head_ref": (pr.get("head") or {}).get("ref"),
            "head_sha": (pr.get("head") or {}).get("sha"),
            "head_repo": ((pr.get("head") or {}).get("repo") or {}).get("full_name"),
            "merge_sha": pr.get("merge_commit_sha"),
        }
        raise RuntimeError(f"PR #{number} provenance/merge mismatch: {actual}")

    ancestry = github("GET", f"compare/{merge_sha}...{AUDIT_BASE}")
    if ancestry.get("behind_by") != 0:
        raise RuntimeError(f"PR #{number} merge is not in the pinned main snapshot")
    return pr


def changed_lines(patch: str):
    if not patch:
        raise RuntimeError("Missing GitHub patch; refusing incomplete comparison")
    return {
        line[1:].strip()
        for line in patch.splitlines()
        if (
            (line.startswith("+") and not line.startswith("+++"))
            or (line.startswith("-") and not line.startswith("---"))
        )
        and line[1:].strip()
    }


def audit():
    main_ref = github("GET", "git/ref/heads/main")
    main_sha = (main_ref.get("object") or {}).get("sha")
    if not main_sha:
        raise RuntimeError("Cannot read current main SHA")
    main_ancestry = github("GET", f"compare/{AUDIT_BASE}...{main_sha}")
    if main_ancestry.get("behind_by") != 0:
        raise RuntimeError("Pinned audit base is not an ancestor of current main")

    open_prs = pages("pulls?state=open")
    replacement_cache = {}

    for target in TARGETS:
        name = target["branch"]
        sha = target["sha"]
        if name in ("main", OWN):
            raise RuntimeError(f"Forbidden target: {name}")

        for pull in open_prs:
            head = pull.get("head") or {}
            base = pull.get("base") or {}
            head_repo = (head.get("repo") or {}).get("full_name")
            base_repo = (base.get("repo") or {}).get("full_name")
            if head.get("ref") == name and head_repo == REPO:
                raise RuntimeError(f"An open PR uses target as head: {name}")
            if base.get("ref") == name and base_repo == REPO:
                raise RuntimeError(f"An open PR is based on target: {name}")

        check_ref(name, sha)
        check_merged_pr(
            target["original_pr"],
            head_ref=name,
            head_sha=target["original_head_sha"],
            merge_sha=target["original_merge_sha"],
        )

        replacement_rows = []
        for replacement in target["replacements"]:
            number = replacement["pr"]
            check_merged_pr(
                number,
                head_ref=replacement["head_ref"],
                head_sha=replacement["head_sha"],
                merge_sha=replacement["merge_sha"],
            )
            if number not in replacement_cache:
                replacement_cache[number] = pages(f"pulls/{number}/files")
            replacement_rows.extend(replacement_cache[number])

        replacement_by_path = {}
        for item in replacement_rows:
            path = item["filename"]
            replacement_by_path.setdefault(path, set()).update(
                changed_lines(item.get("patch"))
            )

        expected_files = set(target["files"])
        if set(replacement_by_path) != expected_files:
            raise RuntimeError(f"Replacement file scope drift for {name}")

        compare = github(
            "GET",
            f"compare/{target['original_head_sha']}...{sha}",
        )
        if (
            compare.get("ahead_by") != target["ahead"]
            or compare.get("behind_by") != 0
        ):
            raise RuntimeError(f"Post-merge commit range drift for {name}")

        target_rows = compare.get("files") or []
        target_by_path = {item["filename"]: item.get("patch") for item in target_rows}
        if set(target_by_path) != expected_files:
            raise RuntimeError(f"Changed-file scope drift for {name}")

        for path in sorted(expected_files):
            target_changes = changed_lines(target_by_path[path])
            if not target_changes.issubset(replacement_by_path[path]):
                raise RuntimeError(
                    f"Merged replacement PRs do not cover the complete delta "
                    f"for {name}:{path}"
                )

        replacement_numbers = ", ".join(
            f"#{item['pr']}" for item in target["replacements"]
        )
        print(
            f"PASS {name}: exact live SHA; original PR "
            f"#{target['original_pr']} and replacement PR(s) "
            f"{replacement_numbers} are merged to main; complete post-merge "
            f"delta is covered"
        )

    print(f"PASS: all {len(TARGETS)} refs preflighted before any deletion")


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
        in (None, REPO)
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
        github("DELETE", f"git/refs/heads/{quote(target['branch'], safe='/')}")
        print(f"DELETED {index}/{len(TARGETS)}: {target['branch']}")

    check_ref(OWN, own_sha)
    github("DELETE", f"git/refs/heads/{quote(OWN, safe='/')}")
    print("DELETED one-time cleanup branch; all other refs untouched")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        raise SystemExit(1)
