import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_group import bake_definition, classic, local_result
from build_windows import build_command
from configuration_fixtures import example
from configure import make_plan

DIGEST = "sha256:" + "1" * 64


def plan(kind):
    return make_plan(example(kind))


class GroupTests(unittest.TestCase):
    def test_classic_siblings_use_local_cache_and_continue_after_failure(self):
        candidate = plan("windows-families")
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("os.environ", {}),
            patch(
                "build_group.build_windows.main",
                side_effect=[ValueError("first variant failed"), None],
            ) as build,
        ):
            with self.assertRaisesRegex(ValueError, "Required builds failed: b000"):
                classic(candidate, "g000", "windows/amd64", directory)
            self.assertEqual(
                [False, True],
                [call.kwargs["use_local_cache"] for call in build.call_args_list],
            )

    def test_unrestricted_classic_cache_does_not_specify_cache_from(self):
        build = {**plan("windows-families")["builds"][0], "platform": "windows/amd64"}
        self.assertNotIn("--cache-from", build_command(build, "tmp/app:trial"))

    def test_sibling_caches_are_imported_but_have_distinct_export_owners(self):
        definition = bake_definition(plan("multi-platform"), "g000", "linux/amd64")
        targets = list(definition["target"].values())
        self.assertEqual(7, len(targets))
        self.assertTrue(all(len(target["cache-from"]) == 7 for target in targets))
        self.assertEqual(7, len({target["cache-to"][0] for target in targets}))
        self.assertEqual(
            {f"1.{minor}" for minor in range(7)},
            {target["args"]["APP_VERSION"] for target in targets},
        )

    def test_trial_builds_export_archives_without_pushing(self):
        targets = bake_definition(
            plan("multi-platform"), "g000", "linux/amd64", publish=False
        )["target"]
        self.assertEqual(["type=oci,dest=bake/b000.tar"], targets["b000"]["output"])

    def test_windows_records_require_actual_os_version(self):
        build = {"id": "b000"}
        config = {
            "os": "windows",
            "architecture": "amd64",
            "os.version": "10.0.17763.1",
        }
        record = local_result(build, DIGEST, "windows/amd64", config)
        self.assertTrue(record["local"])
        self.assertEqual("10.0.17763.1", record["platform"]["os.version"])
        del config["os.version"]
        with self.assertRaisesRegex(ValueError, "Missing Windows OS version"):
            local_result(build, DIGEST, "windows/amd64", config)


if __name__ == "__main__":
    unittest.main()
