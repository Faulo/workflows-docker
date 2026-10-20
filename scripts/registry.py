"""Authenticated Docker Hub manifest operations without recursive image copies."""

import base64
import hashlib
import json
import re
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from configure import IMAGE, TAG, require

INDEX = "application/vnd.oci.image.index.v1+json"
DOCKER_INDEX = "application/vnd.docker.distribution.manifest.list.v2+json"
ACCEPT = (
    f"{INDEX}, {DOCKER_INDEX}, application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)


def content_digest(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class RegistryRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        if urlsplit(url).scheme != "https":
            raise OSError("Refused an insecure registry redirect")
        redirected = super().redirect_request(request, fp, code, message, headers, url)
        if redirected and urlsplit(request.full_url).netloc != urlsplit(url).netloc:
            # Blob downloads may redirect to signed CDN URLs. Registry credentials
            # belong only to the registry, never to the redirect destination.
            redirected.remove_header("Authorization")
        return redirected


class HubRegistry:
    def __init__(self, repository, username, password, request=None):
        require(
            len(repository.split("/")) == 2
            and all(IMAGE.fullmatch(part) for part in repository.split("/")),
            "Registry repository must be a Docker Hub namespace/image",
        )
        require(
            username and password,
            "Registry publication requires Docker Hub credentials",
        )
        self.repository = repository
        self.username, self.password = username, password
        self.request = request or build_opener(RegistryRedirectHandler()).open
        self.token = None
        self.expires = 0
        self.manifests = {}

    def authenticate(self):
        query = urlencode(
            {
                "service": "registry.docker.io",
                "scope": f"repository:{self.repository}:pull,push",
            }
        )
        basic = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        try:
            with self.request(
                Request(
                    "https://auth.docker.io/token?" + query,
                    headers={"Authorization": "Basic " + basic},
                ),
                timeout=30,
            ) as response:
                body = json.load(response)
            token = body["token"]
            payload = token.split(".")[1]
            claims = json.loads(
                base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            )
            authenticated = bool(claims.get("sub"))
            permitted = any(
                grant.get("type") == "repository"
                and grant.get("name") == self.repository
                and {"pull", "push"}.issubset(grant.get("actions", []))
                for grant in claims.get("access", [])
            )
            expires = time.monotonic() + max(0, int(body.get("expires_in", 300)) - 30)
        except HTTPError as error:
            status = error.code
            error.close()
            raise RuntimeError(
                f"Docker Hub registry authentication failed (HTTP {status})"
            ) from None
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            raise RuntimeError(
                "Docker Hub registry authentication failed; check connectivity and credentials"
            ) from None
        require(authenticated, "Docker Hub registry issued an anonymous token")
        require(
            permitted,
            f"Docker Hub token lacks pull/push permission for {self.repository}",
        )
        self.token, self.expires = token, expires
        print(f"Authenticated registry pull/push access to {self.repository}")

    def send(self, method, path, data=None, media_type=None):
        for attempt in range(2):
            if self.token is None or time.monotonic() >= self.expires:
                self.authenticate()
            headers = {"Authorization": "Bearer " + self.token, "Accept": ACCEPT}
            if media_type:
                headers["Content-Type"] = media_type
            try:
                with self.request(
                    Request(
                        f"https://registry-1.docker.io/v2/{self.repository}/{path}",
                        method=method,
                        data=data,
                        headers=headers,
                    ),
                    timeout=30,
                ) as response:
                    return response.read(), response.headers
            except HTTPError as error:
                status = error.code
                error.close()
                if status == 401 and attempt == 0:
                    self.token = None
                    continue
                raise RuntimeError(
                    f"Docker Hub registry {method} {path} failed (HTTP {status})"
                ) from None
            except OSError:
                raise RuntimeError(
                    f"Docker Hub registry {method} {path} failed; check connectivity"
                ) from None

    def manifest(self, reference):
        prefix = self.repository
        require(
            reference.startswith((prefix + ":", prefix + "@")),
            "Unexpected registry repository",
        )
        name = reference[len(prefix) + 1 :]
        immutable = reference[len(prefix)] == "@"
        require(
            bool(re.fullmatch(r"sha256:[0-9a-f]{64}", name))
            if immutable
            else bool(TAG.fullmatch(name)),
            "Invalid registry manifest reference",
        )
        if immutable and name in self.manifests:
            return self.manifests[name]
        payload, headers = self.send("GET", "manifests/" + quote(name, safe=":"))
        digest = content_digest(payload)
        require(
            not immutable or digest == name,
            "Registry returned an unexpected source digest",
        )
        require(
            headers.get("Docker-Content-Digest", digest) == digest,
            "Registry manifest digest header differs from its content",
        )
        document = json.loads(payload)
        document["digest"], document["size"] = digest, len(payload)
        self.manifests[digest] = document
        return document

    def config(self, reference):
        descriptor = self.manifest(reference)["config"]
        payload, _ = self.send("GET", "blobs/" + quote(descriptor["digest"], safe=":"))
        require(
            content_digest(payload) == descriptor["digest"],
            "Registry image configuration digest mismatch",
        )
        require(
            len(payload) == descriptor["size"],
            "Registry image configuration size mismatch",
        )
        return json.loads(payload)

    def put_manifest(self, tag, payload, media_type):
        require(TAG.fullmatch(tag), "Invalid publication tag")
        _, headers = self.send("PUT", "manifests/" + tag, payload, media_type)
        require(
            headers.get("Docker-Content-Digest") == content_digest(payload),
            "Registry accepted an unexpected publication digest",
        )
