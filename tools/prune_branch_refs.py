#!/usr/bin/env python3
"""One-time, fail-closed cleanup of 188 branch refs audited in issue #513.

Dry-run by default. GitHub Actions on a push to main is the only allowed
execution mode for --apply. Neither commits nor working trees are deleted.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

REPO = "askarahodov/Harbor-Transfer-Portal"
EXPECTED = 188
MANIFEST = Path(__file__).with_name("branch-prune-candidates.tsv")
SHA = re.compile(r"^[a-f0-9]{40}$")
BRANCH = re.compile(r"^[A-Za-z0-9._/-]+$")


def gh_json(method: str, path: str):
    args = ["gh", "api", "--method", method, path]
    proc = subprocess.run(args, text=True, capture_output=True, check=False)
    if proc.returncode:
        raise RuntimeError(f"GitHub {method} {path} failed: {proc.stderr.strip()}")
    if method == "DELETE":
        return None
    import json

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GitHub returned invalid JSON for {path}") from exc


def pages(endpoint: str):
    rows = []
    for page in range(1, 30):
        sep = "&" if "?" in endpoint else "?"
        part = gh_json("GET", f"repos/{REPO}/{endpoint}{sep}per_page=100&page={page}")
        if not isinstance(part, list):
            raise RuntimeError(f"Expected array from {endpoint}")
        rows.extend(part)
        if len(part) < 100:
            return rows
    raise RuntimeError(f"Pagination limit for {endpoint}; refusing to proceed")


def load():
    candidates = {}
    count_direct = count_contained = 0
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            raise RuntimeError(f"Bad manifest row: {line!r}")
        name, sha, reason = fields
        if (
            name == "main"
            or name.startswith("/")
            or name.endswith("/")
            or ".." in name
            or "//" in name
            or not BRANCH.fullmatch(name)
            or not SHA.fullmatch(sha)
            or name in candidates
        ):
            raise RuntimeError(f"Unsafe/duplicate manifest entry: {name}")
        if reason.startswith("merged:") and reason[7:].isdigit():
            count_direct += 1
        elif reason == "contained":
            count_contained += 1
        else:
            raise RuntimeError(f"Unknown provenance {name}: {reason}")
        candidates[name] = (sha, reason)
    if (len(candidates), count_direct, count_contained) != (EXPECTED, 180, 8):
        raise RuntimeError(
            f"Manifest changed: {len(candidates)} total, {count_direct} merged, "
            f"{count_contained} contained. No deletions allowed."
        )
    return candidates


def audit(candidates):
    # Open PR head branches take precedence over all historical merged PRs.
    opened = set()
    for pr in pages("pulls?state=open"):
        head = pr.get("head") or {}
        owner = (head.get("repo") or {}).get("full_name")
        if owner == REPO:
            opened.add(head.get("ref"))

    current = {row["name"]: row for row in pages("branches")}
    failed = []
    for name, (sha, reason) in sorted(candidates.items()):
        live = current.get(name)
        if live is None:
            failed.append(f"{name}: branch no longer exists")
            continue
        if live.get("protected"):
            failed.append(f"{name}: protected branch")
        if (live.get("commit") or {}).get("sha") != sha:
            failed.append(f"{name}: HEAD changed since audit")
        if name in opened:
            failed.append(f"{name}: OPEN PR uses branch")
        if reason.startswith("merged:"):
            number = reason.split(":", 1)[1]
            pr = gh_json("GET", f"repos/{REPO}/pulls/{number}")
            head = pr.get("head") or {}
            if not (
                pr.get("merged_at")
                and pr.get("state") == "closed"
                and (pr.get("base") or {}).get("ref") == "main"
                and head.get("ref") == name
                and head.get("sha") == sha
                and (head.get("repo") or {}).get("full_name") == REPO
            ):
                failed.append(f"{name}: merged PR #{number} does not validate against main")
        else:
            comparison = gh_json(
                "GET", f"repos/{REPO}/compare/main...{quote(name, safe='/')}"
            )
            if comparison.get("ahead_by") != 0:
                failed.append(f"{name}: branch still has unique commits")
    if failed:
        raise RuntimeError(
            f"PRE-FLIGHT FAILED: {len(failed)} issues; no deletion performed.\n- "
            + "\n- ".join(failed)
        )
    print(f"PASS: all {len(candidates)} branches validated (180 main-merged + 8 contained)")
    return candidates


def guard_ci_apply():
    required = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": REPO,
        "BRANCH_PRUNE_ENABLE": "issue-513-reviewed-188",
    }
    for key, expected in required.items():
        if os.getenv(key) != expected:
            raise RuntimeError(f"Refusing --apply: {key} does not match approved CI context")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True
    ).stdout.strip()
    if revision != os.getenv("GITHUB_SHA"):
        raise RuntimeError("Refusing --apply: checked-out commit differs from GITHUB_SHA")


def recheck(name: str, sha: str):
    # Re-read the exact ref immediately before the destructive API call.
    ref = gh_json("GET", f"repos/{REPO}/git/ref/heads/{quote(name, safe='/')}")
    if (
        ref.get("ref") != f"refs/heads/{name}"
        or (ref.get("object") or {}).get("sha") != sha
    ):
        raise RuntimeError(f"STOP: {name} changed after preflight")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="CI-only deletion")
    opts = parser.parse_args()
    if opts.apply:
        guard_ci_apply()
    candidates = audit(load())
    if not opts.apply:
        print("DRY RUN: nothing deleted.")
        return 0

    removed = 0
    for name, (sha, _) in sorted(candidates.items()):
        recheck(name, sha)
        gh_json("DELETE", f"repos/{REPO}/git/refs/heads/{quote(name, safe='/')}")
        removed += 1
        print(f"Deleted {removed}/{len(candidates)}: {name}")
    print(f"DELETED {removed} branch refs. Main, four open PR heads and Nexus hold remain.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
