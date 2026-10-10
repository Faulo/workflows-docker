import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from configure import make_plan
from verify_test_image import verify

ROOT = Path(__file__).resolve().parents[1]


class FixtureTests(unittest.TestCase):
    def setUp(self):
        config = json.loads((ROOT / "examples/test-image/docker.json").read_text())
        self.plan = make_plan(config, ROOT, environment={})
        self.records = {}
        for build in self.plan["builds"]:
            windows = build["target"].startswith("windows")
            platform = {
                "os": "windows" if windows else "linux",
                "architecture": "arm64"
                if build["target"] == "linux-arm64"
                else "amd64",
            }
            if windows:
                platform["os.version"] = (
                    "10.0.17763.1"
                    if build["target"] == "windows-2019"
                    else "10.0.20348.1"
                )
            self.records[build["id"]] = {
                "local": True,
                "platform": platform,
                "labels": {
                    "workflow.fixture.variant": build["variant"],
                    "workflow.fixture.target": build["target"],
                    "workflow.fixture.shared": "shared across variants",
                },
            }

    def test_fixture_has_eight_images_in_four_shared_jobs(self):
        self.assertEqual("tmp/workflow-fixture", self.plan["reference"])
        self.assertEqual(4, len(self.plan["groups"]))
        self.assertTrue(all(len(group["builds"]) == 2 for group in self.plan["groups"]))
        verify(self.plan, self.records)

    def test_fixture_verification_rejects_wrong_platform_arguments_or_publication(self):
        mutations = [
            lambda r: r["b000"].update(local=False),
            lambda r: r["b001"]["platform"].update(architecture="amd64"),
            lambda r: r["b002"]["platform"].update({"os.version": "10.0.20348.1"}),
            lambda r: r["b004"]["labels"].update({"workflow.fixture.variant": "1.0"}),
            lambda r: r["b000"]["labels"].update(
                {"workflow.fixture.shared": "$FIXTURE_MESSAGE"}
            ),
        ]
        for mutation in mutations:
            records = copy.deepcopy(self.records)
            mutation(records)
            with self.subTest(records=records), self.assertRaises(ValueError):
                verify(self.plan, records)


if __name__ == "__main__":
    unittest.main()
