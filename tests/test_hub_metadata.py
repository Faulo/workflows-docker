import io
import json
import os
import subprocess
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from configuration_fixtures import example, matrix_example
from configure import make_plan
from hub_metadata import prepare_metadata, synchronize

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "main"
REPOSITORY = "caller/source"
BLOB = f"https://github.com/{REPOSITORY}/blob/{BRANCH}/"


class MetadataConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in ("linux/Dockerfile", "windows/Dockerfile", "docs/README.md"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "# Overview\n[Build](../linux/Dockerfile)\n", encoding="utf-8"
            )

    def test_independent_fields_and_omissions_for_both_schema_versions(self):
        for factory in (example, matrix_example):
            for fields in (
                {},
                {"description": "A container"},
                {"overviewFile": "docs/README.md"},
                {"description": "", "overviewFile": "docs/README.md"},
            ):
                with self.subTest(version=factory()["schemaVersion"], fields=fields):
                    plan = make_plan(factory() | fields, self.root, environment={})
                    self.assertEqual(fields, {k: plan[k] for k in fields})
                    metadata = prepare_metadata(plan, self.root, REPOSITORY, BRANCH)
                    self.assertEqual("description" in fields, "description" in metadata)
                    self.assertEqual(
                        "overviewFile" in fields, "full_description" in metadata
                    )
                    if "overviewFile" in fields:
                        self.assertIn(
                            BLOB + "linux/Dockerfile", metadata["full_description"]
                        )
                    for build in plan["builds"]:
                        self.assertNotIn("description", build)
                        self.assertNotIn("overviewFile", build)

    def test_invalid_fields_fail_before_builds(self):
        cases = [
            ({"description": None}, "description must be a string"),
            ({"description": 42}, "description must be a string"),
            ({"description": "a\nb"}, "single-line"),
            ({"description": "x" * 101}, "100"),
            ({"description": "é" * 51}, "102 UTF-8 bytes"),
            ({"overviewFile": None}, "overviewFile"),
            ({"overviewFile": []}, "overviewFile"),
            ({"overviewFile": "missing.md"}, "overviewFile does not exist"),
            ({"overviewFile": "docs"}, "overviewFile does not exist"),
            ({"overviewFile": "../README.md"}, "repository-relative"),
            ({"overviewFile": "/README.md"}, "repository-relative"),
            ({"overviewFile": "C:/README.md"}, "repository-relative"),
        ]
        for factory in (example, matrix_example):
            for fields, error in cases:
                with (
                    self.subTest(fields=fields),
                    self.assertRaisesRegex(ValueError, error),
                ):
                    make_plan(factory() | fields, self.root, environment={})

    def test_invalid_encoding_and_overview_limit(self):
        path = self.root / "docs/README.md"
        for content, error in ((b"\xff", "UTF-8"), (("é" * 12501).encode(), "25000")):
            path.write_bytes(content)
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                make_plan(
                    matrix_example() | {"overviewFile": "docs/README.md"}, self.root
                )

    def test_limit_is_checked_again_after_link_expansion(self):
        path = self.root / "docs/README.md"
        path.write_text("x" * 24950 + "[a](a.md)", encoding="utf-8")
        plan = make_plan(
            matrix_example() | {"overviewFile": "docs/README.md"}, self.root
        )
        with self.assertRaisesRegex(ValueError, "after URL resolution"):
            prepare_metadata(plan, self.root, REPOSITORY, BRANCH)

    def test_exact_limits_and_explicit_empty_values(self):
        path = self.root / "docs/README.md"
        for overview in ("", "é" * 12500):
            path.write_text(overview, encoding="utf-8")
            plan = make_plan(
                matrix_example()
                | {"description": "é" * 50, "overviewFile": "docs/README.md"},
                self.root,
            )
            self.assertEqual(
                overview,
                prepare_metadata(plan, self.root, REPOSITORY, BRANCH)[
                    "full_description"
                ],
            )

    def test_metadata_is_only_allowed_at_root(self):
        for field in ("description", "overviewFile"):
            for position in ("axis", "choice"):
                config = matrix_example()
                axis = config["matrix"]["version"]
                (axis if position == "axis" else axis["tags"]["1.0"])[field] = (
                    "README.md"
                )
                with (
                    self.subTest(field=field, position=position),
                    self.assertRaisesRegex(ValueError, "Unknown properties"),
                ):
                    make_plan(config)

    def test_cli_snapshots_caller_root_file_and_default_branch(self):
        config = matrix_example() | {
            "overviewFile": "docs/README.md",
            "description": "$SHORT",
        }
        (self.root / "nested").mkdir()
        (self.root / "nested/docker.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        output = self.root / "plan/plan.json"
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/configure.py"),
                "--config",
                "nested/docker.json",
                "--output",
                str(output),
            ],
            cwd=self.root,
            env=os.environ
            | {
                "GITHUB_REPOSITORY": REPOSITORY,
                "GITHUB_DEFAULT_BRANCH": BRANCH,
                "SHORT": "Snapshot",
            },
            capture_output=True,
            text=True,
            check=True,
        )
        snapshot = json.loads(output.with_name("metadata.json").read_text())
        self.assertEqual("Snapshot", snapshot["description"])
        self.assertIn(BLOB + "linux/Dockerfile", snapshot["full_description"])
        (self.root / "docs/README.md").write_text("Changed after configuration")
        self.assertEqual(
            snapshot, json.loads(output.with_name("metadata.json").read_text())
        )

    def test_schema_accepts_fields_and_rejects_invalid_types_and_nested_fields(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("optional jsonschema package is not installed")
        schema = json.loads((ROOT / "docker.schema.json").read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        validator = jsonschema.Draft202012Validator(schema)
        for factory in (example, matrix_example):
            validator.validate(
                factory() | {"description": "", "overviewFile": "docs/README.md"}
            )
            for fields in (
                {"description": None},
                {"description": "x" * 101},
                {"overviewFile": 42},
            ):
                self.assertFalse(validator.is_valid(factory() | fields))
        config = matrix_example()
        config["matrix"]["version"]["description"] = "invalid"
        self.assertFalse(validator.is_valid(config))


class SynchronizationTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"namespace": "destination-org", "image": "configured-image"}

    def test_one_patch_with_only_configured_fields_and_existing_credentials(self):
        for metadata in (
            {"description": "Short"},
            {"full_description": "# Overview"},
            {"description": "", "full_description": ""},
        ):
            request = Mock(
                side_effect=[
                    io.BytesIO(b'{"access_token":"private-bearer"}'),
                    io.BytesIO(b"{}"),
                ]
            )
            synchronize(
                self.plan, metadata, "login-user", "private-pat", request=request
            )
            self.assertEqual(2, request.call_count)
            auth, update = [call.args[0] for call in request.call_args_list]
            self.assertEqual(
                {"identifier": "login-user", "secret": "private-pat"},
                json.loads(auth.data),
            )
            self.assertEqual("PATCH", update.method)
            self.assertEqual(
                "https://hub.docker.com/v2/repositories/destination-org/configured-image/",
                update.full_url,
            )
            self.assertEqual(metadata, json.loads(update.data))
            self.assertEqual(
                "Bearer private-bearer", update.get_header("Authorization")
            )

    def test_omitted_fields_and_disabled_publication_need_no_credentials_or_requests(
        self,
    ):
        request = Mock(side_effect=AssertionError("Unexpected network request"))
        synchronize(self.plan, {}, request=request)
        synchronize(
            self.plan,
            {"description": "Short", "full_description": "Overview"},
            publish=False,
            request=request,
        )
        request.assert_not_called()

    def test_missing_credentials_fail_before_request(self):
        request = Mock()
        with self.assertRaisesRegex(
            ValueError, "DOCKERHUB_USERNAME and DOCKERHUB_TOKEN"
        ):
            synchronize(self.plan, {"description": "Short"}, request=request)
        request.assert_not_called()

    def test_failures_identify_stage_without_exposing_secrets(self):
        for stage in ("authentication", "repository update"):
            for failure in (
                HTTPError(
                    "https://example.com/private-pat",
                    403,
                    "private-pat",
                    {},
                    io.BytesIO(b"private-bearer"),
                ),
                URLError("private-pat"),
            ):
                responses = (
                    [failure]
                    if stage == "authentication"
                    else [io.BytesIO(b'{"access_token":"private-bearer"}'), failure]
                )
                with (
                    self.subTest(stage=stage),
                    self.assertRaisesRegex(RuntimeError, stage) as caught,
                ):
                    synchronize(
                        self.plan,
                        {"description": "Short"},
                        "login-user",
                        "private-pat",
                        request=Mock(side_effect=responses),
                    )
                rendered = "".join(traceback.format_exception(caught.exception))
                self.assertNotIn("private-pat", rendered)
                self.assertNotIn("private-bearer", rendered)

    def test_malformed_authentication_response_fails_without_patch(self):
        for body in (b"not-json", b"{}", b'{"access_token":null}'):
            request = Mock(return_value=io.BytesIO(body))
            with self.assertRaisesRegex(RuntimeError, "authentication"):
                synchronize(
                    self.plan,
                    {"description": "Short"},
                    "user",
                    "token",
                    request=request,
                )
            self.assertEqual(1, request.call_count)

    def test_workflow_updates_once_after_successful_publication_and_keeps_cleanup(self):
        workflow = (ROOT / ".github/workflows/docker.yml").read_text()
        publication = workflow.split("\n  publication:")[1]
        self.assertIn(
            "inputs.publish && needs.configuration.result == 'success'", publication
        )
        self.assertLess(
            publication.index("Publish and verify public tags"),
            publication.index("Synchronize Docker Hub descriptions"),
        )
        self.assertLess(
            publication.index("Synchronize Docker Hub descriptions"),
            publication.index("Delete Windows staging tags"),
        )
        self.assertEqual(
            1, workflow.count("run: python workflow/scripts/hub_metadata.py")
        )
        cleanup = publication.split("- name: Delete Windows staging tags")[1]
        self.assertIn("if: always()", cleanup)


if __name__ == "__main__":
    unittest.main()
