"""Prepare a shared Bake build or execute classic Windows builds for one target."""

import argparse
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

import build_windows
from configure import require, write_json


def group_builds(plan, group_id):
    group = next(group for group in plan["groups"] if group["id"] == group_id)
    builds = {build["id"]: build for build in plan["builds"]}
    return group, [builds[build_id] for build_id in group["builds"]]


def bake_definition(
    plan, group_id, platform, publish=True, source="source", output="bake"
):
    group, builds = group_builds(plan, group_id)
    require(
        platform in ("linux/amd64", "linux/arm64"),
        "Bake builds require a native Linux runner",
    )
    scopes = [f"v4-{build['variant']}-{build['target']}" for build in builds]
    targets = {}
    for build, scope in zip(builds, scopes):
        targets[build["id"]] = {
            "context": f"{source}/{build['context']}",
            "dockerfile": os.path.relpath(
                build["dockerfile"], build["context"]
            ).replace("\\", "/"),
            "args": build["buildArgs"],
            "platforms": [platform],
            "tags": [
                plan["reference"]
                if publish
                else f"tmp/{plan['image']}:{build['target']}-{build['variant']}"
            ],
            "pull": True,
            # Every variant imports the target's sibling caches. Exports have
            # distinct owners, so parallel builds cannot overwrite each other.
            "cache-from": [f"type=gha,scope={item}" for item in scopes],
            "cache-to": [f"type=gha,mode=max,scope={scope}"],
            "attest": ["type=provenance,mode=max", "type=sbom"],
            "output": [
                "type=image,push-by-digest=true,name-canonical=true,push=true"
                if publish
                else f"type=oci,dest={output}/{build['id']}.tar"
            ],
        }
    return {"group": {"default": {"targets": group["builds"]}}, "target": targets}


def oci_config(path):
    with tarfile.open(path) as archive:

        def read(name):
            return json.load(archive.extractfile(name))

        def blob(digest):
            return read("blobs/" + digest.replace(":", "/"))

        index = read("index.json")
        # OCI export may wrap a single-platform index containing attestations.
        while "manifests" in index:
            candidates = [
                item
                for item in index["manifests"]
                if item.get("platform", {}).get("os") != "unknown"
            ]
            require(len(candidates) == 1, "Trial archive has ambiguous runnable images")
            index = blob(candidates[0]["digest"])
        return blob(index["config"]["digest"])


def local_result(build, digest, platform, config):
    expected = dict(zip(("os", "architecture"), platform.split("/")))
    require(
        all(config.get(key) == value for key, value in expected.items()),
        f"Unexpected platform for {build['id']}",
    )
    if expected["os"] == "windows":
        version = config.get("os.version")
        require(
            isinstance(version, str) and bool(version),
            f"Missing Windows OS version for {build['id']}",
        )
        expected["os.version"] = version
    return {
        "id": build["id"],
        "digest": digest,
        "platform": expected,
        "local": True,
        "labels": config.get("config", {}).get("Labels") or {},
    }


def record_results(
    plan,
    group_id,
    platform,
    metadata,
    directory,
    publish=True,
    output="bake",
):
    _, builds = group_builds(plan, group_id)
    for build in builds:
        digest = metadata.get(build["id"], {}).get("containerimage.digest", "")
        require(
            re.fullmatch(r"sha256:[0-9a-f]{64}", digest),
            f"Missing Bake digest for {build['variant']} / {build['target']}",
        )
        expected = dict(zip(("os", "architecture"), platform.split("/")))
        record = {"id": build["id"], "digest": digest, "platform": expected}
        if not publish:
            record = local_result(
                build, digest, platform, oci_config(Path(output) / f"{build['id']}.tar")
            )
        write_json(
            Path(directory) / f"{build['id']}.json",
            record,
        )
        print(
            f"Built {build['variant']} × {build['target']}: {digest} ({expected})",
            flush=True,
        )


def classic(plan, group_id, platform, directory, publish=True):
    _, builds = group_builds(plan, group_id)
    failed = []
    for index, build in enumerate(builds):
        print(f"::group::{build['variant']} × {build['target']}", flush=True)
        os.environ.update(
            IMAGE=plan["reference"],
            BUILD_ENTRY=json.dumps(build),
            BUILD_PLATFORM=platform,
            RECORD_DIRECTORY=str(Path(directory).resolve()),
        )
        try:
            if publish:
                build_windows.main(use_local_cache=index > 0)
            else:
                image = f"tmp/{plan['image']}:{build['target']}-{build['variant']}"
                build_windows.docker(
                    *build_windows.build_command({**build, "platform": platform}, image)
                )
                config = json.loads(
                    build_windows.docker("image", "inspect", image, capture=True).stdout
                )[0]
                record = local_result(
                    build,
                    config["Id"],
                    platform,
                    {
                        "os": config["Os"],
                        "architecture": config["Architecture"],
                        "os.version": config.get("OsVersion"),
                        "config": config.get("Config", {}),
                    },
                )
                write_json(Path(directory) / f"{build['id']}.json", record)
                print(
                    f"Built {build['variant']} × {build['target']}: {record['digest']} ({record['platform']})",
                    flush=True,
                )
        except (subprocess.CalledProcessError, ValueError) as error:
            failed.append(build["id"])
            print(
                f"::error::{build['variant']} × {build['target']}: {error}", flush=True
            )
        finally:
            print("::endgroup::", flush=True)
    require(not failed, f"Required builds failed: {', '.join(failed)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "record", "classic"])
    parser.add_argument("--plan", required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--directory", default="records")
    parser.add_argument("--output", default="bake")
    parser.add_argument("--source", default="source")
    parser.add_argument("--publish", choices=["true", "false"], default="true")
    args = parser.parse_args()
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    publish = args.publish == "true"
    if args.command == "prepare":
        Path(args.output).mkdir(parents=True, exist_ok=True)
        write_json(
            Path(args.output) / "docker-bake.json",
            bake_definition(
                plan, args.group, args.platform, publish, args.source, args.output
            ),
        )
    elif args.command == "record":
        metadata = json.loads(
            (Path(args.output) / "metadata.json").read_text(encoding="utf-8")
        )
        record_results(
            plan,
            args.group,
            args.platform,
            metadata,
            args.directory,
            publish,
            args.output,
        )
    else:
        classic(plan, args.group, args.platform, args.directory, publish)


if __name__ == "__main__":
    main()
