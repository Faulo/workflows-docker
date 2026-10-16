"""Verify the unpublished fixture's two variants on all four native targets."""

import argparse
import json
import os
from pathlib import Path

from configure import require
from publish import load_records


def verify(plan, records):
    expected = {
        "linux-amd64": {"os": "linux", "architecture": "amd64"},
        "linux-arm64": {"os": "linux", "architecture": "arm64"},
        "windows-2019": {"os": "windows", "architecture": "amd64"},
        "windows-2022": {"os": "windows", "architecture": "amd64"},
    }
    combinations = {
        (target, variant) for target in expected for variant in ("1.0", "2.0")
    }
    require(
        len(plan["builds"]) == 8
        and {(build["target"], build["variant"]) for build in plan["builds"]}
        == combinations,
        "Fixture must build exactly two variants on all four targets",
    )
    expected_publications = {
        variant: {
            build["id"] for build in plan["builds"] if build["variant"] == variant
        }
        for variant in ("1.0", "2.0")
    }
    expected_publications.update(
        {
            f"{build['variant']}-{build['target']}": {build["id"]}
            for build in plan["builds"]
        }
    )
    expected_publications.update(
        {
            f"{variant}-{os_name}": {
                build["id"]
                for build in plan["builds"]
                if build["variant"] == variant and build["matrix"]["os"] == os_name
            }
            for variant in ("1.0", "2.0")
            for os_name in ("linux", "windows")
        }
    )
    actual_publications = {
        tag: set(publication["builds"])
        for publication in plan["publications"]
        for tag in publication["tags"]
    }
    require(
        len(plan["publications"]) == 14
        and actual_publications == expected_publications,
        "Fixture must plan two combined tags, eight target-specific tags, and four OS tags",
    )
    for build in plan["builds"]:
        record = records[build["id"]]
        require(record.get("local") is True, "Fixture image must remain unpublished")
        require(
            all(
                record["platform"].get(key) == value
                for key, value in expected[build["target"]].items()
            ),
            f"Incorrect platform for {build['id']}",
        )
        if build["target"].startswith("windows"):
            prefix = (
                "10.0.17763." if build["target"] == "windows-2019" else "10.0.20348."
            )
            require(
                record["platform"].get("os.version", "").startswith(prefix),
                f"Incorrect Windows release for {build['id']}",
            )
        require(
            record.get("labels")
            == {
                "workflow.fixture.variant": build["variant"],
                "workflow.fixture.target": build["target"],
                "workflow.fixture.shared": "shared across variants",
            },
            f"Incorrect variant, target, or substituted build arguments for {build['id']}",
        )
    print("Verified all eight unpublished fixture images")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--records", required=True)
    args = parser.parse_args()
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    verify(plan, load_records(plan, args.records))
    if "GITHUB_STEP_SUMMARY" in os.environ:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(
                "Verified **8 unpublished images**: `1.0` and `2.0` on Linux amd64/arm64 and Windows LTSC 2019/2022.\n"
            )


if __name__ == "__main__":
    main()
