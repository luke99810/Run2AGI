from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DELIVERY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DELIVERY_ROOT))

from codentum_delivery.secret_scan import scan_git_history, scan_text, scan_worktree


class SecretScanTests(unittest.TestCase):
    def test_detects_and_redacts_a_secret(self) -> None:
        secret = "sk" + "-" + "A" * 28
        findings = scan_text(f'api_key="{secret}"', source="test", path="config.py")
        self.assertGreaterEqual(len(findings), 1)
        rendered = repr(findings)
        self.assertNotIn(secret, rendered)
        self.assertIn("…", findings[0].redacted_preview)

    def test_ignores_explicit_placeholders(self) -> None:
        findings = scan_text(
            'api_key="REPLACE_ME_WITH_YOUR_KEY"',
            source="test",
            path="example.env",
        )
        self.assertEqual(findings, ())

    def test_worktree_scans_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = "ghp" + "_" + "B" * 24
            (root / ".env").write_text(f'TOKEN="{secret}"\n', encoding="utf-8")
            scanned, findings = scan_worktree(root)
            self.assertEqual(scanned, 1)
            self.assertTrue(findings)
            self.assertNotIn(secret, repr(findings))

    def test_history_finds_a_secret_deleted_from_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._git(root, "init")
            self._git(root, "config", "user.name", "Delivery Test")
            self._git(root, "config", "user.email", "delivery@example.invalid")
            secret = "glpat" + "-" + "C" * 24
            leaked = root / "deleted.env"
            leaked.write_text(f'TOKEN="{secret}"\n', encoding="utf-8")
            self._git(root, "add", "deleted.env")
            self._git(root, "commit", "-m", "add fixture")
            leaked.unlink()
            self._git(root, "add", "-u")
            self._git(root, "commit", "-m", "remove fixture")

            worktree_count, worktree_findings = scan_worktree(root)
            history_count, history_findings = scan_git_history(root)
            self.assertGreaterEqual(worktree_count, 0)
            self.assertEqual(worktree_findings, ())
            self.assertGreater(history_count, 0)
            self.assertTrue(any(item.path == "deleted.env" for item in history_findings))
            self.assertNotIn(secret, repr(history_findings))

    @staticmethod
    def _git(root: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
