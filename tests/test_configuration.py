import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from configure import make_plan, read_env, relative_path, resolve_config, substitute
from detect_platform import native_platform

ROOT = Path(__file__).resolve().parents[1]


def example(name="docker-farah"):
    config = json.loads(
        (ROOT / "examples" / "fleet" / f"{name}.json").read_text(encoding="utf-8")
    )
    return {**config, "namespace": "faulo", "image": name.removeprefix("docker-")}


class ConfigurationTests(unittest.TestCase):
    def test_all_fleet_plans(self):
        expected = {"choco": 4, "farah": 21, "unreal": 1}
        for path in (ROOT / "examples" / "fleet").glob("*.json"):
            with self.subTest(project=path.stem):
                config = json.loads(path.read_text(encoding="utf-8"))
                plan = make_plan(
                    config,
                    environment={
                        "DOCKER_NAMESPACE": "faulo",
                        "DOCKER_IMAGE": path.stem.removeprefix("docker-"),
                    },
                )
                self.assertEqual(expected.get(plan["image"], 2), len(plan["builds"]))
                self.assertEqual(
                    f"faulo/{path.stem.removeprefix('docker-')}", plan["reference"]
                )
                self.assertEqual(
                    1,
                    sum(
                        "latest" in publication["tags"]
                        for publication in plan["publications"]
                    ),
                )

    def test_farah_platforms_and_latest(self):
        plan = make_plan(example())
        self.assertEqual(["8.5", "latest"], plan["publications"][-1]["tags"])
        self.assertEqual(
            {"ubuntu-latest", "ubuntu-24.04-arm", "windows-latest"},
            {build["runner"] for build in plan["builds"]},
        )
        self.assertEqual("8.0", plan["builds"][3]["buildArgs"]["PHP_VERSION"])
        self.assertEqual("ubuntu-24.04-arm", plan["builds"][1]["runner"])

    def test_choco_families_are_separate_publications(self):
        plan = make_plan(example("docker-choco"))
        self.assertEqual(
            [["windows", "latest"], ["windowsservercore"]],
            [publication["tags"] for publication in plan["publications"]],
        )
        self.assertTrue(
            all(build["runner"] == "windows-latest" for build in plan["builds"])
        )
        self.assertTrue(
            all(
                "platform" not in build and "osVersion" not in build
                for build in plan["builds"]
            )
        )
        self.assertIn(
            "windows/server:ltsc2022", plan["builds"][1]["buildArgs"]["BASE_IMAGE"]
        )

    def test_windows_only(self):
        plan = make_plan(example("docker-unreal"))
        self.assertTrue(
            all(build["runner"] == "windows-latest" for build in plan["builds"])
        )

    def test_linux_only_and_overrides(self):
        config = example()
        config["targets"] = config["targets"][:1]
        config["buildArgs"] = {"SHARED": "global", "COUNT": 2}
        config["variants"] = [{"tag": "latest", "buildArgs": {"SHARED": "variant"}}]
        config["targets"][0].update(
            {
                "buildArgs": {"SHARED": "target", "FLAG": True},
                "runner": ["self-hosted", "linux", "x64"],
                "timeoutMinutes": 42,
            }
        )
        build = make_plan(config)["builds"][0]
        self.assertEqual(
            {"SHARED": "target", "COUNT": "2", "FLAG": "true"}, build["buildArgs"]
        )
        self.assertEqual(42, build["timeoutMinutes"])
        self.assertEqual(["self-hosted", "linux", "x64"], build["runner"])

    def test_duplicate_alias_rejected(self):
        config = example()
        config["variants"][0]["aliases"] = ["latest"]
        with self.assertRaisesRegex(ValueError, "Duplicate public tag"):
            make_plan(config)

    def test_duplicate_target_ids_rejected(self):
        config = example("docker-choco")
        config["variants"][0]["targets"].append(
            copy.deepcopy(config["variants"][0]["targets"][0])
        )
        with self.assertRaisesRegex(ValueError, "Duplicate target id"):
            make_plan(config)

    def test_invalid_configuration_rejected(self):
        mutations = [
            lambda c: c.update(schemaVersion=2),
            lambda c: c.update(schemaVersion=True),
            lambda c: c.update(image="namespace/image"),
            lambda c: c.update(namespace="invalid/namespace"),
            lambda c: c.update(tests={}),
            lambda c: c.update(targets=[]),
            lambda c: c["variants"][0].update(tag="wf-v2-private"),
            lambda c: c["variants"][0].update(tag="invalid tag"),
            lambda c: c["targets"][0].update(platform="windows/arm64"),
            lambda c: c["targets"][0].update(timeoutMinutes=0),
            lambda c: c["targets"][0].update(dockerfile="../Dockerfile"),
            lambda c: c["targets"][0].update(osVersion="10.0.17763"),
            lambda c: c["targets"][0].update(buildArgs={"BAD-NAME": "value"}),
            lambda c: c["targets"][0].update(buildArgs={"VALUE": "injected\nARG=1"}),
            lambda c: c["targets"][0].update(buildArgs={"VALUE": {}}),
            lambda c: c["targets"][0].update(buildArgs={"VALUE": float("nan")}),
            lambda c: c["targets"][2].pop("runner"),
        ]
        for mutation in mutations:
            config = example()
            mutation(config)
            with self.subTest(configuration=config), self.assertRaises(ValueError):
                make_plan(config)

    def test_missing_context_or_dockerfile_rejected(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(ValueError, "does not exist"),
        ):
            make_plan(example(), Path(directory))

    def test_config_path_traversal_rejected(self):
        for path in (
            "../docker.json",
            "/docker.json",
            "C:/docker.json",
            "folder\\docker.json",
            "--config",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                relative_path(path, "config")

    def test_matrix_limit(self):
        config = example()
        config["targets"] = [
            {**config["targets"][0], "id": f"target-{number}"} for number in range(257)
        ]
        config["variants"] = [{"tag": "latest"}]
        with self.assertRaisesRegex(ValueError, "256 matrix"):
            make_plan(config)

    def test_variants_share_target_jobs_without_losing_arguments(self):
        plan = make_plan(example())
        self.assertEqual(3, len(plan["groups"]))
        self.assertTrue(all(len(group["builds"]) == 7 for group in plan["groups"]))
        self.assertEqual(21, sum(len(group["builds"]) for group in plan["groups"]))
        choco = make_plan(example("docker-choco"))
        self.assertEqual(2, len(choco["groups"]))
        self.assertEqual(["b000", "b002"], choco["groups"][0]["builds"])
        self.assertNotEqual(
            choco["builds"][0]["buildArgs"]["BASE_IMAGE"],
            choco["builds"][2]["buildArgs"]["BASE_IMAGE"],
        )

    def test_same_target_rejects_inconsistent_runner_or_timeout(self):
        for override in ({"runner": "windows-2022"}, {"timeoutMinutes": 60}):
            config = example("docker-choco")
            config["variants"][1]["targets"][0].update(override)
            with (
                self.subTest(override=override),
                self.assertRaisesRegex(ValueError, "same runner and timeout"),
            ):
                make_plan(config)

    def test_substitution_is_single_pass_and_preserves_json_values(self):
        source = {"image": "$IMAGE", "args": ["${VALUE}", "$$literal", 42, True]}
        self.assertEqual(
            {"image": 'a"b', "args": ["$OTHER", "$literal", 42, True]},
            substitute(source, {"IMAGE": 'a"b', "VALUE": "$OTHER"}),
        )
        with self.assertRaisesRegex(
            ValueError, "Undefined configuration variable: MISSING"
        ):
            substitute("$MISSING", {})

    def test_dotenv_quotes_comments_and_literal_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "# comment\nexport DOCKER_NAMESPACE=faulo\nDOCKER_IMAGE='choco' # comment\nVALUE=literal # comment\nCOMMAND=$(touch /tmp/unwanted)\n",
                encoding="utf-8",
            )
            values = read_env(path)
            self.assertEqual("choco", values["DOCKER_IMAGE"])
            self.assertEqual("literal", values["VALUE"])
            self.assertEqual("$(touch /tmp/unwanted)", values["COMMAND"])
            path.write_text(
                'ARGS="-v \\"volume:c:/program files/tool\\""\n', encoding="utf-8"
            )
            self.assertEqual(
                '-v "volume:c:/program files/tool"', read_env(path)["ARGS"]
            )

    def test_dotenv_override_and_custom_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "settings.env").write_text(
                "DOCKER_NAMESPACE=file\nDOCKER_IMAGE=choco\n"
            )
            config = {
                "envFile": "settings.env",
                "namespace": "$DOCKER_NAMESPACE",
                "image": "$DOCKER_IMAGE",
            }
            resolved = resolve_config(config, root, {"DOCKER_NAMESPACE": "process"})
            self.assertEqual("process", resolved["namespace"])
            self.assertEqual("choco", resolved["image"])
            with self.assertRaisesRegex(ValueError, "does not exist"):
                resolve_config({"envFile": "missing.env"}, root, {})

    def test_invalid_dotenv_rejected(self):
        for value in ("not an assignment", "VALUE='unfinished", "VALUE=one\nVALUE=two"):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / ".env"
                path.write_text(value)
                with self.subTest(value=value), self.assertRaises(ValueError):
                    read_env(path)

    def test_custom_runner_is_not_interpreted(self):
        config = example("docker-unreal")
        config["targets"][0]["runner"] = "arbitrary-build-host"
        self.assertEqual(
            "arbitrary-build-host", make_plan(config)["builds"][0]["runner"]
        )

    def test_native_platform_detection(self):
        self.assertEqual(
            "linux/arm64",
            native_platform(
                "Linux", "ARM64", {"OSType": "linux", "Architecture": "aarch64"}
            ),
        )
        self.assertEqual(
            "windows/amd64",
            native_platform(
                "Windows", "X64", {"OSType": "windows", "Architecture": "x86_64"}
            ),
        )
        with self.assertRaisesRegex(ValueError, "architecture differs"):
            native_platform(
                "Linux", "ARM64", {"OSType": "linux", "Architecture": "amd64"}
            )
        with self.assertRaisesRegex(ValueError, "daemon OS differs"):
            native_platform(
                "Windows", "X64", {"OSType": "linux", "Architecture": "amd64"}
            )
        with self.assertRaises(ValueError):
            native_platform("macOS", "X64", {})


if __name__ == "__main__":
    unittest.main()
