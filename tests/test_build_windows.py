import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_windows import main

IMAGE_DIGEST = "sha256:" + "1" * 64
INDEX_DIGEST = "sha256:" + "2" * 64


class WindowsBuildTests(unittest.TestCase):
    def run_build(self, directory, push_output, cache=True, local=False, pull_code=0):
        entry = {
            "id": "b000",
            "variant": "latest",
            "tag": "latest-ltsc2019",
            "dockerfile": "windows/Dockerfile",
            "buildArgs": {},
            "context": ".",
        }
        inspection = {
            "Os": "windows",
            "Architecture": "amd64",
            "OsVersion": "10.0.17763.1",
            "RepoDigests": [
                f"example/app@{INDEX_DIGEST}",
                f"example/app@{IMAGE_DIGEST}",
            ],
        }

        def docker_result(*args, **kwargs):
            output = ""
            if args[:2] == ("image", "inspect"):
                output = json.dumps([inspection])
            elif args[:2] == ("image", "push"):
                output = "The push refers to repository [example/app]"
            elif args[:2] == ("manifest", "inspect"):
                output = json.dumps({"Descriptor": {"digest": push_output}})
            return SimpleNamespace(
                returncode=pull_code if args[:2] == ("image", "pull") else 0,
                stdout=output,
            )

        environment = {
            "BUILD_ENTRY": json.dumps(entry),
            "BUILD_PLATFORM": "windows/amd64",
            "IMAGE": "example/app",
            "GITHUB_RUN_ID": "123",
            "GITHUB_SHA": "a" * 40,
            "RECORD_DIRECTORY": directory,
        }
        with (
            patch.dict("os.environ", environment, clear=True),
            patch("build_windows.docker", side_effect=docker_result) as docker,
            patch(
                "build_windows.eligible_cache",
                return_value=(
                    f"example/app:latest-ltsc2019@{IMAGE_DIGEST}" if cache else None
                ),
            ),
        ):
            main(use_local_cache=local)
            return docker

    def test_cached_image_with_index_and_image_digests_records_pushed_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            docker = self.run_build(directory, IMAGE_DIGEST)
            record = json.loads((Path(directory) / "b000.json").read_text())
            self.assertEqual(IMAGE_DIGEST, record["digest"])
            self.assertEqual("10.0.17763.1", record["platform"]["os.version"])
            docker.assert_any_call(
                "manifest",
                "inspect",
                "--verbose",
                "example/app:latest-ltsc2019-aaaaaaaaaaaa-123",
                capture=True,
            )
            docker.assert_any_call(
                "image",
                "pull",
                f"example/app:latest-ltsc2019@{IMAGE_DIGEST}",
                check=False,
            )

    def test_ineligible_remote_cache_is_never_pulled_or_passed_to_build(self):
        with tempfile.TemporaryDirectory() as directory:
            docker = self.run_build(directory, IMAGE_DIGEST, cache=False)
        self.assertFalse(
            any(call.args[:2] == ("image", "pull") for call in docker.call_args_list)
        )
        build = next(
            call.args for call in docker.call_args_list if call.args[0] == "build"
        )
        self.assertNotIn("--cache-from", build)

    def test_failed_pull_and_sibling_build_keep_unrestricted_local_cache(self):
        for options in ({"pull_code": 1}, {"local": True}):
            with (
                self.subTest(options=options),
                tempfile.TemporaryDirectory() as directory,
            ):
                docker = self.run_build(directory, IMAGE_DIGEST, **options)
            build = next(
                call.args for call in docker.call_args_list if call.args[0] == "build"
            )
            self.assertNotIn("--cache-from", build)

    def test_missing_or_ambiguous_push_digest_does_not_write_a_record(self):
        for output in ("", "latest", IMAGE_DIGEST + "\n" + INDEX_DIGEST):
            with (
                self.subTest(output=output),
                tempfile.TemporaryDirectory() as directory,
            ):
                with self.assertRaisesRegex(ValueError, "immutable image digest"):
                    self.run_build(directory, output)
                self.assertFalse((Path(directory) / "b000.json").exists())
