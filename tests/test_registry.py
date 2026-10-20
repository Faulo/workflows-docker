import base64
import json
import sys
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from publish import publish
from registry import INDEX, HubRegistry, RegistryRedirectHandler, content_digest

MEDIA = "application/vnd.oci.image.manifest.v1+json"


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.documents, self.blobs, self.pushed, self.requests = {}, {}, {}, []
        self.actions, self.subject = ["pull", "push"], "account"
        self.reject_once, self.wrong_digest = False, False
        self.auth_count = 0
        self.quota = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, status, payload=b"", digest=None):
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                if digest:
                    self.send_header("Docker-Content-Digest", digest)
                self.end_headers()
                self.wfile.write(payload)

            def authorized(self):
                owner.requests.append(
                    (self.command, self.path, self.headers.get("Authorization"))
                )
                if owner.reject_once:
                    owner.reject_once = False
                    self.respond(401)
                    return False
                if self.headers.get("Authorization") != "Bearer " + owner.token:
                    self.respond(401)
                    return False
                return True

            def do_GET(self):
                if self.path.startswith("/token?"):
                    owner.auth_count += 1
                    owner.assertEqual(
                        "Basic " + base64.b64encode(b"user:private-password").decode(),
                        self.headers.get("Authorization"),
                    )
                    owner.assertEqual(
                        ["repository:org/image:pull,push"],
                        parse_qs(urlsplit(self.path).query)["scope"],
                    )
                    claims = {
                        "sub": owner.subject,
                        "access": [
                            {
                                "type": "repository",
                                "name": "org/image",
                                "actions": owner.actions,
                            }
                        ],
                    }
                    encoded = (
                        base64.urlsafe_b64encode(json.dumps(claims).encode())
                        .decode()
                        .rstrip("=")
                    )
                    owner.token = "header." + encoded + ".private-signature"
                    self.respond(
                        200,
                        json.dumps({"token": owner.token, "expires_in": 300}).encode(),
                    )
                    return
                if not self.authorized():
                    return
                name = self.path.rsplit("/", 1)[1]
                source = owner.blobs if "/blobs/" in self.path else owner.documents
                if "/manifests/" in self.path and owner.quota is not None:
                    if owner.quota == 0:
                        self.respond(429)
                        return
                    owner.quota -= 1
                if name not in source:
                    self.respond(404, b"private-password private-signature")
                    return
                payload = source[name]
                self.respond(200, payload, content_digest(payload))

            def do_PUT(self):
                if not self.authorized():
                    return
                payload = self.rfile.read(int(self.headers["Content-Length"]))
                document = json.loads(payload)
                owner.assertEqual(document["mediaType"], self.headers["Content-Type"])
                if any(
                    item["digest"] not in owner.documents
                    for item in document["manifests"]
                ):
                    self.respond(400)
                    return
                tag = self.path.rsplit("/", 1)[1]
                owner.documents[tag] = payload
                owner.pushed[tag] = payload
                self.respond(
                    201,
                    digest="sha256:" + "f" * 64
                    if owner.wrong_digest
                    else content_digest(payload),
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.registry = HubRegistry(
            "org/image", "user", "private-password", self.request
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, item, **kwargs):
        parts = urlsplit(item.full_url)
        self.assertIn(parts.netloc, ("auth.docker.io", "registry-1.docker.io"))
        local = f"http://127.0.0.1:{self.server.server_port}{parts.path}"
        if parts.query:
            local += "?" + parts.query
        return urlopen(
            Request(
                local,
                method=item.get_method(),
                data=item.data,
                headers=dict(item.header_items()),
            ),
            **kwargs,
        )

    def seed_image(self, os_name, architecture, version=None, identity=None):
        config = {"os": os_name, "architecture": architecture}
        if version:
            config["os.version"] = version
        platform = config.copy()
        if identity:
            config["config"] = {"Labels": {"variant": identity}}
        blob = json.dumps(config).encode()
        config_digest = content_digest(blob)
        self.blobs[config_digest] = blob
        payload = json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": MEDIA,
                "config": {"digest": config_digest, "size": len(blob)},
                "layers": [],
            }
        ).encode()
        digest = content_digest(payload)
        self.documents[digest] = payload
        return {
            "digest": digest,
            "mediaType": MEDIA,
            "size": len(payload),
            "platform": platform,
        }

    def fixture(self):
        windows = self.seed_image("windows", "amd64", "10.0.17763.1234")
        linux = self.seed_image("linux", "amd64")
        # An attestation is preserved by descriptor, without pulling its contents.
        attestation_payload = b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json","layers":[]}'
        attestation_digest = content_digest(attestation_payload)
        self.documents[attestation_digest] = attestation_payload
        attestation = {
            "digest": attestation_digest,
            "size": len(attestation_payload),
            "mediaType": MEDIA,
            "platform": {"os": "unknown", "architecture": "unknown"},
            "annotations": {
                "vnd.docker.reference.type": "attestation-manifest",
                "vnd.docker.reference.digest": linux["digest"],
            },
        }
        linux_index = json.dumps(
            {"schemaVersion": 2, "mediaType": INDEX, "manifests": [linux, attestation]}
        ).encode()
        linux_digest = content_digest(linux_index)
        self.documents[linux_digest] = linux_index
        plan = {
            "builds": [{"id": "linux"}, {"id": "windows"}],
            "publications": [
                {"tags": ["8.2", "latest"], "builds": ["linux", "windows"]},
                {"tags": ["8.2-windows"], "builds": ["windows"]},
            ],
        }
        records = {
            "linux": {"digest": linux_digest, "platform": linux["platform"]},
            "windows": {"digest": windows["digest"], "platform": windows["platform"]},
        }
        return plan, records, [linux, attestation, windows]

    def test_publication_over_http_preserves_metadata_without_copying_children(self):
        plan, records, expected = self.fixture()
        with redirect_stdout(StringIO()):
            publish(plan, records, "org/image", self.registry)
        self.assertEqual(1, self.auth_count)
        self.assertEqual(self.pushed["8.2"], self.pushed["latest"])
        self.assertEqual(expected, json.loads(self.pushed["latest"])["manifests"])
        self.assertEqual(
            expected[-1:], json.loads(self.pushed["8.2-windows"])["manifests"]
        )
        self.assertEqual(
            2 + 3,
            sum(
                method == "GET" and "/manifests/" in path
                for method, path, _ in self.requests
            ),
        )
        self.assertEqual(1, sum("/blobs/" in path for _, path, _ in self.requests))
        self.assertTrue(
            all(auth == "Bearer " + self.token for _, _, auth in self.requests)
        )

    def test_anonymous_or_read_only_token_fails_before_registry_requests(self):
        for subject, actions in (("", ["pull", "push"]), ("account", ["pull"])):
            self.subject, self.actions = subject, actions
            registry = HubRegistry(
                "org/image", "user", "private-password", self.request
            )
            with self.assertRaises(ValueError):
                registry.manifest("org/image:latest")
        self.assertEqual([], self.requests)

    def test_farah_sized_publication_stays_under_100_manifest_pulls(self):
        plan = {"builds": [], "publications": []}
        records = {}
        self.quota = 100
        for version in ("7.4", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5"):
            build_ids = []
            for os_name, architecture in (
                ("linux", "amd64"),
                ("linux", "arm64"),
                ("windows", "amd64"),
            ):
                image = self.seed_image(
                    os_name,
                    architecture,
                    "10.0.17763.1234" if os_name == "windows" else None,
                    identity=version,
                )
                digest = image["digest"]
                if os_name == "linux":
                    attestation_payload = json.dumps(
                        {"attestation": version, "architecture": architecture}
                    ).encode()
                    attestation_digest = content_digest(attestation_payload)
                    self.documents[attestation_digest] = attestation_payload
                    attestation = {
                        "digest": attestation_digest,
                        "size": len(attestation_payload),
                        "mediaType": MEDIA,
                        "platform": {"os": "unknown", "architecture": "unknown"},
                        "annotations": {
                            "vnd.docker.reference.type": "attestation-manifest",
                            "vnd.docker.reference.digest": digest,
                        },
                    }
                    payload = json.dumps(
                        {
                            "schemaVersion": 2,
                            "mediaType": INDEX,
                            "manifests": [image, attestation],
                        }
                    ).encode()
                    digest = content_digest(payload)
                    self.documents[digest] = payload
                build_id = f"{version}-{os_name}-{architecture}"
                plan["builds"].append({"id": build_id})
                records[build_id] = {"digest": digest, "platform": image["platform"]}
                build_ids.append(build_id)
            for suffix, selected in (
                ("", build_ids),
                ("-linux", build_ids[:2]),
                ("-linux-amd64", build_ids[:1]),
                ("-linux-arm64", build_ids[1:2]),
                ("-windows", build_ids[2:]),
                ("-windows-ltsc2019", build_ids[2:]),
            ):
                tags = [version + suffix]
                if version == "8.5":
                    tags.append("latest" + suffix)
                plan["publications"].append({"tags": tags, "builds": selected})
        with redirect_stdout(StringIO()):
            publish(plan, records, "org/image", self.registry)
        self.assertEqual(21, len(records))
        self.assertEqual(48, len(self.pushed))
        self.assertEqual(31, self.quota)  # 21 source manifests + 48 tag verifications.
        self.assertEqual(7, sum("/blobs/" in path for _, path, _ in self.requests))
        self.assertEqual(1, self.auth_count)

    def test_unauthorized_request_refreshes_once_and_keeps_credentials(self):
        image = self.seed_image("windows", "amd64")
        self.reject_once = True
        with redirect_stdout(StringIO()):
            result = self.registry.manifest("org/image@" + image["digest"])
        self.assertEqual(image["digest"], result["digest"])
        self.assertEqual(2, self.auth_count)
        self.assertEqual(2, len(self.requests))

    def test_http_errors_do_not_echo_response_bodies_or_tokens(self):
        with (
            redirect_stdout(StringIO()),
            self.assertRaisesRegex(RuntimeError, "HTTP 404") as caught,
        ):
            self.registry.manifest("org/image:missing")
        self.assertNotIn("private-password", str(caught.exception))
        self.assertNotIn("private-signature", str(caught.exception))

    def test_digest_mismatch_and_missing_children_fail(self):
        image = self.seed_image("linux", "amd64")
        self.documents[image["digest"]] += b" "
        with (
            redirect_stdout(StringIO()),
            self.assertRaisesRegex(ValueError, "unexpected source digest"),
        ):
            self.registry.manifest("org/image@" + image["digest"])
        payload = json.dumps(
            {"schemaVersion": 2, "mediaType": INDEX, "manifests": [image]}
        ).encode()
        self.wrong_digest = True
        with self.assertRaisesRegex(ValueError, "unexpected publication digest"):
            self.registry.put_manifest("test", payload, INDEX)
        self.documents.clear()
        with self.assertRaisesRegex(RuntimeError, "HTTP 400"):
            self.registry.put_manifest("test", payload, INDEX)

    def test_blob_redirect_does_not_forward_credentials_to_cdn(self):
        request = Request(
            "https://registry-1.docker.io/v2/org/image/blobs/sha256:abc",
            headers={"Authorization": "Bearer private-token"},
        )
        redirected = RegistryRedirectHandler().redirect_request(
            request, None, 307, "redirect", {}, "https://cdn.example.com/signed-blob"
        )
        self.assertIsNone(redirected.get_header("Authorization"))
        self.assertEqual("Bearer private-token", request.get_header("Authorization"))
