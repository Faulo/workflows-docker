"""Name staging images and keep cleanup separate from configured public tags."""

import hashlib
import re


def staging_tag(tag, source_sha, run_id):
    if not isinstance(tag, str) or not re.fullmatch(
        r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag
    ):
        raise ValueError("Invalid staging image's public tag")
    if not re.fullmatch(r"[0-9a-f]{40}", str(source_sha)) or not re.fullmatch(
        r"[0-9]+", str(run_id)
    ):
        raise ValueError("Invalid workflow source SHA or run identity")
    suffix = f"-{source_sha[:12]}-{run_id}"
    budget = 128 - len(suffix)
    if budget < 18:
        raise ValueError("Workflow run identity is too long for a Docker tag")
    if len(tag) > budget:
        tag = tag[: budget - 17] + "-" + hashlib.sha256(tag.encode()).hexdigest()[:16]
    return tag + suffix


def staging_tags(plan, source_sha, run_id):
    tags = [staging_tag(build["tag"], source_sha, run_id) for build in plan["builds"]]
    public = {
        tag for publication in plan["publications"] for tag in publication["tags"]
    }
    if public.intersection(tags):
        raise ValueError("Staging tag collides with a configured public tag")
    if len(tags) != len(set(tags)):
        raise ValueError("Builds have colliding staging tags")
    return tags
