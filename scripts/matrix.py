"""Expand recursive, labeled matrix axes into builds and prefix publications."""

import json
import re

from configure import (
    IMAGE,
    TAG,
    arguments,
    object_keys,
    relative_path,
    require,
    validate_targets,
)

FIELDS = {"context", "dockerfile", "runner", "buildArgs", "timeoutMinutes"}
EXPRESSION = re.compile(r"\{\{\s*matrix\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def resolve_matrix(value, selected):
    if isinstance(value, str):

        def replace(match):
            require(match[1] in selected, f"Unknown matrix reference: {match[1]}")
            return selected[match[1]]

        result = EXPRESSION.sub(replace, value)
        require("{{" not in result, f"Invalid matrix expression: {value}")
        return result
    if isinstance(value, dict):
        return {key: resolve_matrix(item, selected) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_matrix(item, selected) for item in value]
    return value


def merge(parent, child):
    result = parent | {key: value for key, value in child.items() if key in FIELDS}
    if "buildArgs" in child:
        arguments(child["buildArgs"])
        result["buildArgs"] = parent.get("buildArgs", {}) | child["buildArgs"]
    return result


def axes(matrix):
    require(isinstance(matrix, dict) and matrix, "matrix must be a nonempty map")
    for label, axis in matrix.items():
        require(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", label),
            f"Invalid matrix axis label: {label}",
        )
        object_keys(axis, FIELDS | {"tags"}, f"matrix axis {label}")
        tags = axis.get("tags")
        require(
            isinstance(tags, dict) and tags, f"Axis {label} tags must be a nonempty map"
        )
        if "buildArgs" in axis:
            arguments(axis["buildArgs"])
        for tag, choice in tags.items():
            require(
                TAG.fullmatch(tag) and "{{" not in tag,
                f"Invalid matrix tag segment: {tag}",
            )
            object_keys(choice, FIELDS | {"aliases", "matrix"}, f"matrix choice {tag}")
            aliases = choice.get("aliases", [])
            require(isinstance(aliases, list), "aliases must be an array")
            for alias in aliases:
                require(
                    isinstance(alias, str) and TAG.fullmatch(alias),
                    f"Invalid matrix alias: {alias}",
                )
            if "buildArgs" in choice:
                arguments(choice["buildArgs"])
            if "matrix" in choice:
                axes(choice["matrix"])
    return list(matrix.items())


def make_matrix_plan(config, root=None):
    object_keys(
        config,
        FIELDS | {"schemaVersion", "namespace", "image", "envFile", "matrix"},
        "configuration",
    )
    namespace, image = config.get("namespace"), config.get("image")
    for name, value in (("namespace", namespace), ("image", image)):
        require(
            isinstance(value, str) and IMAGE.fullmatch(value),
            f"{name} must be a lowercase Docker Hub name",
        )
    pending = axes(config.get("matrix"))
    builds, publications, public_tags, groups = [], [], set(), {}

    def walk(pending, inherited, selected, segments, prefixes, ancestors):
        if not pending:
            resolved = resolve_matrix(inherited, selected)
            target = "-".join(segments[1:]) or segments[0]
            validate_targets([{"tag": target, **resolved}], root)
            build_args = arguments(resolved.get("buildArgs", {}))
            build_id = f"b{len(builds):03d}"
            build = {
                "id": build_id,
                "variant": segments[0],
                "target": target,
                "tag": prefixes[0],
                "runner": resolved["runner"],
                "context": resolved.get("context", "."),
                "dockerfile": resolved["dockerfile"],
                "buildArgs": build_args,
                "buildArgsText": "\n".join(
                    f"{key}={value}" for key, value in build_args.items()
                ),
                "timeoutMinutes": resolved.get("timeoutMinutes", 180),
                "matrix": selected,
            }
            # Context defaults must receive the same filesystem checks as overrides.
            relative_path(build["context"], "context", root, directory=True)
            builds.append(build)
            for publication in ancestors:
                publication["builds"].append(build_id)
            if target not in groups:
                groups[target] = {
                    "id": f"g{len(groups):03d}",
                    "target": target,
                    "runner": build["runner"],
                    "timeoutMinutes": build["timeoutMinutes"],
                    "builds": [],
                }
            group = groups[target]
            require(
                group["runner"] == build["runner"]
                and group["timeoutMinutes"] == build["timeoutMinutes"],
                f"Target {target} must use the same runner and timeout in every variant",
            )
            group["builds"].append(build_id)
            require(len(groups) <= 256, "Configuration is limited to 256 matrix jobs")
            require(
                len(builds) <= 4096, "Matrix exceeds the build expansion limit (4096)"
            )
            return
        (label, axis), *remaining = pending
        require(label not in selected, f"Repeated matrix axis on one path: {label}")
        for segment, choice in axis["tags"].items():
            names = [segment, *choice.get("aliases", [])]
            require(
                len(prefixes) * len(names) <= 4096,
                "Matrix exceeds the alias expansion limit (4096)",
            )
            tags = [
                f"{prefix}-{name}" if prefix else name
                for prefix in prefixes
                for name in names
            ]
            for tag in tags:
                require(
                    TAG.fullmatch(tag) and not tag.startswith("wf-v3-"),
                    f"Invalid or reserved public tag: {tag}",
                )
                require(tag not in public_tags, f"Duplicate public tag: {tag}")
                public_tags.add(tag)
            publication = {"tags": tags, "builds": []}
            publications.append(publication)
            require(
                len(publications) <= 16384,
                "Matrix exceeds the publication expansion limit",
            )
            walk(
                (axes(choice["matrix"]) if "matrix" in choice else []) + remaining,
                merge(merge(inherited, axis), choice),
                selected | {label: segment},
                [*segments, segment],
                tags,
                [*ancestors, publication],
            )

    walk(pending, merge({}, config), {}, [], [""], [])
    plan = {
        "namespace": namespace,
        "image": image,
        "reference": f"{namespace}/{image}",
        "builds": builds,
        "groups": list(groups.values()),
        "publications": publications,
    }
    require(
        len(json.dumps(plan).encode()) < 400_000,
        "Configuration exceeds the workflow output size budget",
    )
    return plan
