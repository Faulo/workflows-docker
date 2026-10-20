import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cleanup import staging_tags
from configuration_fixtures import matrix_example
from configure import make_plan, unique_object
from publish import publish
from registry_fixtures import MemoryRegistry


class MatrixTests(unittest.TestCase):
    def test_recursive_product_inherits_resolves_and_groups_without_extra_builds(self):
        config = matrix_example()
        original = copy.deepcopy(config)
        plan = make_plan(config)
        self.assertEqual(original, config)
        self.assertEqual(8, len(plan["builds"]))
        self.assertEqual(4, len(plan["groups"]))
        self.assertTrue(all(len(group["builds"]) == 2 for group in plan["groups"]))
        self.assertEqual(14, len(plan["publications"]))
        tags = {
            tag: item["builds"] for item in plan["publications"] for tag in item["tags"]
        }
        self.assertEqual(["b004", "b005", "b006", "b007"], tags["latest"])
        self.assertEqual(["b004", "b005"], tags["latest-linux"])
        self.assertEqual(["b006", "b007"], tags["latest-windows"])
        self.assertEqual(["b006"], tags["latest-windows-ltsc2019"])
        self.assertEqual(tags["latest"], tags["2.0"])
        linux, windows = plan["builds"][5], plan["builds"][6]
        self.assertEqual("ubuntu-24.04-arm", linux["runner"])
        self.assertEqual("linux/Dockerfile", linux["dockerfile"])
        self.assertEqual({"APP_VERSION": "2.0"}, linux["buildArgs"])
        self.assertEqual("windows-latest", windows["runner"])
        self.assertEqual(
            {"APP_VERSION": "2.0", "OS_BASE": "ltsc2019"}, windows["buildArgs"]
        )
        self.assertEqual(
            {"version": "2.0", "os": "windows", "release": "ltsc2019"},
            windows["matrix"],
        )

    def test_aliases_multiply_but_variables_keep_primary_keys(self):
        config = matrix_example()
        windows = config["matrix"]["os"]["tags"]["windows"]
        windows["aliases"] = ["win"]
        windows["matrix"]["release"]["tags"]["ltsc2019"]["aliases"] = ["2019"]
        publication = next(
            p
            for p in make_plan(config)["publications"]
            if "latest-win-2019" in p["tags"]
        )
        self.assertEqual(8, len(publication["tags"]))
        self.assertEqual(["b006"], publication["builds"])

    def test_substitution_is_deferred_until_after_overrides_and_environment(self):
        config = matrix_example()
        config["buildArgs"] = {"COUNT": 2, "BOOL": True, "LITERAL": "$$HOME"}
        config["runner"] = "{{matrix.nonexistent}}"
        config["dockerfile"] = "missing/Dockerfile"
        config["matrix"]["version"]["buildArgs"].update(
            OS="{{matrix.os}}", APP_VERSION="$PREFIX-{{matrix.version}}"
        )
        config["matrix"]["os"]["tags"]["linux"]["buildArgs"] = {"COUNT": 3}
        build = make_plan(config, environment={"PREFIX": "release"})["builds"][0]
        self.assertEqual(
            {
                "COUNT": "3",
                "BOOL": "true",
                "LITERAL": "$HOME",
                "OS": "linux",
                "APP_VERSION": "release-1.0",
            },
            build["buildArgs"],
        )

    def test_nested_axes_expand_before_following_outer_axis(self):
        config = matrix_example()
        config["matrix"] = {
            "product": {
                "tags": {
                    "app": {"matrix": {"flavor": {"tags": {"small": {}, "large": {}}}}}
                }
            },
            "platform": {
                "runner": "ubuntu-latest",
                "dockerfile": "Dockerfile",
                "tags": {"linux": {}},
            },
        }
        plan = make_plan(config)
        self.assertEqual(
            ["app-small-linux", "app-large-linux"], [b["tag"] for b in plan["builds"]]
        )
        self.assertEqual(
            ["app", "app-small", "app-small-linux", "app-large", "app-large-linux"],
            [p["tags"][0] for p in plan["publications"]],
        )

    def test_single_axis_builds_and_no_alias_builds(self):
        config = {
            "schemaVersion": 3,
            "namespace": "example",
            "image": "app",
            "runner": "ubuntu-latest",
            "dockerfile": "Dockerfile",
            "matrix": {"product": {"tags": {"app": {"aliases": ["latest"]}}}},
        }
        plan = make_plan(config)
        self.assertEqual(1, len(plan["builds"]))
        self.assertEqual(
            [{"tags": ["app", "latest"], "builds": ["b000"]}], plan["publications"]
        )

    def test_invalid_shapes_and_unresolved_values_fail(self):
        mutations = [
            lambda c: c.update(matrix={}),
            lambda c: c.update(matrix=[]),
            lambda c: c.update(matrix={"bad-label": {"tags": {"x": {}}}}),
            lambda c: c["matrix"].update(version={"tags": {}}),
            lambda c: c["matrix"].update(version={"tags": []}),
            lambda c: c["matrix"].update(version={"tags": {"x": None}}),
            lambda c: c["matrix"]["version"].update(extra=True),
            lambda c: c["matrix"]["version"]["tags"]["1.0"].update(matrix={}),
            lambda c: c["matrix"]["version"]["tags"]["1.0"].update(aliases="a"),
            lambda c: c["matrix"]["version"]["tags"]["1.0"].update(aliases=[{}]),
            lambda c: c["matrix"]["version"]["tags"]["1.0"].update(aliases=["1.0"]),
            lambda c: c["matrix"]["version"]["tags"].update({"wf-v3-private": {}}),
            lambda c: c["matrix"]["version"]["tags"].update({"bad tag": {}}),
            lambda c: c.update(buildArgs={"X": "{{matrix.unknown}}"}),
            lambda c: c.update(buildArgs={"X": "{{other.version}}"}),
            lambda c: c.update(buildArgs={"X": "{{matrix.os"}),
            lambda c: c.update(timeoutMinutes=0),
            lambda c: c.update(variants=[]),
            lambda c: c["matrix"]["os"].update(dockerfile="../Dockerfile"),
        ]
        for mutation in mutations:
            config = matrix_example()
            mutation(config)
            with self.subTest(config=config), self.assertRaises(ValueError):
                make_plan(config)

    def test_repeated_axes_and_flattened_tag_collisions_fail(self):
        config = matrix_example()
        config["matrix"]["version"]["tags"]["1.0"]["matrix"] = {
            "os": {"tags": {"other": {}}}
        }
        with self.assertRaisesRegex(ValueError, "Repeated matrix axis"):
            make_plan(config)
        config = matrix_example()
        config["matrix"]["version"]["tags"]["1.0-linux"] = {}
        with self.assertRaisesRegex(ValueError, "Duplicate public tag"):
            make_plan(config)
        config = matrix_example()
        config["matrix"]["version"]["tags"]["2.0"]["aliases"] = ["x" * 128]
        with self.assertRaisesRegex(ValueError, "Invalid or reserved public tag"):
            make_plan(config)

    def test_missing_paths_and_job_limit_fail_before_building(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(ValueError, "does not exist"),
        ):
            make_plan(matrix_example(), Path(directory))
        config = matrix_example()
        config["matrix"]["os"]["tags"] = {
            str(i): {"runner": "ubuntu-latest", "dockerfile": "Dockerfile"}
            for i in range(257)
        }
        with self.assertRaisesRegex(ValueError, "256 matrix jobs"):
            make_plan(config)

    def test_duplicate_axis_or_tag_keys_are_rejected_during_json_parsing(self):
        for source in (
            '{"matrix":{"version":{},"version":{}}}',
            '{"tags":{"latest":{},"latest":{}}}',
        ):
            with (
                self.subTest(source=source),
                self.assertRaisesRegex(ValueError, "Duplicate JSON key"),
            ):
                json.loads(source, object_pairs_hook=unique_object)

    def test_publication_preserves_selected_sources_and_verifies_aliases(self):
        plan = make_plan(matrix_example())
        sources, records, manifests = {}, {}, {}
        for number, build in enumerate(plan["builds"]):
            digest = "sha256:" + format(number + 1, "064x")
            windows = build["matrix"]["os"] == "windows"
            platform = {
                "os": "windows" if windows else "linux",
                "architecture": build["matrix"].get("architecture", "amd64"),
            }
            if windows:
                platform["os.version"] = "10.0." + (
                    "17763.1000"
                    if build["matrix"]["release"] == "ltsc2019"
                    else "20348.1000"
                )
                platform["os.features"] = ["win32k"]
            descriptor = {
                "digest": digest,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "size": 100,
                "platform": platform,
            }
            entries = [descriptor]
            if not windows:
                entries.append(
                    {
                        **descriptor,
                        "digest": "sha256:" + format(number + 100, "064x"),
                        "platform": {"os": "unknown", "architecture": "unknown"},
                        "annotations": {
                            "vnd.docker.reference.type": "attestation-manifest",
                            "vnd.docker.reference.digest": digest,
                        },
                    }
                )
            sources[build["id"]] = entries
            records[build["id"]] = {"digest": digest, "platform": platform}
            manifests[digest] = (
                {"digest": digest, "manifests": entries}
                if not windows
                else {k: v for k, v in descriptor.items() if k != "platform"},
                platform,
            )
        registry = MemoryRegistry("example/app")
        for digest, (manifest, platform) in manifests.items():
            registry.documents[digest] = manifest
            registry.configs[digest] = platform
        with patch.dict(os.environ, {}, clear=True):
            publish(plan, records, "example/app", registry)
        for publication in plan["publications"]:
            expected = [
                entry
                for build_id in publication["builds"]
                for entry in sources[build_id]
            ]
            primary = registry.tags[publication["tags"][0]]
            self.assertEqual(expected, primary["manifests"])
            for tag in publication["tags"]:
                self.assertEqual(primary, registry.tags[tag])
        self.assertEqual(14, len(plan["publications"]))
        self.assertTrue(
            all(
                tag.endswith("-aaaaaaaaaaaa-123")
                for tag in staging_tags(plan, "a" * 40, "123")
            )
        )


if __name__ == "__main__":
    unittest.main()
