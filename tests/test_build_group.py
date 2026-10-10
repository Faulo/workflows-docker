import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_group import bake_definition, record_results
from configure import make_plan

ROOT = Path(__file__).resolve().parents[1]
DIGEST = "sha256:" + "1" * 64


def plan(project):
    config = json.loads(
        (ROOT / "examples" / "fleet" / f"docker-{project}.json").read_text()
    )
    return make_plan(
        config, environment={"DOCKER_NAMESPACE": "faulo", "DOCKER_IMAGE": project}
    )


class GroupTests(unittest.TestCase):
    def test_sibling_caches_are_imported_but_have_distinct_export_owners(self):
        definition = bake_definition(plan("farah"), "g000", "linux/amd64")
        targets = list(definition["target"].values())
        self.assertEqual(7, len(targets))
        self.assertTrue(all(len(target["cache-from"]) == 7 for target in targets))
        self.assertEqual(7, len({target["cache-to"][0] for target in targets}))
        self.assertEqual(
            {"7.4", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5"},
            {target["args"]["PHP_VERSION"] for target in targets},
        )

    def test_windows_variants_keep_their_own_base_images(self):
        definition = bake_definition(plan("choco"), "g000", "windows/amd64")
        self.assertEqual(["b000", "b002"], definition["group"]["default"]["targets"])
        targets = definition["target"]
        self.assertNotEqual(
            targets["b000"]["args"]["BASE_IMAGE"], targets["b002"]["args"]["BASE_IMAGE"]
        )
        self.assertTrue(
            all(
                "type=provenance,disabled=true" in target["attest"]
                for target in targets.values()
            )
        )

    def test_trial_builds_export_archives_without_pushing(self):
        targets = bake_definition(
            plan("choco"), "g000", "windows/amd64", publish=False
        )["target"]
        self.assertEqual(["type=oci,dest=bake/b000.tar"], targets["b000"]["output"])

    def test_windows_records_require_actual_os_version(self):
        candidate = plan("choco")
        metadata = {
            build_id: {"containerimage.digest": DIGEST}
            for build_id in candidate["groups"][0]["builds"]
        }
        with tempfile.TemporaryDirectory() as directory:
            inspect = lambda *_: {
                "os": "windows",
                "architecture": "amd64",
                "os.version": "10.0.17763.1",
            }
            record_results(
                candidate, "g000", "windows/amd64", metadata, directory, inspect=inspect
            )
            record = json.loads((Path(directory) / "b000.json").read_text())
            self.assertEqual("10.0.17763.1", record["platform"]["os.version"])
            with self.assertRaisesRegex(ValueError, "Missing Windows OS version"):
                record_results(
                    candidate,
                    "g000",
                    "windows/amd64",
                    metadata,
                    directory,
                    inspect=lambda *_: {"os": "windows", "architecture": "amd64"},
                )


if __name__ == "__main__":
    unittest.main()
