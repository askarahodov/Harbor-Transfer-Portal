from __future__ import annotations

import subprocess
import unittest
from unittest.mock import call, patch

from atomic_ref_cleanup import delete_refs


class AtomicRefCleanupTests(unittest.TestCase):
    REPO = "askarahodov/Harbor-Transfer-Portal"

    @patch("atomic_ref_cleanup.subprocess.run")
    def test_dry_run_uses_atomic_explicit_leases_and_exact_deletions(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(["gh"], 0, "", ""),
            subprocess.CompletedProcess(["git"], 0, "https://github.com/askarahodov/Harbor-Transfer-Portal.git", ""),
            subprocess.CompletedProcess(["git"], 0, "dry run ok", ""),
        ]
        output = delete_refs(
            self.REPO,
            [("feature/one", "a" * 40), ("chore/cleanup", "b" * 40)],
            dry_run=True,
        )
        command = run.call_args_list[-1].args[0]
        self.assertIn("--atomic", command)
        self.assertIn("--dry-run", command)
        self.assertIn("--force-with-lease=refs/heads/feature/one:" + "a" * 40, command)
        self.assertIn("--force-with-lease=refs/heads/chore/cleanup:" + "b" * 40, command)
        self.assertIn(":refs/heads/feature/one", command)
        self.assertIn(":refs/heads/chore/cleanup", command)
        self.assertIn("CAS dry-run passed", output)

    @patch("atomic_ref_cleanup.subprocess.run")
    def test_stale_lease_failure_is_fail_closed(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(["gh"], 0, "", ""),
            subprocess.CompletedProcess(["git"], 0, "https://github.com/askarahodov/Harbor-Transfer-Portal.git", ""),
            subprocess.CompletedProcess(["git"], 1, "", "stale info"),
        ]
        with self.assertRaisesRegex(RuntimeError, "compare-and-swap"):
            delete_refs(self.REPO, [("feature/one", "a" * 40)])

    @patch("atomic_ref_cleanup.subprocess.run")
    def test_wrong_remote_is_rejected_before_push(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(["gh"], 0, "", ""),
            subprocess.CompletedProcess(["git"], 0, "https://github.com/another/repo.git", ""),
        ]
        with self.assertRaisesRegex(RuntimeError, "expected GitHub repository"):
            delete_refs(self.REPO, [("feature/one", "a" * 40)])
        self.assertEqual(run.call_count, 2)

    @patch("atomic_ref_cleanup.subprocess.run")
    def test_duplicate_refs_and_invalid_sha_are_rejected_before_auth(self, run):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            delete_refs(self.REPO, [("feature/one", "a" * 40), ("feature/one", "b" * 40)])
        with self.assertRaisesRegex(ValueError, "expected SHA"):
            delete_refs(self.REPO, [("feature/one", "not-a-sha")])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
