"""Delete only the staging names computed for this workflow run."""

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from staging import staging_tags


def cleanup(plan, username, token, run_id, source_sha, request=urlopen):
    tags = staging_tags(plan, source_sha, run_id)
    if not tags:
        return
    payload = json.dumps({"identifier": username, "secret": token}).encode()
    with request(
        Request(
            "https://hub.docker.com/v2/auth/token",
            data=payload,
            headers={"Content-Type": "application/json"},
        ),
        timeout=30,
    ) as response:
        access_token = json.load(response)["access_token"]
    errors = []
    for tag in tags:
        url = f"https://hub.docker.com/v2/namespaces/{quote(plan['namespace'], safe='')}/repositories/{quote(plan['image'], safe='')}/tags/{tag}"
        try:
            with request(
                Request(
                    url,
                    method="DELETE",
                    headers={"Authorization": f"Bearer {access_token}"},
                ),
                timeout=30,
            ):
                pass
        except HTTPError as error:
            if error.code != 404:
                errors.append(f"{tag}: HTTP {error.code}")
            error.close()
        except OSError as error:
            errors.append(f"{tag}: {error}")
    if errors:
        raise RuntimeError("Failed to clean staging tags: " + "; ".join(errors))
    print(f"Cleaned {len(tags)} possible staging tags for this run")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    args = parser.parse_args()
    cleanup(
        json.loads(Path(args.plan).read_text(encoding="utf-8")),
        os.environ["DOCKERHUB_USERNAME"],
        os.environ["DOCKERHUB_TOKEN"],
        os.environ["GITHUB_RUN_ID"],
        os.environ["GITHUB_SHA"],
    )
