import json
import os
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_windows import build_command, staging_tag
from cleanup import cleanup, staging_tags
from configuration_fixtures import example
from configure import make_plan
from publish import load_records, publish, source_descriptors, verify_manifest

DIGEST = "sha256:" + "1" * 64
OTHER = "sha256:" + "2" * 64
INDEX = "sha256:" + "3" * 64
MEDIA = "application/vnd.oci.image.manifest.v1+json"


def descriptor(digest=DIGEST, os_name="linux", architecture="amd64", version=None):
    platform = {"os": os_name, "architecture": architecture}
    if version:
        platform["os.version"] = version
    return {"mediaType": MEDIA, "digest": digest, "size": 500, "platform": platform}


class PublicationTests(unittest.TestCase):
    def test_target_tags_preserve_only_their_image_and_attestations(self):
        config = example()
        config["targets"] = [config["targets"][0], config["targets"][2]]
        config["variants"] = [{"tag": "1.0", "aliases": ["latest"]}]
        plan = make_plan(config)
        linux = [descriptor(), descriptor(OTHER, "unknown", "unknown")]
        windows = [descriptor(INDEX, "windows", version="10.0.17763.1000")]
        sources = {"b000": linux, "b001": windows}
        published = {}
        preflight_count = 0

        def read_files(args):
            return [
                json.loads(Path(args[index + 1]).read_text())
                for index, arg in enumerate(args)
                if arg == "--file"
            ]

        def inspect(*args):
            nonlocal preflight_count
            if args[0] == "create":
                preflight_count += 1
                return {"manifests": read_files(args)}
            return {"digest": INDEX, "manifests": published[args[1]]}

        def run(command, **kwargs):
            self.assertEqual(3, preflight_count)
            for index, arg in enumerate(command):
                if arg == "--tag":
                    published[command[index + 1]] = read_files(command)
            Path(command[-1]).write_text(
                json.dumps({"containerimage.descriptor": {"digest": INDEX}})
            )

        with (
            patch(
                "publish.source_descriptors",
                side_effect=lambda image, build, record, inspect: sources[build["id"]],
            ),
            patch("publish.subprocess.run", side_effect=run),
            patch.dict(os.environ, {}, clear=True),
        ):
            publish(plan, {"b000": {}, "b001": {}}, "example/app", inspect)
        self.assertEqual(linux + windows, published["example/app:1.0"])
        self.assertEqual(linux + windows, published["example/app:latest"])
        self.assertEqual(linux, published["example/app:1.0-linux-amd64"])
        self.assertEqual(linux, published["example/app:latest-linux-amd64"])
        self.assertEqual(windows, published["example/app:1.0-windows-2019"])
        self.assertEqual(windows, published["example/app:latest-windows-2019"])

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

    def test_preflight_failure_never_updates_public_tags(self):
        plan = {
            "builds": [
                {"id": "b000"},
                {"id": "b001"},
            ],
            "publications": [{"tags": ["latest"], "builds": ["b000", "b001"]}],
        }
        records = {
            "b000": {
                "digest": INDEX,
                "platform": {"os": "linux", "architecture": "amd64"},
            },
            "b001": {"digest": "invalid"},
        }
        with patch("publish.subprocess.run") as run, self.assertRaises(ValueError):
            publish(
                plan,
                records,
                "faulo/example",
                lambda *args: {"digest": INDEX, "manifests": [descriptor()]},
            )
        run.assert_not_called()

    def test_ambiguous_windows_releases_never_update_public_tags(self):
        first = {
            "os": "windows",
            "architecture": "amd64",
            "os.version": "10.0.17763.1000",
        }
        second = {**first, "os.version": "10.0.17763.2000"}
        records = {
            "b000": {"digest": DIGEST, "platform": first},
            "b001": {"digest": OTHER, "platform": second},
        }
        plan = {
            "builds": [{"id": "b000"}, {"id": "b001"}],
            "publications": [{"tags": ["latest"], "builds": ["b000", "b001"]}],
        }

        def inspect(*args):
            digest = args[1].split("@")[1]
            if args[-1] == "{{json .Image}}":
                return first if digest == DIGEST else second
            return {"digest": digest, "size": 500, "mediaType": MEDIA}

        with (
            patch("publish.subprocess.run") as run,
            self.assertRaisesRegex(ValueError, "Ambiguous image platforms"),
        ):
            publish(plan, records, "faulo/example", inspect)
        run.assert_not_called()

    def test_publish_aliases_from_same_digest(self):
        plan = {
            "builds": [{"id": "b000"}],
            "publications": [{"tags": ["8.5", "latest"], "builds": ["b000"]}],
        }
        commands = []

        def inspect(*args):
            if args[0] == "create":
                self.assertIn("--dry-run", args)
                self.assertIn("faulo/example:8.5", args)
                return {"manifests": [descriptor()]}
            return {"digest": INDEX, "manifests": [descriptor()]}

        def run(command, **kwargs):
            commands.append(command)
            Path(command[-1]).write_text(
                json.dumps(
                    {
                        "containerimage.descriptor": {
                            "digest": INDEX,
                            "platform": {"os": "linux", "architecture": "amd64"},
                        }
                    }
                )
            )

        with (
            patch("publish.subprocess.run", side_effect=run),
            patch.dict(os.environ, {}, clear=True),
        ):
            publish(
                plan,
                {
                    "b000": {
                        "digest": INDEX,
                        "platform": {"os": "linux", "architecture": "amd64"},
                    }
                },
                "faulo/example",
                inspect,
            )
        self.assertEqual(1, len(commands))
        self.assertIn("faulo/example:8.5", commands[0])
        self.assertIn("faulo/example:latest", commands[0])

    def test_wrong_alias_digest_fails_publication(self):
        plan = {
            "builds": [{"id": "b000"}],
            "publications": [{"tags": ["latest"], "builds": ["b000"]}],
        }

        def inspect(*args):
            return {
                "digest": OTHER if ":latest" in args[1] else INDEX,
                "manifests": [descriptor()],
            }

        def run(command, **kwargs):
            Path(command[-1]).write_text(
                json.dumps(
                    {
                        "containerimage.descriptor": {
                            "digest": INDEX,
                            "platform": {"os": "linux", "architecture": "amd64"},
                        }
                    }
                )
            )

        with (
            patch("publish.subprocess.run", side_effect=run),
            self.assertRaisesRegex(ValueError, "does not reference"),
        ):
            publish(
                plan,
                {
                    "b000": {
                        "digest": INDEX,
                        "platform": {"os": "linux", "architecture": "amd64"},
                    }
                },
                "faulo/example",
                inspect,
            )

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

    def test_cleanup_scope_and_reruns(self):
        plan = {
            "builds": [
                {"id": "b000"},
                {"id": "b001"},
            ]
        }
        self.assertEqual(
            [
                "wf-v3-123-1-b000",
                "wf-v3-123-2-b000",
                "wf-v3-123-1-b001",
                "wf-v3-123-2-b001",
            ],
            staging_tags(plan, "123", "2"),
        )
        with self.assertRaises(ValueError):
            staging_tag("b000", "../latest", "1")

    def test_cleanup_404_and_other_failures(self):
        plan = {
            "namespace": "organization",
            "image": "example",
            "builds": [{"id": "b000"}],
        }
        visited = []

        def request(item, **kwargs):
            visited.append(item.full_url)
            if item.get_method() == "POST":
                return BytesIO(b'{"access_token":"secret"}')
            raise HTTPError(
                item.full_url, 404 if "-1-" in item.full_url else 403, "error", {}, None
            )

        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            cleanup(plan, "faulo", "token", "123", "2", request)
        self.assertEqual(3, len(visited))
        self.assertIn("/namespaces/organization/", visited[-1])


if __name__ == "__main__":
    unittest.main()
