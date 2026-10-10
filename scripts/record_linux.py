"""Record a successful Buildx result without creating public platform tags."""

import json
import os
import re
from pathlib import Path

from configure import require, write_json

if __name__ == "__main__":
    entry = json.loads(os.environ["BUILD_ENTRY"])
    digest = os.environ["IMAGE_DIGEST"]
    require(
        re.fullmatch(r"sha256:[0-9a-f]{64}", digest),
        "Buildx did not return a valid image digest",
    )
    write_json(
        Path(os.environ["RECORD_DIRECTORY"]) / f"{entry['id']}.json",
        {
            "id": entry["id"],
            "digest": digest,
            "platform": dict(
                zip(("os", "architecture"), os.environ["BUILD_PLATFORM"].split("/"))
            ),
        },
    )
