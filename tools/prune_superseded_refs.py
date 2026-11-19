#!/usr/bin/env python3
"""Audit/cleanup ten unchanged branches explicitly superseded by merged PRs.

Does not merge code. The original closed PR and commit SHA stay in GitHub.
This script is dry-run by default and --apply is guarded to a main push CI.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

from prune_branch_refs import gh_json, pages, recheck, REPO

MANIFEST = Path(__file__).with_name("superseded-branch-prune.tsv")
COUNT = 10
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def load():
    result = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 4:
            raise RuntimeError(f"Invalid row: {line!r}")
        name, sha, closed, replacement = fields
        if (
            not BRANCH_RE.fullmatch(name)
            or name == "main"
            or ".." in name
            or name.startswith("/")
            or name.endswith("/")
            or not SHA_RE.fullmatch(sha)
            or not closed.isdigit()
            or not replacement.isdigit()
            or name in result
        ):
            raise RuntimeError(f"Invalid/duplicate candidate: {name}")
        result[name] = (sha, int(closed), int(replacement))
    if len(result) != COUNT:
        raise RuntimeError(f"Expected {COUNT} superseded refs, found {len(result)}")
    return result


def audit(candidates):
    active = {
        p["head"]["ref"]
        for p in pages("pulls?state=open")
        if (p.get("head") or {}).get("repo", {}).get("full_name") == REPO
    }
    live = {r["name"]: r for r in pages("branches")}
    problems = []
    for name, (sha, closed, replacement) in sorted(candidates.items()):
        record = live.get(name)
        if not record or (record.get("commit") or {}).get("sha") != sha:
            problems.append(f"{name}: missing or SHA changed")
        elif record.get("protected"):
            problems.append(f"{name}: protected")
        if name in active:
            problems.append(f"{name}: used by an open PR")
        original = gh_json("GET", f"repos/{REPO}/pulls/{closed}")
        head = original.get("head") or {}
        if not (
            original.get("state") == "closed"
            and not original.get("merged_at")
            and head.get("ref") == name
            and head.get("sha") == sha
            and (head.get("repo") or {}).get("full_name") == REPO
        ):
            problems.append(f"{name}: original PR #{closed} no longer matches")
        successor = gh_json("GET", f"repos/{REPO}/pulls/{replacement}")
        if not (
            successor.get("merged_at")
            and successor.get("state") == "closed"
            and (successor.get("base") or {}).get("ref") == "main"
        ):
            problems.append(f"{name}: replacement PR #{replacement} not merged into main")
    if problems:
        raise RuntimeError(
            f"Preflight blocked all deletion ({len(problems)} issues):\n- "
            + "\n- ".join(problems)
        )
    print(f"PASS: {COUNT} unchanged abandoned branches superseded by merged PRs.")
    return candidates


def guard():
    expected = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": REPO,
        "BRANCH_PRUNE_ENABLE": "issue-513-superseded-10",
    }
    for key, val in expected.items():
        if os.getenv(key) != val:
            raise RuntimeError(f"Refusing deletion: invalid {key}")
    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if actual != os.getenv("GITHUB_SHA"):
        raise RuntimeError("Refusing deletion: checkout SHA != workflow SHA")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        guard()
    targets = audit(load())
    if not args.apply:
        print("DRY RUN: no refs deleted.")
        return 0
    count = 0
    for branch, (sha, _closed, _replacement) in sorted(targets.items()):
        recheck(branch, sha)
        gh_json("DELETE", f"repos/{REPO}/git/refs/heads/{branch}")
        count += 1
        print(f"Deleted superseded ref {count}/{COUNT}: {branch}")
    print(f"DELETED {count} superseded refs; merged replacements remain in main.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
