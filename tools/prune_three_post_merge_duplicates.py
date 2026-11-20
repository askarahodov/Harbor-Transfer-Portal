#!/usr/bin/env python3
"""One-shot cleanup of three post-merge refs whose complete deltas are superseded.

Two targets have exact post-merge patches duplicated by later main-merged PRs.
The dependency-refresh target's post-merge additions/deletions are a subset of
the later CI qualification PR. Dry-run by default; deletion is main-push guarded.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-three-post-merge-duplicates-once"
ENABLE = "issue-513-three-post-merge-duplicates"
AUDIT_BASE = "fbe768ac29ee01fac941091795c16dc3a68bad25"

TARGETS = (
    {
        "branch": "feat/p6-1-3-history-ui",
        "sha": "85e79e11a637b1bfe30d5a91bbe5839895fe15a7",
        "original_pr": 97,
        "original_head_sha": "6b272501ccbfdf0308ef75a40277eaf0f3d916a5",
        "original_merge_sha": "fb0048690e5993e5a12819e4432bdb6b8a4cf2e5",
        "replacement_pr": 98,
        "replacement_head_ref": "fix/history-semantic-tokens",
        "replacement_head_sha": "e89f3ff2c5663dbe0fe173173359cc84180aacca",
        "replacement_merge_sha": "2912d2c569415a59b797e9f38004f116ec114481",
        "ahead": 1,
        "files": ("frontend/src/styles/tokens.css",),
        "patch_mode": "exact",
    },
    {
        "branch": "docs/p7-4-final",
        "sha": "5215620a4a3275d3dcc3674817ce96b0c66e3915",
        "original_pr": 127,
        "original_head_sha": "25486905cac56e61331817e9565ee5e69e447484",
        "original_merge_sha": "cadd260f8d01637b72f501e9ddbacc0d07171d84",
        "replacement_pr": 128,
        "replacement_head_ref": "docs/p7-4-discovery-size-followup",
        "replacement_head_sha": "15c01da84764658111c69656d246b359ca5dc9eb",
        "replacement_merge_sha": "43e1a229863c49b5963e8204b3b17546928af20d",
        "ahead": 1,
        "files": ("docs/troubleshooting.md",),
        "patch_mode": "exact",
    },
    {
        "branch": "chore/338-backend-dependency-refresh",
        "sha": "73b202b7631b3e4bbd8c3b6cadc9f4443b10e1b9",
        "original_pr": 341,
        "original_head_sha": "f99307addb76b52c0dd2a99a5a14003d58167595",
        "original_merge_sha": "3394d2682eefeb833e3885391991e121e75bee5c",
        "replacement_pr": 343,
        "replacement_head_ref": "ci/runtime-dependency-security-scope",
        "replacement_head_sha": "cbce719712611e3044951f3f219c0fca3617cad1",
        "replacement_merge_sha": "31d0e3a7aa2ce7fde362f2190d2778ec97f31cf3",
        "ahead": 3,
        "files": (
            "docs/testing.md",
            "tools/ci_scope.py",
            "tools/test_ci_scope.py",
        ),
        "patch_mode": "subset",
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
    current_main = github("GET", "git/ref/heads/main")
    main_sha = (current_main.get("object") or {}).get("sha")
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
        check_merged_pr(
            target["replacement_pr"],
            head_ref=target["replacement_head_ref"],
            head_sha=target["replacement_head_sha"],
            merge_sha=target["replacement_merge_sha"],
        )

        replacement_number = target["replacement_pr"]
        if replacement_number not in replacement_cache:
            replacement_cache[replacement_number] = pages(
                f"pulls/{replacement_number}/files"
            )
        replacement_rows = replacement_cache[replacement_number]
        replacement_by_path = {
            item["filename"]: item.get("patch") for item in replacement_rows
        }
        expected_files = set(target["files"])
        if set(replacement_by_path) != expected_files:
            raise RuntimeError(
                f"Replacement file scope drift for PR #{replacement_number}"
            )

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
            replacement_changes = changed_lines(replacement_by_path[path])
            if target["patch_mode"] == "exact":
                covered = target_changes == replacement_changes
            else:
                covered = target_changes.issubset(replacement_changes)
            if not covered:
                raise RuntimeError(
                    f"Replacement patch does not cover the exact delta for "
                    f"{name}:{path}"
                )

        print(
            f"PASS {name}: exact live SHA; original PR "
            f"#{target['original_pr']} and replacement PR "
            f"#{replacement_number} are merged to main; complete post-merge "
            f"delta is covered by replacement patch"
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
        encoded = quote(target["branch"], safe="/")
        github("DELETE", f"git/refs/heads/{encoded}")
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
