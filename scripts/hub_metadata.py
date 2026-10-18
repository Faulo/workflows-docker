"""Validate, snapshot, and synchronize optional Docker Hub repository metadata."""

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from configure import relative_path, require
from markdown_urls import rewrite_markdown

DESCRIPTION_LIMIT = 100
OVERVIEW_LIMIT = 25_000


def content_limit(value, field, limit):
    require(isinstance(value, str), f"{field} must be a string")
    require("\0" not in value, f"{field} must not contain NUL characters")
    size = len(value.encode("utf-8"))
    require(
        size <= limit,
        f"{field} is {size} UTF-8 bytes; Docker Hub allows at most {limit}. "
        "Shorten the content; it will not be truncated.",
    )


def read_overview(root, path):
    relative_path(path, "overviewFile", root)
    try:
        value = (root / path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise ValueError(
            f"overviewFile must be a readable UTF-8 file: {path}"
        ) from None
    content_limit(value, "overviewFile", OVERVIEW_LIMIT)
    return value


def metadata_fields(config, root=None):
    result = {}
    if "description" in config:
        value = config["description"]
        content_limit(value, "description", DESCRIPTION_LIMIT)
        require(
            "\r" not in value and "\n" not in value, "description must be single-line"
        )
        result["description"] = value
    if "overviewFile" in config:
        path = relative_path(config["overviewFile"], "overviewFile", root)
        if root is not None:
            read_overview(root, path)
        result["overviewFile"] = path
    return result


def prepare_metadata(plan, root, repository=None, branch=None):
    fields = metadata_fields(plan, root)
    result = {"description": fields["description"]} if "description" in fields else {}
    if "overviewFile" in fields:
        path = fields["overviewFile"]
        value = rewrite_markdown(read_overview(root, path), repository, branch, root)
        content_limit(value, "overviewFile after URL resolution", OVERVIEW_LIMIT)
        result["full_description"] = value
    return result


def synchronize(
    plan, metadata, username=None, token=None, publish=True, request=urlopen
):
    if not publish or not metadata:
        return
    require(
        not metadata.keys() - {"description", "full_description"},
        "Invalid Docker Hub metadata fields",
    )
    for field, value in metadata.items():
        content_limit(
            value,
            field,
            DESCRIPTION_LIMIT if field == "description" else OVERVIEW_LIMIT,
        )
    require(
        username and token,
        "Docker Hub metadata synchronization requires DOCKERHUB_USERNAME and DOCKERHUB_TOKEN",
    )
    stage = "authentication"
    try:
        with request(
            Request(
                "https://hub.docker.com/v2/auth/token",
                data=json.dumps({"identifier": username, "secret": token}).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=30,
        ) as response:
            access_token = json.load(response)["access_token"]
        require(
            isinstance(access_token, str) and access_token,
            "Invalid Docker Hub authentication response",
        )
        stage = "repository update"
        namespace, image = (quote(plan[key], safe="") for key in ("namespace", "image"))
        with request(
            Request(
                f"https://hub.docker.com/v2/repositories/{namespace}/{image}/",
                method="PATCH",
                data=json.dumps(metadata).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {access_token}",
                },
            ),
            timeout=30,
        ):
            pass
    except HTTPError as error:
        status = error.code
        error.close()
        raise RuntimeError(
            f"Docker Hub metadata {stage} failed (HTTP {status}). "
            "Check token permissions and repository ownership; metadata updates require repository edit access."
        ) from None
    except (OSError, ValueError, KeyError, TypeError):
        # Never include response bodies or exception text that might echo secrets.
        raise RuntimeError(
            f"Docker Hub metadata {stage} failed; check connectivity and credentials."
        ) from None
    print(f"Synchronized Docker Hub metadata for {plan['namespace']}/{plan['image']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--publish", choices=("true", "false"), default="true")
    args = parser.parse_args()
    synchronize(
        json.loads(Path(args.plan).read_text(encoding="utf-8")),
        json.loads(Path(args.metadata).read_text(encoding="utf-8")),
        os.environ.get("DOCKERHUB_USERNAME"),
        os.environ.get("DOCKERHUB_TOKEN"),
        publish=args.publish == "true",
    )


if __name__ == "__main__":
    main()
