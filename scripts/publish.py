"""Assemble public manifests from immutable build results and verify aliases."""

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path

from configure import require
from registry import DOCKER_INDEX, INDEX, HubRegistry, content_digest


def descriptor_key(descriptor):
    platform = descriptor.get("platform", {})
    return (
        descriptor["digest"],
        platform.get("os"),
        platform.get("architecture"),
        platform.get("os.version"),
        platform.get("variant"),
        tuple(platform.get("os.features", [])),
    )


def verify_manifest(manifest, expected):
    require("manifests" in manifest, "Publication did not produce an image index")
    require(
        Counter(map(descriptor_key, manifest["manifests"]))
        == Counter(map(descriptor_key, expected)),
        "Published manifest differs from the expected digests or platform metadata",
    )


def source_descriptors(image, build, record, registry):
    digest = record.get("digest", "")
    require(
        re.fullmatch(r"sha256:[0-9a-f]{64}", digest),
        f"Invalid digest for {build['id']}",
    )
    reference = f"{image}@{digest}"
    manifest = registry.manifest(reference)
    require(
        manifest.get("digest") == digest,
        "Registry returned an unexpected source digest",
    )
    expected = record.get("platform", {})
    expected_os, expected_arch = expected.get("os"), expected.get("architecture")
    require(
        (expected_os, expected_arch)
        in (("linux", "amd64"), ("linux", "arm64"), ("windows", "amd64")),
        f"Missing or unsupported native platform for {build['id']}",
    )
    if "manifests" in manifest:
        require(
            expected_os == "linux",
            "Windows builds must publish a single image manifest",
        )
        descriptors = manifest["manifests"]
        runnable = [
            item
            for item in descriptors
            if item.get("platform", {}).get("os") != "unknown"
        ]
        require(
            len(runnable) == 1
            and runnable[0]["platform"]["os"] == expected_os
            and runnable[0]["platform"]["architecture"] == expected_arch,
            f"Source index has unexpected platforms for {build['id']}",
        )
        for item in descriptors:
            if item not in runnable:
                require(
                    item.get("annotations", {}).get("vnd.docker.reference.type")
                    == "attestation-manifest"
                    and item["annotations"].get("vnd.docker.reference.digest")
                    == runnable[0]["digest"],
                    "Source index contains an unexpected non-image manifest",
                )
        return descriptors
    config = registry.config(reference)
    require(
        config.get("os") == expected_os and config.get("architecture") == expected_arch,
        f"Unexpected image platform for {build['id']}",
    )
    platform = {
        key: config[key]
        for key in ("os", "architecture", "os.version", "os.features", "variant")
        if key in config
    }
    if expected_os == "windows":
        require(
            isinstance(platform.get("os.version"), str)
            and bool(platform["os.version"]),
            f"Unexpected Windows OS version for {build['id']}",
        )
        require(
            record.get("platform", {}).get("os.version") == platform["os.version"],
            "Windows local and registry OS versions differ",
        )
    return [
        {
            **{key: manifest[key] for key in ("mediaType", "digest", "size")},
            "platform": platform,
        }
    ]


def load_records(plan, directory):
    expected_ids = {build["id"] for build in plan["builds"]}
    records = {}
    for path in Path(directory).glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        require(
            record.get("id") in expected_ids and record["id"] not in records,
            f"Unexpected or duplicate build record: {path}",
        )
        records[record["id"]] = record
    require(
        records.keys() == expected_ids,
        f"Missing build records: {expected_ids - records.keys()}",
    )
    return records


def publish(plan, records, image, registry=None):
    registry = registry or HubRegistry(
        image, os.environ.get("DOCKERHUB_USERNAME"), os.environ.get("DOCKERHUB_TOKEN")
    )
    # Inspect every immutable source and validate every product before moving tags.
    sources = {
        build["id"]: source_descriptors(image, build, records[build["id"]], registry)
        for build in plan["builds"]
    }
    prepared = []
    for publication in plan["publications"]:
        descriptors = [
            descriptor
            for build_id in publication["builds"]
            for descriptor in sources[build_id]
        ]
        runnable = [
            item
            for item in descriptors
            if item.get("platform", {}).get("os") != "unknown"
        ]
        signatures = [
            (
                item["platform"]["os"],
                item["platform"]["architecture"],
                ".".join(item["platform"].get("os.version", "").split(".")[:3]),
            )
            for item in runnable
        ]
        require(
            len(set(signatures)) == len(signatures),
            "Ambiguous image platforms in one publication; separate Windows base families into top-level choices",
        )
        media_type = (
            DOCKER_INDEX
            if all(
                item["mediaType"].startswith("application/vnd.docker.")
                for item in descriptors
            )
            else INDEX
        )
        candidate = {
            "schemaVersion": 2,
            "mediaType": media_type,
            "manifests": descriptors,
        }
        payload = json.dumps(
            candidate, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        prepared.append((publication, descriptors, payload, media_type))
    for publication, descriptors, payload, media_type in prepared:
        digest = content_digest(payload)
        for tag in publication["tags"]:
            # Every child already lives in this repository. PUT only the index;
            # Buildx create recursively copies children even within one repo.
            registry.put_manifest(tag, payload, media_type)
            reference = f"{image}:{tag}"
            manifest = registry.manifest(reference)
            require(
                manifest.get("digest") == digest,
                f"Alias {reference} does not reference the published digest",
            )
            verify_manifest(manifest, descriptors)
            print(f"Verified {reference}@{digest}", flush=True)
        if "GITHUB_STEP_SUMMARY" in os.environ:
            with open(
                os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8"
            ) as summary:
                summary.write(
                    f"Published `{image}` tags **{', '.join(publication['tags'])}**: `{digest}`\n\n"
                )
                for descriptor in descriptors:
                    platform = descriptor.get("platform", {})
                    if platform.get("os") != "unknown":
                        summary.write(
                            f"- `{platform.get('os')}/{platform.get('architecture')}` "
                            f"{platform.get('os.version', '')}: `{descriptor['digest']}`\n"
                        )
                summary.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--records", required=True)
    args = parser.parse_args()
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    image = plan["reference"]
    publish(plan, load_records(plan, args.records), image)


if __name__ == "__main__":
    main()
