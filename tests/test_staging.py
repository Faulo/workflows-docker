import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cleanup import cleanup
from staging import staging_tag, staging_tags

SHA = "27adeb663eb6e16d3b676142c892952459b5445b"


class StagingTests(unittest.TestCase):
    def plan(self):
        return {
            "namespace": "organization",
            "image": "example",
            "builds": [{"tag": "8.2-windows-ltsc2019"}, {"tag": "8.2-linux-amd64"}],
            "publications": [{"tags": ["8.2", "latest"]}],
        }

    def test_readable_name_and_run_isolation(self):
        self.assertEqual(
            "8.2-windows-ltsc2019-27adeb663eb6-39495899061",
            staging_tag("8.2-windows-ltsc2019", SHA, "39495899061"),
        )
        self.assertNotEqual(
            staging_tag("latest", SHA, "123"), staging_tag("latest", SHA, "124")
        )

    def test_long_tags_remain_distinct_and_fit(self):
        first = staging_tag("a" * 127 + "b", SHA, "39495899061")
        second = staging_tag("a" * 127 + "c", SHA, "39495899061")
        self.assertEqual(128, len(first))
        self.assertEqual(128, len(second))
        self.assertNotEqual(first, second)
        self.assertTrue(first.endswith("-27adeb663eb6-39495899061"))

    def test_invalid_identity_rejected(self):
        for tag, sha, run in (
            ("../latest", SHA, "123"),
            ("latest", "bad", "123"),
            ("latest", SHA, "../latest"),
        ):
            with self.subTest(tag=tag, sha=sha, run=run), self.assertRaises(ValueError):
                staging_tag(tag, sha, run)

    def test_public_tag_collision_prevents_deletion(self):
        plan = self.plan()
        plan["publications"][0]["tags"].append(
            staging_tag(plan["builds"][0]["tag"], SHA, "123")
        )
        with self.assertRaisesRegex(ValueError, "configured public tag"):
            cleanup(
                plan,
                "user",
                "secret",
                "123",
                SHA,
                lambda *args, **kwargs: self.fail("No request allowed"),
            )

    def test_cleanup_exact_names_and_retry_names_are_stable(self):
        visited = []

        def request(item, **kwargs):
            visited.append(item)
            if item.get_method() == "POST":
                return BytesIO(json.dumps({"access_token": "secret"}).encode())
            raise HTTPError(item.full_url, 404, "missing", {}, None)

        plan = self.plan()
        cleanup(plan, "user", "secret", "123", SHA, request)
        expected = staging_tags(plan, SHA, "123")
        self.assertEqual(
            expected, [item.full_url.rsplit("/", 1)[1] for item in visited[1:]]
        )
        self.assertTrue(all(item.get_method() == "DELETE" for item in visited[1:]))
        self.assertEqual(3, len(visited))

    def test_cleanup_reports_failure_but_continues_other_tags(self):
        visited = []

        def request(item, **kwargs):
            visited.append(item)
            if item.get_method() == "POST":
                return BytesIO(b'{"access_token":"secret"}')
            raise HTTPError(item.full_url, 403, "forbidden", {}, None)

        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            cleanup(self.plan(), "user", "secret", "123", SHA, request)
        self.assertEqual(3, len(visited))
