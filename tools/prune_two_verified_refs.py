#!/usr/bin/env python3
"""One-time guarded deletion of exactly two verified redundant refs, plus own merged PR branch."""
from __future__ import annotations

import json
import os
import subprocess
import sys

REPO = "askarahodov/Harbor-Transfer-Portal"
OWN = "chore/513-prune-two-verified-refs-once"
TARGETS = {
    "docs/159-release-doc-sync": {
        "sha": "3c9a050cfc8869689d9f3f160be557aa59a33182",
        "pr": 160,
        "merged_head_sha": "a2a44b1fffc816d7165f9710bb3fccb1903a5e9e",
        "mode": "identical_tree",
    },
    "feat/recover-3-mode-theme-from-nexus-rc": {
        "sha": "9008e3b1aa0316b61fb1e30660aaad5c3ba72462",
        "pr": 518,
        "merged_head_sha": "9008e3b1aa0316b61fb1e30660aaad5c3ba72462",
        "mode": "merged_head",
    },
}


def github(method: str, path: str):
    cmd = ["gh", "api", "--method", method, f"repos/{REPO}/{path}"]
    process = subprocess.run(cmd, capture_output=True, text=True, check=True)
    if method == "DELETE":
        return None
    return json.loads(process.stdout)


def pages(path: str):
    rows = []
    for page in range(1, 5):
        results = github("GET", f"{path}?per_page=100&page={page}")
        if not isinstance(results, list):
            raise RuntimeError(f"Unexpected API payload for {path}")
        rows.extend(results)
        if len(results) != 100:
            return rows
    raise RuntimeError(f"Pagination overflow for {path}")


def check_ref(name: str, sha: str):
    # Fetch ref directly immediately before deletion as well as during preflight.
    data = github("GET", f"git/ref/heads/{name}")
    if data.get("ref") != f"refs/heads/{name}" or data.get("object", {}).get("sha") != sha:
        raise RuntimeError(f"Ref drift: {name}")
    branch = github("GET", f"branches/{name}")
    if branch.get("protected") or branch.get("commit", {}).get("sha") != sha:
        raise RuntimeError(f"Branch protected/changed: {name}")


def audit():
    open_heads = {
        p.get("head", {}).get("ref") for p in pages("pulls?state=open")
        if (p.get("head", {}).get("repo") or {}).get("full_name") == REPO
    }
    for name, info in TARGETS.items():
        if name == "main" or name in open_heads or name == OWN:
            raise RuntimeError(f"Unsafe candidate: {name}")
        check_ref(name, info["sha"])
        p = github("GET", f"pulls/{info['pr']}")
        if not (
            p.get("merged_at")
            and p.get("state") == "closed"
            and p.get("head", {}).get("ref") == name
            and (p.get("head", {}).get("repo") or {}).get("full_name") == REPO
            and p.get("head", {}).get("sha") == info["merged_head_sha"]
            and p.get("base", {}).get("ref") == "main"
        ):
            raise RuntimeError(f"PR provenance mismatch: {name}")
        if info["mode"] == "merged_head":
            if info["merged_head_sha"] != info["sha"]:
                raise RuntimeError(f"Merged HEAD drift: {name}")
        else:
            merged = github("GET", f"git/commits/{info['merged_head_sha']}")
            current = github("GET", f"git/commits/{info['sha']}")
            if merged["tree"]["sha"] != current["tree"]["sha"]:
                raise RuntimeError(f"Non-identical file tree: {name}")
        print(f"PASS {name}: exact SHA and original merged PR verified")


def apply_guard():
    required = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": REPO,
        "BRANCH_PRUNE_ENABLE": "issue-513-two-proven-refs",
    }
    for key, value in required.items():
        if os.environ.get(key) != value:
            raise RuntimeError(f"Refusing destructive mode: {key}")
    local = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if not local or local != os.environ.get("GITHUB_SHA"):
        raise RuntimeError("Checkout SHA mismatch")


def own_branch_sha():
    merge_sha = os.environ["GITHUB_SHA"]
    related = github("GET", f"commits/{merge_sha}/pulls")
    correct = [
        p for p in related
        if p.get("merged_at")
        and p.get("merge_commit_sha") == merge_sha
        and p.get("head", {}).get("ref") == OWN
        and p.get("base", {}).get("ref") == "main"
        and (p.get("head", {}).get("repo") or {}).get("full_name") == REPO
    ]
    if len(correct) != 1:
        raise RuntimeError("Cannot unambiguously identify merged cleanup PR")
    sha = correct[0]["head"]["sha"]
    if any(p.get("head", {}).get("ref") == OWN for p in pages("pulls?state=open")):
        raise RuntimeError("Own branch still in open PR")
    check_ref(OWN, sha)
    return sha


def main():
    write = sys.argv[1:] == ["--apply"]
    if sys.argv[1:] not in ([], ["--apply"]):
        raise RuntimeError("Only --apply is supported")
    if write:
        apply_guard()
    audit()  # FULL PRECHECK BEFORE ANY DELETION
    if not write:
        print("DRY RUN: no refs deleted")
        return
    sha = own_branch_sha()
    # Re-check all targets before any delete in case anything changed during audit.
    for name, info in TARGETS.items():
        check_ref(name, info["sha"])
    check_ref(OWN, sha)
    for name, info in TARGETS.items():
        check_ref(name, info["sha"])
        github("DELETE", f"git/refs/heads/{name}")
        print(f"DELETED {name}")
    check_ref(OWN, sha)
    github("DELETE", f"git/refs/heads/{OWN}")
    print("DELETED own cleanup branch. Total: 3 exact refs, no branch contents overwritten.")


if __name__ == "__main__":
    try:
        main()
    except (Exception, subprocess.CalledProcessError) as exc:
        print(f"FAIL CLOSED: {exc}", file=sys.stderr)
        sys.exit(1)
