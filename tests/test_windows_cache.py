import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from windows_cache import (
    eligible_cache,
    expand,
    final_base,
    inspect_manifest,
    native_manifest,
)

PLATFORM = "windows/amd64"
CACHE = "example/app:latest-windows-ltsc2019"
DIGEST = "sha256:" + "a" * 64


def metadata(layers, os_name="windows", architecture="amd64"):
    return {
        "Descriptor": {
            "digest": DIGEST,
            "platform": {"os": os_name, "architecture": architecture},
        },
        "SchemaV2Manifest": {
            "layers": [{"digest": "sha256:" + value * 64} for value in layers],
        },
    }


class WindowsCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "Dockerfile"
        self.entry = {
            "dockerfile": str(self.path),
            "buildArgs": {},
            "platform": PLATFORM,
        }

    def base(self, text, arguments=None):
        self.path.write_text(text, encoding="utf-8")
        return final_base(self.path, arguments or {}, PLATFORM)

    def test_effective_global_arguments_and_stage_aliases(self):
        self.assertEqual(
            "mcr.microsoft.com/windows:ltsc2019",
            self.base(
                "ARG ROOT=mcr.microsoft.com/windows\nARG RELEASE=ltsc2022\n"
                "FROM unrelated/sdk:new AS compiler\n"
                "ARG RELEASE=ignored\n"
                "FROM ${ROOT}:$RELEASE AS runtime\n"
                "COPY --from=compiler /out /out\nFROM runtime AS final\n",
                {"RELEASE": "ltsc2019", "ROOT": "mcr.microsoft.com/windows"},
            ),
        )

    def test_continuations_comments_and_windows_escape_directive(self):
        for escape in ("\\", "`"):
            with self.subTest(escape=escape):
                self.assertEqual(
                    "example/base:latest",
                    self.base(
                        f'# escape={escape}\nARG BASE="example/base:latest"\n'
                        f"FROM --platform=windows/amd64 {escape}\n"
                        "# ignored comment\n ${BASE} AS base\nFROM BASE\n"
                    ),
                )

    def test_common_parameter_defaults_and_no_recursive_expansion(self):
        self.assertEqual("fallback", expand("${MISSING:-fallback}", {}))
        self.assertEqual("fallback", expand("${EMPTY:-fallback}", {"EMPTY": ""}))
        self.assertEqual("", expand("${EMPTY-fallback}", {"EMPTY": ""}))
        self.assertEqual("yes", expand("${SET:+yes}", {"SET": "value"}))
        self.assertEqual("$OTHER", expand("$SET", {"SET": "$OTHER"}))

    def test_unsupported_or_unresolved_input_skips_cache(self):
        for text in (
            "FROM $MISSING\n",
            "FROM ${BASE#prefix}\n",
            "FROM scratch\n",
            "FROM --platform=linux/amd64 example/base\n",
            "RUN echo no-base\n",
            "ARG BASE\nFROM $BASE\n",
            "FROM example/base AS a\nFROM example/other AS a\n",
            "ARG BASE='$OTHER'\nFROM $BASE\n",
        ):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                inspect = Mock()
                self.assertIsNone(eligible_cache(self.entry, CACHE, inspect))
                inspect.assert_not_called()

    def test_complete_ordered_prefix_is_required(self):
        self.path.write_text("FROM example/base:latest\n", encoding="utf-8")
        for layers, expected in (
            ("1234", True),
            ("12", True),
            ("1", False),
            ("132", False),
            ("312", False),
            ("134", False),
        ):
            with self.subTest(layers=layers):
                inspect = Mock(side_effect=[metadata("12"), metadata(layers)])
                result = eligible_cache(self.entry, CACHE, inspect)
                self.assertEqual(f"{CACHE}@{DIGEST}" if expected else None, result)
                self.assertEqual(
                    ["example/base:latest", CACHE],
                    [c.args[0] for c in inspect.call_args_list],
                )

    def test_multi_platform_publication_selects_only_native_image(self):
        selected = metadata("123")
        self.assertEqual(
            (DIGEST, ["sha256:" + value * 64 for value in "123"]),
            native_manifest([metadata("9", "linux"), selected], PLATFORM),
        )
        for candidate in (
            [selected, selected],
            metadata("1", "linux"),
            {},
            metadata(""),
        ):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                native_manifest(candidate, PLATFORM)

    def test_metadata_errors_disable_cache_without_failing_build(self):
        self.path.write_text("FROM example/base\n", encoding="utf-8")
        for failure in (
            subprocess.TimeoutExpired("docker", 30),
            subprocess.CalledProcessError(1, "docker"),
            ValueError("invalid JSON"),
            OSError("missing CLI"),
        ):
            with self.subTest(failure=failure):
                self.assertIsNone(
                    eligible_cache(self.entry, CACHE, Mock(side_effect=failure))
                )

    def test_manifest_probe_captures_output_and_has_timeout(self):
        with patch(
            "windows_cache.subprocess.run",
            return_value=SimpleNamespace(stdout=json.dumps(metadata("12"))),
        ) as run:
            self.assertEqual(metadata("12"), inspect_manifest("example/base"))
        self.assertIn("manifest", run.call_args.args[0])
        self.assertNotIn("pull", run.call_args.args[0])
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual(30, run.call_args.kwargs["timeout"])
