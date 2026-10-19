"""Check Docker Hub authentication and pull quota before expensive builds."""

import base64
import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from configure import require


def check(username, password, request=urlopen):
    repository = "ratelimitpreview/test"
    query = urlencode(
        {"service": "registry.docker.io", "scope": f"repository:{repository}:pull"}
    )
    credential = base64.b64encode(f"{username}:{password}".encode()).decode()
    with request(
        Request(
            "https://auth.docker.io/token?" + query,
            headers={"Authorization": "Basic " + credential},
        ),
        timeout=30,
    ) as response:
        token = json.load(response)["token"]
    payload = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    authenticated = bool(claims.get("sub"))
    print(f"Docker Hub token is authenticated: {authenticated}")
    require(
        authenticated,
        "Docker Hub issued an anonymous token; check the configured username and token",
    )
    with request(
        Request(
            f"https://registry-1.docker.io/v2/{repository}/manifests/latest",
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json",
            },
            method="HEAD",
        ),
        timeout=30,
    ) as response:
        remaining = response.headers.get("ratelimit-remaining")
        print(
            f"Docker Hub pull limit: {response.headers.get('ratelimit-limit', 'unlimited or unavailable')}"
        )
        print(f"Docker Hub pulls remaining: {remaining or 'unlimited or unavailable'}")
        require(
            remaining is None or int(remaining.split(";", 1)[0]) > 0,
            "Docker Hub pull quota is exhausted; retry after the quota window resets",
        )


def main():
    check(os.environ["DOCKERHUB_USERNAME"], os.environ["DOCKERHUB_TOKEN"])


if __name__ == "__main__":
    main()
