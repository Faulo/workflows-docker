"""Build a Windows image and record its immutable registry digest."""

import json
import os
import subprocess
from pathlib import Path

from configure import require, write_json


def docker(*args, capture=False, check=True):
    return subprocess.run(
        ["docker", "--context", "default", *args],
        check=check,
        stdout=subprocess.PIPE if capture else None,
        text=True,
    )


def staging_tag(build_id, run_id, attempt):
    require(
        str(run_id).isdigit() and str(attempt).isdigit(),
        "Invalid workflow run identity",
    )
    return f"wf-v2-{run_id}-{attempt}-{build_id}"


def build_command(entry, target_image, cache_image):
    args = [
        "build",
        "--isolation=hyperv",
        "--platform",
        entry["platform"],
        "--pull",
        "--cache-from",
        cache_image,
        "--file",
        entry["dockerfile"],
        "--tag",
        target_image,
    ]
    for key, value in entry["buildArgs"].items():
        args.extend(["--build-arg", f"{key}={value}"])
    return [*args, entry["context"]]


def main():
    entry = {
        **json.loads(os.environ["BUILD_ENTRY"]),
        "platform": os.environ["BUILD_PLATFORM"],
    }
    image = os.environ["IMAGE"]
    tag = staging_tag(
        entry["id"], os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    )
    target_image = f"{image}:{tag}"
    cache_image = f"{image}:{entry['variant']}"
    # A cold cache or an incompatible previous manifest must not prevent a build.
    docker("image", "pull", cache_image, check=False)
    docker(*build_command(entry, target_image, cache_image))
    inspection = json.loads(
        docker("image", "inspect", target_image, capture=True).stdout
    )[0]
    require(
        inspection["Os"] == "windows" and inspection["Architecture"] == "amd64",
        "Unexpected Windows image platform",
    )
    version = inspection.get("OsVersion", "")
    require(
        isinstance(version, str) and bool(version),
        f"Unexpected Windows image OS version: {version}",
    )
    docker("image", "push", target_image)
    inspection = json.loads(
        docker("image", "inspect", target_image, capture=True).stdout
    )[0]
    digests = {
        item.split("@", 1)[1]
        for item in inspection.get("RepoDigests", [])
        if item.split("@", 1)[0].removeprefix("docker.io/") == image
    }
    require(
        len(digests) == 1,
        "Pushed Windows image must have exactly one repository digest",
    )
    write_json(
        Path(os.environ["RECORD_DIRECTORY"]) / f"{entry['id']}.json",
        {
            "id": entry["id"],
            "digest": digests.pop(),
            "stagingTag": tag,
            "platform": {
                "os": "windows",
                "architecture": "amd64",
                "os.version": version,
            },
        },
    )


if __name__ == "__main__":
    main()
