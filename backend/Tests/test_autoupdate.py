import json
import os
import sys
import threading
import unittest
from unittest.mock import mock_open, patch

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
        self.repo_path = "/fake/repo"
        self.backup_path = "/fake/repo/.autoupdate_backups/20260101-120000/settings.json"
        self.dest_path = "/fake/repo/settings.json"

    # ----------------------------------------------------------------
    # 1. Config Migration Logic
    # ----------------------------------------------------------------
    def test_migrate_old_flat_config_to_new_nested(self):
        user_old_data = {
            "font_name": "Comic Sans",
            "time_font_size": 200,
            "margin_left": 10,
            "service_name": "MyPhotoFrame",
            "backend_configs": {"host": "1.1.1.1", "server_port": 9000},
            "custom_unknown_key": "keep_me"
        }

        new_template_data = {
            "ui": {
                "font_name": "arial.ttf",
                "time_font_size": 120,
                "margins": {"left": 80, "bottom": 30}
            },
            "system": {"service_name": "PhotoFrame_Default"},
            "backend_configs": {"host": "0.0.0.0", "server_port": 5002}
        }

        merged = self.updater._migrate_config_structure(user_old_data, new_template_data)

        self.assertEqual(merged["ui"]["font_name"], "Comic Sans")
        self.assertEqual(merged["ui"]["time_font_size"], 200)
        self.assertEqual(merged["ui"]["margins"]["left"], 10)
        self.assertEqual(merged["ui"]["margins"]["bottom"], 30)
        self.assertEqual(merged["backend_configs"]["server_port"], 9000)
        self.assertEqual(merged["system"]["service_name"], "MyPhotoFrame")

    def test_migrate_already_migrated_config(self):
        user_mixed_data = {
            "date_font_size": 99,
            "ui": {"font_name": "AlreadyNew", "margins": {"right": 5}}
        }
        new_template = {
            "ui": {"font_name": "Default", "date_font_size": 10, "margins": {"right": 50, "left": 50}}
        }

        merged = self.updater._migrate_config_structure(user_mixed_data, new_template)

        self.assertEqual(merged["ui"]["date_font_size"], 99)
        self.assertEqual(merged["ui"]["font_name"], "AlreadyNew")
        self.assertEqual(merged["ui"]["margins"]["right"], 5)
        self.assertEqual(merged["ui"]["margins"]["left"], 50)

    # ----------------------------------------------------------------
    # 2. File Restore Integration
    # ----------------------------------------------------------------
    @patch("builtins.open", new_callable=mock_open)
    @patch("os.makedirs")
    @patch("shutil.copy2")
    @patch("os.path.exists", return_value=True)
    def test_restore_and_migrate_single_file(self, mock_exists, mock_copy, mock_makedirs, mock_file):
        backup_json = '{"font_name": "UserFont"}'
        template_json = '{"ui": {"font_name": "Default"}}'
        
        # side_effect controls return values of consecutive open() calls
        # 1. Read Backup
        # 2. Read Template
        # Note: The 3rd open call is for writing, which mock_open handles automatically
        mock_file.return_value.read.side_effect = [backup_json, template_json]
        
        self.updater._restore_and_migrate_single_file(self.backup_path, self.dest_path)

        # Get the mock file handle used
        file_handle = mock_file.return_value
        
        # Verify write was called
        self.assertTrue(file_handle.write.called, "File.write() was never called")
        
        # Combine all writes (json.dump might write in chunks)
        written_content = "".join(args[0] for args, _ in file_handle.write.call_args_list)
        written_data = json.loads(written_content)
        
        self.assertEqual(written_data["ui"]["font_name"], "UserFont")

    # ----------------------------------------------------------------
    # 3. Git Operations
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