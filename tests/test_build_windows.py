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
    def run_build(self, directory, push_output):
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
            return SimpleNamespace(returncode=0, stdout=output)

        environment = {
            "BUILD_ENTRY": json.dumps(entry),
            "BUILD_PLATFORM": "windows/amd64",
            "IMAGE": "example/app",
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            "RECORD_DIRECTORY": directory,
        }
        with (
            patch.dict("os.environ", environment, clear=True),
            patch("build_windows.docker", side_effect=docker_result) as docker,
        ):
            main()
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
                "example/app:wf-v4-123-1-b000",
                capture=True,
            )
            docker.assert_any_call(
                "image", "pull", "example/app:latest-ltsc2019", check=False
            )

    def test_missing_or_ambiguous_push_digest_does_not_write_a_record(self):
        for output in ("", "latest", IMAGE_DIGEST + "\n" + INDEX_DIGEST):
            with (
                self.subTest(output=output),
                tempfile.TemporaryDirectory() as directory,
            ):
                with self.assertRaisesRegex(ValueError, "immutable image digest"):
                    self.run_build(directory, output)
                self.assertFalse((Path(directory) / "b000.json").exists())
