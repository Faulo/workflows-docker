import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_windows_runner as preparation


class RunnerPreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.installation = self.root / "Visual Studio"
        self.installation.mkdir()
        self.targets = patch.object(preparation, "TARGETS", (self.installation,))
        self.targets.start()
        self.addCleanup(self.targets.stop)

    def test_only_explicit_hosted_windows_opt_in_can_remove_files(self):
        environment = {
            "RUNNER_OS": "Windows",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "CLEANUP_WINDOWS_RUNNER": "true",
        }
        for change in (
            {"RUNNER_OS": "Linux"},
            {"RUNNER_ENVIRONMENT": "self-hosted"},
            {"RUNNER_ENVIRONMENT": ""},
            {"CLEANUP_WINDOWS_RUNNER": "false"},
            {"CLEANUP_WINDOWS_RUNNER": ""},
        ):
            with (
                self.subTest(change=change),
                patch.dict(os.environ, environment | change, clear=True),
                patch.object(preparation, "free_space") as disk,
                patch.object(preparation, "remove_installation") as remove,
            ):
                preparation.main()
                disk.assert_not_called()
                remove.assert_not_called()

    def test_existing_installation_and_readonly_files_are_removed(self):
        nested = self.installation / "nested"
        nested.mkdir()
        readonly = nested / "compiler.exe"
        readonly.write_bytes(b"test compiler")
        readonly.chmod(stat.S_IREAD)
        preparation.inventory(self.installation)
        preparation.remove_installation(self.installation)
        self.assertFalse(self.installation.exists())

    def test_missing_target_is_skipped_but_regular_file_is_rejected(self):
        self.installation.rmdir()
        self.assertFalse(preparation.validate_target(self.installation))
        self.installation.write_text("unexpected file")
        with self.assertRaisesRegex(ValueError, "unredirected directory"):
            preparation.validate_target(self.installation)

    def test_unknown_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            preparation.validate_target(self.root)
        self.assertTrue(self.root.exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction semantics")
    def test_nested_junction_is_removed_without_touching_destination(self):
        destination = self.root / "keep"
        destination.mkdir()
        sentinel = destination / "keep.txt"
        sentinel.write_text("must survive")
        junction = self.installation / "linked tools"
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(destination)],
            check=True,
            capture_output=True,
        )
        preparation.inventory(self.installation)
        preparation.remove_installation(self.installation)
        self.assertEqual("must survive", sentinel.read_text())

    @unittest.skipUnless(os.name == "nt", "Windows junction semantics")
    def test_redirected_target_and_parent_are_rejected(self):
        destination = self.root / "keep"
        destination.mkdir()
        self.installation.rmdir()
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(self.installation), str(destination)],
            check=True,
            capture_output=True,
        )
        for candidate in (self.installation, self.installation / "child"):
            with (
                self.subTest(candidate=candidate),
                patch.object(preparation, "TARGETS", (candidate,)),
                self.assertRaisesRegex(ValueError, "unredirected directory"),
            ):
                preparation.validate_target(candidate)
        self.assertTrue(destination.exists())

    def test_unknown_reparse_types_fail_but_compressed_files_are_supported(self):
        unknown = SimpleNamespace(
            st_mode=stat.S_IFREG,
            st_file_attributes=preparation.REPARSE_POINT,
            st_reparse_tag=123,
        )
        with self.assertRaisesRegex(ValueError, "Unsupported reparse"):
            preparation.classify(unknown)
        unknown.st_reparse_tag = preparation.WOF
        self.assertEqual("file", preparation.classify(unknown))

    def test_unrelated_permission_errors_propagate_without_chmod(self):
        item = self.installation / "file"
        item.write_text("keep")
        action = Mock(side_effect=PermissionError("access denied"))
        with (
            patch.object(preparation.os, "chmod") as chmod,
            self.assertRaises(PermissionError),
        ):
            preparation.remove_readonly(action, item)
        chmod.assert_not_called()
        self.assertEqual("keep", item.read_text())

    def test_all_targets_are_inspected_before_any_deletion(self):
        other = self.root / "unexpected-file"
        other.write_text("preserve")
        environment = {
            "RUNNER_OS": "Windows",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "CLEANUP_WINDOWS_RUNNER": "true",
        }
        with (
            patch.object(preparation.os, "name", "nt"),
            patch.dict(os.environ, environment, clear=True),
            patch.object(preparation, "TARGETS", (self.installation, other)),
            patch.object(preparation, "verify_host_tools"),
            patch.object(preparation, "free_space", return_value=100),
            patch.object(preparation, "remove_installation") as remove,
            self.assertRaises(ValueError),
        ):
            preparation.main()
        remove.assert_not_called()
        self.assertTrue(self.installation.exists())
        self.assertTrue(other.exists())

    def test_opted_in_run_checks_tools_and_logs_space_even_on_failure(self):
        environment = {
            "RUNNER_OS": "Windows",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "CLEANUP_WINDOWS_RUNNER": "true",
        }
        with (
            patch.object(preparation.os, "name", "nt"),
            patch.dict(os.environ, environment, clear=True),
            patch.object(preparation, "validate_target", return_value=True),
            patch.object(preparation, "inventory"),
            patch.object(preparation, "verify_host_tools") as tools,
            patch.object(preparation, "free_space", return_value=100) as disk,
            patch.object(
                preparation, "remove_installation", side_effect=OSError("failed")
            ),
            self.assertRaises(OSError),
        ):
            preparation.main()
        tools.assert_called_once()
        self.assertEqual("After Visual Studio cleanup", disk.call_args.args[0])
