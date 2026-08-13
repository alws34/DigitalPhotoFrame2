import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

# Ensure Utilities can be imported
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

from Utilities.autoupdate_utils import AutoUpdater  # noqa: E402


class TestAutoUpdater(unittest.TestCase):

    def setUp(self):
        self.stop_event = threading.Event()
        self.updater = AutoUpdater(
            stop_event=self.stop_event,
            interval_sec=1,
            auto_restart_on_update=False
        )
        self.repo_path = tempfile.mkdtemp()
        self.db_path = os.path.join(self.repo_path, "database.db")

    def tearDown(self):
        shutil.rmtree(self.repo_path, ignore_errors=True)

    # ----------------------------------------------------------------
    # 1. Settings DB backup / restore
    # ----------------------------------------------------------------
    def test_backup_settings_copies_db(self):
        with open(self.db_path, "wb") as f:
            f.write(b"fake-sqlite-bytes")

        with patch("Utilities.config_store._get_db_path", return_value=self.db_path):
            backup_root = self.updater._backup_settings(self.repo_path)

        self.assertIsNotNone(backup_root)
        backed_up = os.path.join(backup_root, "database.db")
        self.assertTrue(os.path.isfile(backed_up))
        with open(backed_up, "rb") as f:
            self.assertEqual(f.read(), b"fake-sqlite-bytes")

    def test_backup_settings_missing_db_returns_none(self):
        with patch("Utilities.config_store._get_db_path", return_value=self.db_path):
            backup_root = self.updater._backup_settings(self.repo_path)
        self.assertIsNone(backup_root)

    def test_restore_settings_does_not_overwrite_live_db(self):
        # Common case: checkout never touched the (untracked) live DB.
        # Restoring must not clobber it with the pre-update backup.
        backup_root = os.path.join(self.repo_path, ".autoupdate_backups", "20260101-120000")
        os.makedirs(backup_root)
        with open(os.path.join(backup_root, "database.db"), "wb") as f:
            f.write(b"backup-bytes")
        with open(self.db_path, "wb") as f:
            f.write(b"live-bytes")

        with patch("Utilities.config_store._get_db_path", return_value=self.db_path):
            self.updater._restore_settings(self.repo_path, backup_root)

        with open(self.db_path, "rb") as f:
            self.assertEqual(f.read(), b"live-bytes")

    def test_restore_settings_recovers_deleted_db(self):
        backup_root = os.path.join(self.repo_path, ".autoupdate_backups", "20260101-120000")
        os.makedirs(backup_root)
        with open(os.path.join(backup_root, "database.db"), "wb") as f:
            f.write(b"backup-bytes")
        # self.db_path deliberately never created -- simulates checkout
        # deleting a still-tracked database.db on an old device.

        with patch("Utilities.config_store._get_db_path", return_value=self.db_path):
            self.updater._restore_settings(self.repo_path, backup_root)

        with open(self.db_path, "rb") as f:
            self.assertEqual(f.read(), b"backup-bytes")

    # ----------------------------------------------------------------
    # 2. Git Operations
    # ----------------------------------------------------------------
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/git")
    def test_check_current_tag(self, mock_which, mock_run):
        # IMPORTANT: Set stderr to empty string, otherwise it's a MagicMock object
        # which causes string concatenation errors in _run_git
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "v1.0.1\n"
        mock_run.return_value.stderr = ""

        tag = self.updater._current_semver_tag(self.repo_path, {}, 10)
        self.assertEqual(tag, "v1.0.1")

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/git")
    def test_list_remote_tags(self, mock_which, mock_run):
        output = (
            "hash1\trefs/tags/v1.0.0\n"
            "hash2\trefs/tags/v1.1.0\n"
            "hash3\trefs/tags/beta-release\n" 
            "hash4\trefs/tags/v1.1.0^{}\n"
        )
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = output
        mock_run.return_value.stderr = ""  # FIX

        tags = self.updater._list_remote_semver_tags("origin", {}, 10)
        self.assertIn("v1.0.0", tags)
        self.assertIn("v1.1.0", tags)
        self.assertNotIn("beta-release", tags)
        self.assertEqual(len(tags), 2)

    def test_semver_comparison(self):
        def ver(t): return self.updater._parse_semver(t)
        self.assertTrue(ver("v1.0.2") > ver("v1.0.1"))
        self.assertTrue(ver("v2.0.0") > ver("v1.9.9"))
        self.assertTrue(ver("1.0.0") == ver("v1.0.0"))
        
        tags = ["v1.0.0", "v1.0.5", "v1.0.2"]
        max_t = self.updater._max_tag(tags)
        self.assertEqual(max_t, "v1.0.5")


if __name__ == "__main__":
    unittest.main()