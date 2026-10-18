"""Build a Windows image and record its immutable registry digest."""

import json
import os
import re
import subprocess
from pathlib import Path

from configure import require, write_json
from windows_cache import eligible_cache


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
    return f"wf-v3-{run_id}-{attempt}-{build_id}"


def build_command(entry, target_image, cache_image=None):
    args = [
        "build",
        "--isolation=hyperv",
        "--platform",
        entry["platform"],
        "--pull",
        "--file",
        entry["dockerfile"],
        "--tag",
        target_image,
    ]
    if cache_image:
        args.extend(["--cache-from", cache_image])
    for key, value in entry["buildArgs"].items():
        args.extend(["--build-arg", f"{key}={value}"])
    return [*args, entry["context"]]


def main(use_local_cache=False):
    entry = {
        **json.loads(os.environ["BUILD_ENTRY"]),
        "platform": os.environ["BUILD_PLATFORM"],
    }
    image = os.environ["IMAGE"]
    tag = staging_tag(
        entry["id"], os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    )
    target_image = f"{image}:{tag}"
    cache_image = f"{image}:{entry['tag']}"
    # Obsolete bases make the previous image expensive to pull but useless as a
    # classic build cache. Check metadata before downloading its layers.
    cache_reference = eligible_cache(entry, cache_image)
    pulled = (
        docker("image", "pull", cache_reference, check=False)
        if cache_reference
        else None
    )
    # Legacy --cache-from restricts eligible local layers to that image's
    # ancestry, excluding compiler stages. Sibling builds need unrestricted
    # local cache; a missing remote cache must not disable it either.
    cache_source = (
        cache_reference
        if pulled is not None and pulled.returncode == 0 and not use_local_cache
        else None
    )
    docker(*build_command(entry, target_image, cache_source))
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
    # A cached image can retain an index digest as well as its image digest.
    docker("image", "push", target_image)
    manifest = json.loads(
        docker("manifest", "inspect", "--verbose", target_image, capture=True).stdout
    )
    require(isinstance(manifest, dict), "Windows staging tag must resolve to one image")
    digest = manifest.get("Descriptor", {}).get("digest", "")
    require(
        isinstance(digest, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None,
        "Windows staging manifest must have an immutable image digest",
    )
    print(f"Pushed {target_image}@{digest}")
    write_json(
        Path(os.environ["RECORD_DIRECTORY"]) / f"{entry['id']}.json",
        {
            "id": entry["id"],
            "digest": digest,
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
