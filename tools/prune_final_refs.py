#!/usr/bin/env python3
"""Finish audited branch cleanup with 9 pinned refs and this merged PR head.

Dry-run on the PR; destructive mode only for main push GitHub Actions.
Never deletes main, Nexus RC, or branches without approved provenance.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

from prune_branch_refs import REPO, gh_json, pages, recheck

MANIFEST = Path(__file__).with_name("final-ref-prune.tsv")
SELF = "chore/513-final-ref-prune"
EXPECTED = 9
SHA = re.compile(r"^[0-9a-f]{40}$")
NAME = re.compile(r"^[A-Za-z0-9._/-]+$")


def load():
    result = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 5:
            raise RuntimeError(f"Bad row {line!r}")
        name, sha, mode, pr, replacement = fields
        if (
            not NAME.fullmatch(name)
            or name == "main"
            or name == SELF
            or name.startswith("/")
            or name.endswith("/")
            or ".." in name
            or not SHA.fullmatch(sha)
            or not pr.isdigit()
            or mode not in {"merged", "superseded"}
            or (mode == "merged" and replacement != "-")
            or (mode == "superseded" and not replacement.isdigit())
            or name in result
        ):
            raise RuntimeError(f"Unsafe ref candidate {name}")
        result[name] = (sha, mode, int(pr), replacement)
    if len(result) != EXPECTED:
        raise RuntimeError(f"Expected {EXPECTED} candidates, got {len(result)}")
    return result


def audit(targets):
    live = {b["name"]: b for b in pages("branches")}
    opened = {
        (p.get("head") or {}).get("ref")
        for p in pages("pulls?state=open")
        if ((p.get("head") or {}).get("repo") or {}).get("full_name") == REPO
    }
    bad = []
    for name, (sha, mode, number, replacement) in sorted(targets.items()):
        b = live.get(name)
        if not b or b.get("protected") or (b.get("commit") or {}).get("sha") != sha:
            bad.append(f"{name}: ref missing/protected/changed")
        if name in opened:
            bad.append(f"{name}: head of open PR")
        pr = gh_json("GET", f"repos/{REPO}/pulls/{number}")
        head = pr.get("head") or {}
        if not (
            head.get("ref") == name
            and head.get("sha") == sha
            and (head.get("repo") or {}).get("full_name") == REPO
            and pr.get("state") == "closed"
        ):
            bad.append(f"{name}: original PR #{number} mismatches")
        if mode == "merged":
            if not pr.get("merged_at") or (pr.get("base") or {}).get("ref") != "main":
                bad.append(f"{name}: PR #{number} not merged into main")
        else:
            if pr.get("merged_at"):
                bad.append(f"{name}: PR #{number} unexpectedly merged")
            repl = gh_json("GET", f"repos/{REPO}/pulls/{replacement}")
            if not repl.get("merged_at") or (repl.get("base") or {}).get("ref") != "main":
                bad.append(f"{name}: replacement PR #{replacement} not merged into main")
    if bad:
        raise RuntimeError("FAILED before any deletion:\n- " + "\n- ".join(bad))
    print(f"PASS: {len(targets)} cleanup/superseded branches validated.")
    return targets


def guard():
    required = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": REPO,
        "BRANCH_PRUNE_ENABLE": "issue-513-final-10-self",
    }
    for key, value in required.items():
        if os.getenv(key) != value:
            raise RuntimeError(f"Refusing --apply without {key}")
    rev = subprocess.run(
        ["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True
    ).stdout.strip()
    if rev != os.getenv("GITHUB_SHA"):
        raise RuntimeError("Refusing --apply with checkout mismatch")


def audit_own_merged_pr():
    # GitHub's merge commit must belong to the PR for this exact cleanup branch.
    sha = os.environ["GITHUB_SHA"]
    related = gh_json("GET", f"repos/{REPO}/commits/{sha}/pulls")
    matches = [
        p for p in related
        if p.get("merged_at")
        and p.get("merge_commit_sha") == sha
        and (p.get("base") or {}).get("ref") == "main"
        and (p.get("head") or {}).get("ref") == SELF
        and ((p.get("head") or {}).get("repo") or {}).get("full_name") == REPO
    ]
    if len(matches) != 1:
        raise RuntimeError("Could not validate exactly one merged own PR for workflow commit")
    expected_head = (matches[0].get("head") or {}).get("sha")
    if not SHA.fullmatch(expected_head or ""):
        raise RuntimeError("Own PR head SHA unavailable")
    opened = pages("pulls?state=open")
    if any(
        (p.get("head") or {}).get("ref") == SELF
        and ((p.get("head") or {}).get("repo") or {}).get("full_name") == REPO
        for p in opened
    ):
        raise RuntimeError("Own branch still referenced by open PR")
    b = gh_json("GET", f"repos/{REPO}/branches/{SELF}")
    if b.get("protected") or (b.get("commit") or {}).get("sha") != expected_head:
        raise RuntimeError("Own branch changed or protected")
    print(f"PASS: own merged PR head {SELF} = {expected_head}")
    return expected_head


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        guard()
    targets = audit(load())
    own_sha = audit_own_merged_pr() if args.apply else None
    if not args.apply:
        print("DRY RUN: nothing deleted; own PR will be validated after merge.")
        return 0

    removed = 0
    for name, (sha, *_rest) in sorted(targets.items()):
        recheck(name, sha)
        gh_json("DELETE", f"repos/{REPO}/git/refs/heads/{name}")
        removed += 1
        print(f"Deleted {removed}/{EXPECTED + 1}: {name}")
    assert own_sha is not None
    recheck(SELF, own_sha)
    gh_json("DELETE", f"repos/{REPO}/git/refs/heads/{SELF}")
    removed += 1
    print(f"DELETED {removed} verified refs including own cleanup branch.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
