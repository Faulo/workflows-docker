import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_windows import build_command
from configuration_fixtures import example
from configure import make_plan
from publish import load_records, publish, verify_manifest
from publish import source_descriptors as read_sources
from registry import DOCKER_INDEX, content_digest
from registry_fixtures import MemoryRegistry

DIGEST = "sha256:" + "1" * 64
OTHER = "sha256:" + "2" * 64
INDEX = "sha256:" + "3" * 64
MEDIA = "application/vnd.oci.image.manifest.v1+json"


def descriptor(digest=DIGEST, os_name="linux", architecture="amd64", version=None):
    platform = {"os": os_name, "architecture": architecture}
    if version:
        platform["os.version"] = version
    return {"mediaType": MEDIA, "digest": digest, "size": 500, "platform": platform}


def source_descriptors(image, build, record, inspect):
    class Reader:
        def manifest(self, reference):
            return inspect("inspect", reference, "--format", "{{json .Manifest}}")

        def config(self, reference):
            return inspect("inspect", reference, "--format", "{{json .Image}}")

    return read_sources(image, build, record, Reader())


class PublicationTests(unittest.TestCase):
    def test_exact_manifest_including_attestations(self):
        image = descriptor()
        attestation = descriptor(OTHER, "unknown", "unknown")
        verify_manifest({"manifests": [attestation, image]}, [image, attestation])
        for invalid in ([image], [image, image], [descriptor(OTHER), attestation]):
            with self.subTest(manifests=invalid), self.assertRaises(ValueError):
                verify_manifest({"manifests": invalid}, [image, attestation])

    def test_windows_os_version_verification(self):
        expected = [descriptor(os_name="windows", version="10.0.17763.1234")]
        wrong = [descriptor(os_name="windows", version="10.0.20348.1234")]
        with self.assertRaises(ValueError):
            verify_manifest({"manifests": wrong}, expected)

    def test_linux_source_preserves_attestations(self):
        image = descriptor()
        attestation = descriptor(OTHER, "unknown", "unknown")
        attestation["annotations"] = {
            "vnd.docker.reference.type": "attestation-manifest",
            "vnd.docker.reference.digest": DIGEST,
        }
        manifest = {"digest": INDEX, "manifests": [image, attestation]}
        result = source_descriptors(
            "faulo/example",
            {"id": "b000"},
            {"digest": INDEX, "platform": {"os": "linux", "architecture": "amd64"}},
            lambda *args: manifest,
        )
        self.assertEqual([image, attestation], result)

    def test_unexpected_linux_platform_rejected(self):
        manifest = {"digest": INDEX, "manifests": [descriptor(architecture="arm64")]}
        with self.assertRaisesRegex(ValueError, "unexpected platforms"):
            source_descriptors(
                "faulo/example",
                {"id": "b000"},
                {"digest": INDEX, "platform": {"os": "linux", "architecture": "amd64"}},
                lambda *args: manifest,
            )

    def test_wrong_attestation_reference_rejected(self):
        item = descriptor(OTHER, "unknown", "unknown")
        item["annotations"] = {
            "vnd.docker.reference.type": "attestation-manifest",
            "vnd.docker.reference.digest": OTHER,
        }
        with self.assertRaisesRegex(ValueError, "unexpected non-image"):
            source_descriptors(
                "faulo/example",
                {"id": "b000"},
                {"digest": INDEX, "platform": {"os": "linux", "architecture": "amd64"}},
                lambda *args: {"digest": INDEX, "manifests": [descriptor(), item]},
            )

    def test_windows_source_metadata_from_registry(self):
        build = {"id": "b000"}
        config = {
            "os": "windows",
            "architecture": "amd64",
            "os.version": "10.0.17763.1234",
            "os.features": ["win32k"],
        }

        def inspect(*args):
            return (
                config
                if args[-1] == "{{json .Image}}"
                else {"digest": DIGEST, "size": 500, "mediaType": MEDIA}
            )

        result = source_descriptors(
            "faulo/example", build, {"digest": DIGEST, "platform": config}, inspect
        )
        self.assertEqual(config, result[0]["platform"])
        with self.assertRaisesRegex(
            ValueError, "local and registry OS versions differ"
        ):
            source_descriptors(
                "faulo/example",
                build,
                {
                    "digest": DIGEST,
                    "platform": {**config, "os.version": "10.0.20348.1234"},
                },
                inspect,
            )

    def test_missing_and_duplicate_records_rejected(self):
        plan = {"builds": [{"id": "b000"}, {"id": "b001"}]}
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "b000.json"
            first.write_text(json.dumps({"id": "b000", "digest": DIGEST}))
            with self.assertRaisesRegex(ValueError, "Missing build records"):
                load_records(plan, directory)
            (Path(directory) / "duplicate.json").write_text(first.read_text())
            with self.assertRaisesRegex(ValueError, "duplicate build record"):
                load_records(plan, directory)

    def test_windows_arguments_remain_literal(self):
        entry = {
            "platform": "windows/amd64",
            "dockerfile": "windows/Dockerfile",
            "context": ".",
            "buildArgs": {"ARG": "value; Write-Host $SECRET"},
        }
        command = build_command(entry, "faulo/example:stage", "faulo/example:latest")
        self.assertIn("ARG=value; Write-Host $SECRET", command)
        self.assertEqual(".", command[-1])

    def fixture(self):
        config = example()
        config["targets"] = [config["targets"][0], config["targets"][2]]
        config["variants"] = [{"tag": "1.0", "aliases": ["latest"]}]
        plan = make_plan(config)
        linux = [descriptor(), descriptor(OTHER, "unknown", "unknown")]
        linux[1]["annotations"] = {
            "vnd.docker.reference.type": "attestation-manifest",
            "vnd.docker.reference.digest": DIGEST,
        }
        windows = descriptor(INDEX, "windows", version="10.0.17763.1000")
        records = {
            "b000": {"digest": DIGEST, "platform": linux[0]["platform"]},
            "b001": {"digest": INDEX, "platform": windows["platform"]},
        }
        registry = MemoryRegistry("example/app")
        registry.documents[DIGEST] = {"digest": DIGEST, "manifests": linux}
        registry.documents[INDEX] = {
            k: v for k, v in windows.items() if k != "platform"
        }
        registry.configs[INDEX] = windows["platform"]
        return plan, records, registry, linux, [windows]

    def test_exact_descendants_and_aliases_without_recursive_copy(self):
        plan, records, registry, linux, windows = self.fixture()
        with patch.dict(os.environ, {}, clear=True):
            publish(plan, records, "example/app", registry)
        self.assertEqual(3, len(registry.reads_before_first_put))
        for tag, expected in (
            ("1.0", linux + windows),
            ("latest", linux + windows),
            ("1.0-linux-amd64", linux),
            ("latest-linux-amd64", linux),
            ("1.0-windows-2019", windows),
            ("latest-windows-2019", windows),
        ):
            self.assertEqual(expected, registry.tags[tag]["manifests"])
        for publication in plan["publications"]:
            payloads = [registry.payloads[tag] for tag in publication["tags"]]
            self.assertTrue(all(payload == payloads[0] for payload in payloads))
            self.assertEqual(
                content_digest(payloads[0]),
                registry.tags[publication["tags"][0]]["digest"],
            )

    def test_all_sources_and_all_products_checked_before_any_put(self):
        plan, records, registry, _, _ = self.fixture()
        records["b001"]["digest"] = "invalid"
        with self.assertRaises(ValueError):
            publish(plan, records, "example/app", registry)
        self.assertEqual({}, registry.payloads)
        plan, records, registry, _, _ = self.fixture()
        # A later ambiguous product must fail before an earlier valid one moves.
        plan["publications"].append({"tags": ["bad"], "builds": ["b001", "b001"]})
        with self.assertRaisesRegex(ValueError, "Ambiguous image platforms"):
            publish(plan, records, "example/app", registry)
        self.assertEqual({}, registry.payloads)

    def test_windows_docker_manifest_keeps_docker_index_media_type(self):
        plan, records, registry, _, _ = self.fixture()
        registry.documents[INDEX]["mediaType"] = (
            "application/vnd.docker.distribution.manifest.v2+json"
        )
        with patch.dict(os.environ, {}, clear=True):
            publish(plan, records, "example/app", registry)
        self.assertEqual(DOCKER_INDEX, registry.tags["1.0-windows-2019"]["mediaType"])

    def test_wrong_alias_digest_fails_publication(self):
        plan, records, registry, _, _ = self.fixture()
        original = registry.manifest

        def wrong(reference):
            result = original(reference)
            return (
                {**result, "digest": OTHER} if reference.endswith(":latest") else result
            )

        registry.manifest = wrong
        with self.assertRaisesRegex(
            ValueError, "does not reference the published digest"
        ):
            publish(plan, records, "example/app", registry)

    def test_manifest_metadata_changes_fail_verification(self):
        plan, records, registry, _, _ = self.fixture()
        original = registry.manifest

        def wrong(reference):
            result = original(reference)
            if ":" in reference and "@" not in reference:
                return {**result, "manifests": result["manifests"][:-1]}
            return result

        registry.manifest = wrong
        with self.assertRaisesRegex(
            ValueError, "expected digests or platform metadata"
        ):
            publish(plan, records, "example/app", registry)


if __name__ == "__main__":
    unittest.main()
