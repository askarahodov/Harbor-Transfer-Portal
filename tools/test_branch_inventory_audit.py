from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.branch_inventory_audit import (
    AuditError,
    AuditRow,
    BranchFacts,
    PullRequest,
    build_row,
    classify,
    collect_branch_facts,
    format_pull_requests,
    list_remote_branches,
    load_pull_requests,
    main,
    render_json,
    render_tsv,
    summarize,
)

_SOURCE = Path(__file__).with_name("branch_inventory_audit.py").read_text(encoding="utf-8")


def _facts(
    *,
    name: str = "feat/example",
    head_sha: str = "a" * 40,
    ancestor: bool = False,
    content: bool | None = None,
    merge_base: str | None = "b" * 40,
    committed_at: str = "2026-11-20T10:00:00+00:00",
) -> BranchFacts:
    return BranchFacts(
        name=name,
        head_sha=head_sha,
        committed_at=committed_at,
        merge_base=merge_base,
        ancestor_of_main=ancestor,
        content_in_main=content,
        files_changed=0,
    )


def _pr(
    number: int,
    *,
    state: str = "closed",
    draft: bool = False,
    base: str = "main",
    merged: bool = False,
    head_ref: str = "feat/example",
    head_sha: str = "a" * 40,
) -> PullRequest:
    return PullRequest(
        number=number,
        state=state,
        draft=draft,
        base=base,
        merged=merged,
        head_ref=head_ref,
        head_sha=head_sha,
    )


class ClassificationTests(unittest.TestCase):
    def test_ancestor_of_base_is_merged(self) -> None:
        self.assertEqual(
            classify(_facts(ancestor=True), [], github_available=True),
            "merged",
        )

    def test_merged_pr_into_base_marks_merged(self) -> None:
        facts = _facts(content=False)
        prs = [_pr(10, merged=True)]
        self.assertEqual(classify(facts, prs, github_available=True), "merged")

    def test_merged_pr_into_other_base_is_not_merged(self) -> None:
        facts = _facts(content=False)
        prs = [_pr(10, merged=True, base="integration/nexus-oss-release-candidate-451")]
        self.assertEqual(
            classify(facts, prs, github_available=True),
            "closed_unmerged",
        )

    def test_content_present_at_other_sha_is_content_in_main(self) -> None:
        facts = _facts(ancestor=False, content=True)
        prs = [_pr(11, merged=False)]
        self.assertEqual(
            classify(facts, prs, github_available=True),
            "content_in_main",
        )

    def test_open_pr_takes_precedence_over_git_evidence(self) -> None:
        facts = _facts(ancestor=True, content=True)
        prs = [_pr(12, state="open", draft=False)]
        self.assertEqual(classify(facts, prs, github_available=True), "open_pr")

    def test_open_draft_pr_is_draft_pr(self) -> None:
        facts = _facts(ancestor=False, content=None)
        prs = [_pr(13, state="open", draft=True)]
        self.assertEqual(classify(facts, prs, github_available=True), "draft_pr")

    def test_closed_unmerged_pr(self) -> None:
        facts = _facts(ancestor=False, content=False)
        prs = [_pr(14, state="closed", merged=False)]
        self.assertEqual(
            classify(facts, prs, github_available=True),
            "closed_unmerged",
        )

    def test_no_pr_with_github_metadata(self) -> None:
        facts = _facts(ancestor=False, content=False)
        self.assertEqual(classify(facts, [], github_available=True), "no_pr")

    def test_post_merge_drift_is_flagged_for_owner_review(self) -> None:
        facts = _facts(content=False)
        prs = [_pr(16, merged=True, head_sha="c" * 40)]
        self.assertEqual(classify(facts, prs, github_available=True), "unknown")

    def test_no_github_metadata_degrades_to_unknown(self) -> None:
        facts = _facts(ancestor=False, content=None)
        self.assertEqual(classify(facts, [], github_available=False), "unknown")

    def test_unknown_content_with_closed_pr_stays_closed_unmerged(self) -> None:
        facts = _facts(ancestor=False, content=None)
        prs = [_pr(15, state="closed", merged=False)]
        self.assertEqual(
            classify(facts, prs, github_available=False),
            "closed_unmerged",
        )


class RowTests(unittest.TestCase):
    def test_risk_levels_match_category(self) -> None:
        cases = {
            "merged": "low",
            "content_in_main": "low",
            "open_pr": "high",
            "draft_pr": "high",
            "closed_unmerged": "medium",
            "no_pr": "medium",
            "unknown": "high",
        }
        for category, risk in cases.items():
            with self.subTest(category=category):
                facts = _facts(
                    ancestor=category == "merged",
                    content=True if category == "content_in_main" else None,
                )
                prs: list[PullRequest] = []
                github_available = True
                if category == "open_pr":
                    prs = [_pr(1, state="open")]
                elif category == "draft_pr":
                    prs = [_pr(2, state="open", draft=True)]
                elif category == "closed_unmerged":
                    prs = [_pr(3, state="closed")]
                elif category == "unknown":
                    github_available = False
                row = build_row(facts, prs, github_available=github_available)
                self.assertEqual(row.risk, risk)

    def test_action_mapping(self) -> None:
        cases = {
            "merged": "delete_safe",
            "content_in_main": "delete_safe",
            "open_pr": "keep",
            "draft_pr": "keep",
            "closed_unmerged": "needs_owner_review",
            "no_pr": "needs_owner_review",
            "unknown": "needs_owner_review",
        }
        for category, action in cases.items():
            with self.subTest(category=category):
                facts = _facts(
                    ancestor=category == "merged",
                    content=True if category == "content_in_main" else None,
                )
                prs: list[PullRequest] = []
                github_available = True
                if category == "open_pr":
                    prs = [_pr(1, state="open")]
                elif category == "draft_pr":
                    prs = [_pr(2, state="open", draft=True)]
                elif category == "closed_unmerged":
                    prs = [_pr(3, state="closed")]
                elif category == "unknown":
                    github_available = False
                row = build_row(facts, prs, github_available=github_available)
                self.assertEqual(row.category, category)
                self.assertEqual(row.action, action)

    def test_tri_state_and_missing_merge_base(self) -> None:
        row = build_row(
            _facts(merge_base=None, content=None), [], github_available=False
        )
        self.assertEqual(row.merge_base, "none")
        self.assertEqual(row.content_in_main, "unknown")
        self.assertEqual(row.ancestor_of_main, "false")

    def test_pull_request_format_is_sorted_and_stable(self) -> None:
        prs = [
            _pr(9, state="closed", draft=True, base="main"),
            _pr(2, state="closed", merged=True, base="main"),
        ]
        self.assertEqual(
            format_pull_requests(prs),
            "#2(closed,merged,base=main) #9(closed,draft,base=main)",
        )
        self.assertEqual(format_pull_requests([]), "-")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Audit Test",
            "GIT_AUTHOR_EMAIL": "audit@example.test",
            "GIT_COMMITTER_NAME": "Audit Test",
            "GIT_COMMITTER_EMAIL": "audit@example.test",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
    ).stdout.strip()


class GitFactsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self._temporary.name)
        _git(self.repo, "init", "--initial-branch=main")
        (self.repo / "base.txt").write_text("base\n", encoding="utf-8")
        _git(self.repo, "add", "base.txt")
        _git(self.repo, "commit", "-m", "base")
        self.base_sha = _git(self.repo, "rev-parse", "HEAD")

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _commit_on_branch(self, branch: str, path: str, content: str) -> str:
        _git(self.repo, "checkout", "-q", "-B", branch, self.base_sha)
        (self.repo / path).write_text(content, encoding="utf-8")
        _git(self.repo, "add", path)
        _git(self.repo, "commit", "-m", f"change {path}")
        return _git(self.repo, "rev-parse", "HEAD")

    def test_list_remote_branches_excludes_base_and_head_and_sorts(self) -> None:
        target = "refs/remotes/origin"
        for name in ("zeta", "alpha", "main"):
            _git(self.repo, "update-ref", f"{target}/{name}", self.base_sha)
        _git(self.repo, "update-ref", f"{target}/HEAD", self.base_sha)

        with patch("tools.branch_inventory_audit.REPO_ROOT", self.repo):
            branches = list_remote_branches("origin", "main")
        self.assertEqual([name for name, _ in branches], ["alpha", "zeta"])

    def test_content_in_main_detects_squash_equivalent_change(self) -> None:
        branch_sha = self._commit_on_branch("feature", "feature.txt", "same\n")
        _git(self.repo, "checkout", "-q", "main")
        (self.repo / "feature.txt").write_text("same\n", encoding="utf-8")
        _git(self.repo, "add", "feature.txt")
        _git(self.repo, "commit", "-m", "squash feature")
        main_sha = _git(self.repo, "rev-parse", "HEAD")

        with patch("tools.branch_inventory_audit.REPO_ROOT", self.repo):
            facts = collect_branch_facts("feature", branch_sha, main_sha)
        self.assertFalse(facts.ancestor_of_main)
        self.assertEqual(facts.files_changed, 1)
        self.assertTrue(facts.content_in_main)

    def test_content_in_main_false_when_content_differs(self) -> None:
        branch_sha = self._commit_on_branch("feature", "feature.txt", "branch\n")

        with patch("tools.branch_inventory_audit.REPO_ROOT", self.repo):
            facts = collect_branch_facts("feature", branch_sha, self.base_sha)
        self.assertFalse(facts.content_in_main)

    def test_ancestor_detection(self) -> None:
        _git(self.repo, "checkout", "-q", "main")
        _git(self.repo, "commit", "-q", "--allow-empty", "-m", "later")
        later = _git(self.repo, "rev-parse", "HEAD")

        with patch("tools.branch_inventory_audit.REPO_ROOT", self.repo):
            facts = collect_branch_facts("feature", self.base_sha, later)
        self.assertTrue(facts.ancestor_of_main)


class ReadOnlyGuaranteeTests(unittest.TestCase):
    def test_source_has_no_ref_mutation_path(self) -> None:
        forbidden = [
            r"--force-with-lease",
            r"--delete\b",
            r"branch\s+-D",
            r"push\s+--force",
            r'"DELETE"',
            r"git/refs",
            r"delete_refs",
        ]
        for pattern in forbidden:
            with self.subTest(pattern=pattern):
                self.assertIsNone(
                    re.search(pattern, _SOURCE),
                    f"read-only audit must not contain {pattern!r}",
                )

    def test_help_exits_zero(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(
            SystemExit
        ) as caught:
            main(["--help"])
        self.assertEqual(caught.exception.code, 0)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self._temporary.name)
        _git(self.repo, "init", "--initial-branch=main")
        (self.repo / "base.txt").write_text("base\n", encoding="utf-8")
        _git(self.repo, "add", "base.txt")
        _git(self.repo, "commit", "-m", "base")
        self.base_sha = _git(self.repo, "rev-parse", "HEAD")
        _git(self.repo, "checkout", "-q", "-B", "feat/only-git", self.base_sha)
        (self.repo / "feat.txt").write_text("feat\n", encoding="utf-8")
        _git(self.repo, "add", "feat.txt")
        _git(self.repo, "commit", "-m", "feat")
        self.feat_sha = _git(self.repo, "rev-parse", "HEAD")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", self.base_sha)
        _git(self.repo, "update-ref", "refs/remotes/origin/feat/only-git", self.feat_sha)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_offline_run_reports_without_network(self) -> None:
        buffer = io.StringIO()
        with patch("tools.branch_inventory_audit.REPO_ROOT", self.repo), patch(
            "tools.branch_inventory_audit.load_pull_requests",
            side_effect=AssertionError("network must not be used with --no-github"),
        ), contextlib.redirect_stdout(buffer):
            self.assertEqual(main(["--no-github", "--format", "json"]), 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual([row["branch"] for row in payload["branches"]], ["feat/only-git"])
        self.assertEqual(payload["branches"][0]["category"], "unknown")
        self.assertEqual(payload["branches"][0]["action"], "needs_owner_review")

    def test_offline_run_writes_tsv_to_file(self) -> None:
        output = self.repo / "out" / "snapshot.tsv"
        with patch("tools.branch_inventory_audit.REPO_ROOT", self.repo):
            self.assertEqual(
                main(["--no-github", "--format", "tsv", "--output", str(output)]), 0
            )
        text = output.read_text(encoding="utf-8")
        self.assertIn("branch\thead_sha\t", text)
        self.assertIn("feat/only-git\t" + self.feat_sha, text)
        self.assertNotIn("\nmain\t", text)

    def test_missing_base_ref_is_a_clean_failure(self) -> None:
        with patch("tools.branch_inventory_audit.REPO_ROOT", self.repo):
            with self.assertRaises(AuditError):
                main(["--no-github", "--base", "does-not-exist"])


class GitHubDegradationTests(unittest.TestCase):
    def test_missing_gh_client_returns_none(self) -> None:
        with patch(
            "tools.branch_inventory_audit.subprocess.run",
            side_effect=FileNotFoundError("gh"),
        ):
            self.assertIsNone(load_pull_requests("owner/repo"))

    def test_failed_api_call_returns_none(self) -> None:
        with patch(
            "tools.branch_inventory_audit.subprocess.run",
            return_value=subprocess.CompletedProcess(["gh"], 1, "", "rate limited"),
        ):
            self.assertIsNone(load_pull_requests("owner/repo"))

    def test_invalid_json_returns_none(self) -> None:
        with patch(
            "tools.branch_inventory_audit.subprocess.run",
            return_value=subprocess.CompletedProcess(["gh"], 0, "not json", ""),
        ):
            self.assertIsNone(load_pull_requests("owner/repo"))

    def test_foreign_and_fork_heads_are_ignored_and_sorted(self) -> None:
        payload = [
            [
                {
                    "number": 5,
                    "state": "closed",
                    "draft": False,
                    "merged_at": "2026-11-01T00:00:00Z",
                    "head": {
                        "ref": "feat/example",
                        "sha": "a" * 40,
                        "repo": {"full_name": "owner/repo"},
                    },
                    "base": {"ref": "main"},
                },
                {
                    "number": 2,
                    "state": "open",
                    "draft": True,
                    "merged_at": None,
                    "head": {
                        "ref": "feat/example",
                        "sha": "b" * 40,
                        "repo": {"full_name": "owner/repo"},
                    },
                    "base": {"ref": "main"},
                },
                {
                    "number": 3,
                    "state": "open",
                    "draft": False,
                    "merged_at": None,
                    "head": {
                        "ref": "feat/fork",
                        "sha": "c" * 40,
                        "repo": {"full_name": "someone/fork"},
                    },
                    "base": {"ref": "main"},
                },
                {
                    "number": 4,
                    "state": "open",
                    "draft": False,
                    "merged_at": None,
                    "head": {"ref": "feat/deleted", "sha": "d" * 40, "repo": None},
                    "base": {"ref": "main"},
                },
            ]
        ]
        with patch(
            "tools.branch_inventory_audit.subprocess.run",
            return_value=subprocess.CompletedProcess(
                ["gh"], 0, json.dumps(payload), ""
            ),
        ):
            requests = load_pull_requests("owner/repo")
        assert requests is not None
        self.assertEqual([pr.number for pr in requests], [2, 5])
        merged = [pr for pr in requests if pr.merged]
        self.assertEqual([pr.number for pr in merged], [5])


class RenderingTests(unittest.TestCase):
    def _rows(self) -> list[AuditRow]:
        return [
            build_row(
                _facts(name="feat/zeta", ancestor=True),
                [],
                github_available=True,
            ),
            build_row(
                _facts(name="feat/alpha", content=True),
                [],
                github_available=True,
            ),
            build_row(
                _facts(name="feat/beta", content=False),
                [_pr(7, state="open", head_ref="feat/beta")],
                github_available=True,
            ),
        ]

    def test_tsv_is_deterministic_and_sorted(self) -> None:
        first = render_tsv(self._rows())
        second = render_tsv(list(reversed(self._rows())))
        self.assertEqual(first, second)
        lines = first.splitlines()
        self.assertTrue(lines[0].startswith("# Harbor Transfer Portal branch inventory"))
        header_index = next(
            index for index, line in enumerate(lines) if line.startswith("branch\t")
        )
        data = lines[header_index + 1 :]
        self.assertEqual(
            [line.split("\t")[0] for line in data],
            ["feat/alpha", "feat/beta", "feat/zeta"],
        )
        for line in data:
            self.assertEqual(len(line.split("\t")), 12)

    def test_tsv_summary_counts(self) -> None:
        text = render_tsv(self._rows())
        self.assertIn(
            "merged=1 content_in_main=1 open_pr=1 draft_pr=0 "
            "closed_unmerged=0 no_pr=0 unknown=0",
            text,
        )
        self.assertIn("delete_safe=2 keep=1 needs_owner_review=0", text)

    def test_json_is_deterministic_and_sorted(self) -> None:
        payload = json.loads(render_json(self._rows(), base="main"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["mode"], "read-only")
        self.assertEqual(
            [item["branch"] for item in payload["branches"]],
            ["feat/alpha", "feat/beta", "feat/zeta"],
        )
        self.assertEqual(payload["summary"]["categories"]["merged"], 1)
        self.assertEqual(
            render_json(self._rows(), base="main"),
            render_json(list(reversed(self._rows())), base="main"),
        )

    def test_merged_flag_combines_ancestry_and_content(self) -> None:
        ancestor_row = build_row(
            _facts(name="feat/ancestor", ancestor=True), [], github_available=True
        )
        content_row = build_row(
            _facts(name="feat/content", content=True), [], github_available=True
        )
        review_row = build_row(
            _facts(name="feat/review", content=False), [], github_available=True
        )
        merged_pr_row = build_row(
            _facts(name="feat/merged-pr", content=False),
            [_pr(20, merged=True)],
            github_available=True,
        )
        nexus_row = build_row(
            _facts(name="feat/nexus", content=False),
            [_pr(21, merged=True, base="integration/nexus-oss-release-candidate-451")],
            github_available=True,
        )
        self.assertEqual(ancestor_row.merged_into_main, "true")
        self.assertEqual(content_row.merged_into_main, "true")
        self.assertEqual(merged_pr_row.merged_into_main, "true")
        self.assertEqual(nexus_row.merged_into_main, "false")
        self.assertEqual(review_row.merged_into_main, "false")

    def test_summary_covers_every_category(self) -> None:
        summary = summarize(self._rows())
        self.assertEqual(
            sorted(summary["categories"]),
            sorted(
                [
                    "merged",
                    "content_in_main",
                    "open_pr",
                    "draft_pr",
                    "closed_unmerged",
                    "no_pr",
                    "unknown",
                ]
            ),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()