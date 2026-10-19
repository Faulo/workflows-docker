import base64
import json
import sys
import unittest
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_registry_auth import check


class RegistryAuthTests(unittest.TestCase):
    def responses(self, subject="account", remaining="100;w=21600"):
        payload = (
            base64.urlsafe_b64encode(json.dumps({"sub": subject}).encode())
            .decode()
            .rstrip("=")
        )
        token = f"header.{payload}.signature"
        auth = BytesIO(json.dumps({"token": token}).encode())
        quota = Mock()
        quota.__enter__ = Mock(return_value=quota)
        quota.__exit__ = Mock(return_value=False)
        quota.headers = (
            {}
            if remaining is None
            else {"ratelimit-limit": "200;w=21600", "ratelimit-remaining": remaining}
        )
        return Mock(side_effect=[auth, quota])

    def test_authenticated_probe_uses_head_without_printing_credentials(self):
        request = self.responses()
        output = StringIO()
        with redirect_stdout(output):
            check("user", "secret", request)
        first, second = [call.args[0] for call in request.call_args_list]
        self.assertEqual("HEAD", second.get_method())
        self.assertIn("ratelimitpreview/test/manifests/latest", second.full_url)
        self.assertEqual(
            "Basic " + base64.b64encode(b"user:secret").decode(),
            first.get_header("Authorization"),
        )
        self.assertIn("authenticated: True", output.getvalue())
        self.assertIn("100;w=21600", output.getvalue())
        self.assertNotIn("secret", output.getvalue())
        self.assertNotIn("signature", output.getvalue())

    def test_anonymous_token_stops_before_builds(self):
        request = self.responses(subject="")
        with (
            redirect_stdout(StringIO()),
            self.assertRaisesRegex(ValueError, "anonymous token"),
        ):
            check("user", "secret", request)
        self.assertEqual(1, request.call_count)

    def test_exhausted_quota_stops_before_builds(self):
        with (
            redirect_stdout(StringIO()),
            self.assertRaisesRegex(ValueError, "quota is exhausted"),
        ):
            check("user", "secret", self.responses(remaining="0;w=21600"))

    def test_unlimited_quota_is_accepted(self):
        with redirect_stdout(StringIO()):
            check("user", "secret", self.responses(remaining=None))


if __name__ == "__main__":
    unittest.main()
