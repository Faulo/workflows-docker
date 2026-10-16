"""Validate consumer configuration and expand it into native build jobs."""

import argparse
import json
import math
import os
import re
from pathlib import Path

TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")
IMAGE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
VARIABLE = re.compile(r"\$(\$|\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)")


def read_env(path):
    """Read literal dotenv assignments without executing or expanding values."""
    values = {}
    for number, line in enumerate(
        Path(path).read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        require(match is not None, f"Invalid dotenv assignment at line {number}")
        name, value = match.groups()
        require(name not in values, f"Duplicate dotenv variable: {name}")
        if value.startswith(("'", '"')):
            quote = value[0]
            quoted = re.fullmatch(
                rf"{quote}((?:\\.|[^{quote}\\])*){quote}\s*(?:#.*)?", value
            )
            require(
                quoted is not None,
                f"Invalid quoted dotenv value at line {number}",
            )
            value = quoted[1]
            if quote == '"':
                value = re.sub(r'\\(["\\])', r"\1", value)
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[name] = value
    return values


def substitute(value, variables):
    """Expand JSON string values once; keys and substituted values stay literal."""
    if isinstance(value, str):

        def replace(match):
            token = match[1]
            if token == "$":
                return "$"
            name = token[1:-1] if token.startswith("{") else token
            require(name in variables, f"Undefined configuration variable: {name}")
            return variables[name]

        return VARIABLE.sub(replace, value)
    if isinstance(value, list):
        return [substitute(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: substitute(item, variables) for key, item in value.items()}
    return value


def resolve_config(config, root=None, environment=None):
    require(isinstance(config, dict), "configuration must be an object")
    environment = dict(os.environ if environment is None else environment)
    variables = {}
    env_file = substitute(config.get("envFile", ".env"), environment)
    relative_path(env_file, "envFile")
    if root is not None:
        path = root / env_file
        if path.exists() or "envFile" in config:
            relative_path(env_file, "envFile", root)
            variables = read_env(path)
    # Explicit process environment overrides dotenv, following dotenv conventions.
    return substitute(config, variables | environment)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def object_keys(value, allowed, description):
    require(isinstance(value, dict), f"{description} must be an object")
    require(
        not (value.keys() - allowed),
        f"Unknown properties in {description}: {value.keys() - allowed}",
    )


def text(value, description):
    require(
        isinstance(value, str) and value and not any(c in value for c in "\r\n\0"),
        f"{description} must be a nonempty single-line string",
    )
    return value


def arguments(value):
    require(isinstance(value, dict), "buildArgs must be an object")
    result = {}
    for key, item in value.items():
        require(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key),
            f"Invalid build argument: {key}",
        )
        require(
            isinstance(item, (str, int, float, bool))
            and (not isinstance(item, float) or math.isfinite(item)),
            f"Invalid value for build argument {key}",
        )
        result[key] = item if isinstance(item, str) else json.dumps(item)
        require(
            not any(c in result[key] for c in "\r\n\0"),
            f"Build argument {key} must be single-line",
        )
    return result


def relative_path(value, description, root=None, directory=False):
    text(value, description)
    # Configuration paths are portable and always relative to the consumer root.
    require(
        not value.startswith(("/", "-"))
        and "\\" not in value
        and ":" not in value
        and ".." not in value.split("/"),
        f"{description} must be a repository-relative path",
    )
    if root is not None:
        resolved = (root / value).resolve()
        require(
            resolved.is_relative_to(root.resolve()),
            f"{description} escapes the repository",
        )
        require(
            resolved.is_dir() if directory else resolved.is_file(),
            f"{description} does not exist: {value}",
        )
    return value


def make_plan(config, root=None, environment=None):
    config = resolve_config(config, root, environment)
    if type(config.get("schemaVersion")) is int and config["schemaVersion"] == 3:
        from matrix import make_matrix_plan

        return make_matrix_plan(config, root)
    object_keys(
        config,
        {
            "schemaVersion",
            "namespace",
            "image",
            "envFile",
            "context",
            "buildArgs",
            "targets",
            "variants",
        },
        "configuration",
    )
    require(
        type(config.get("schemaVersion")) is int and config["schemaVersion"] == 2,
        "schemaVersion must be 2 (legacy) or 3 (recursive matrix)",
    )
    image = config.get("image")
    namespace = config.get("namespace")
    require(
        isinstance(namespace, str) and IMAGE.fullmatch(namespace),
        "namespace must be a lowercase Docker Hub namespace",
    )
    require(
        isinstance(image, str) and IMAGE.fullmatch(image),
        "image must be a lowercase Docker Hub repository name without a namespace",
    )
    context = relative_path(config.get("context", "."), "context", root, directory=True)
    base_args = arguments(config.get("buildArgs", {}))
    variants = config.get("variants")
    require(
        isinstance(variants, list) and variants, "variants must be a nonempty array"
    )
    if "targets" in config:
        validate_targets(config["targets"], root)
    builds, publications, target_publications, tags = [], [], [], set()

    def register_tag(name):
        require(
            isinstance(name, str)
            and TAG.fullmatch(name)
            and not name.startswith("wf-v3-"),
            f"Invalid or reserved public tag: {name}",
        )
        require(name not in tags, f"Duplicate public tag: {name}")
        tags.add(name)

    for variant in variants:
        object_keys(variant, {"tag", "aliases", "buildArgs", "targets"}, "variant")
        tag = variant.get("tag")
        aliases = variant.get("aliases", [])
        require(isinstance(aliases, list), "aliases must be an array")
        for name in [tag, *aliases]:
            register_tag(name)
        merged_args = base_args | arguments(variant.get("buildArgs", {}))
        targets = variant.get("targets", config.get("targets"))
        validate_targets(targets, root)
        ids = []
        for target in targets:
            build_id = f"b{len(builds):03d}"
            target_tags = [f"{name}-{target['tag']}" for name in [tag, *aliases]]
            for name in target_tags:
                register_tag(name)
            target_publications.append({"tags": target_tags, "builds": [build_id]})
            build_args = merged_args | arguments(target.get("buildArgs", {}))
            builds.append(
                {
                    "id": build_id,
                    "variant": tag,
                    "target": target["tag"],
                    "tag": target_tags[0],
                    "runner": target["runner"],
                    "context": target.get("context", context),
                    "dockerfile": target["dockerfile"],
                    "buildArgs": build_args,
                    "buildArgsText": "\n".join(
                        f"{key}={value}" for key, value in build_args.items()
                    ),
                    "timeoutMinutes": target.get("timeoutMinutes", 180),
                }
            )
            ids.append(build_id)
        publications.append({"tags": [tag, *aliases], "builds": ids})
    publications.extend(target_publications)
    groups = {}
    for build in builds:
        target = build["target"]
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
        group["builds"].append(build["id"])
    require(len(groups) <= 256, "Configuration is limited to 256 matrix jobs")
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


def validate_targets(targets, root):
    require(
        isinstance(targets, list) and targets,
        "Each variant must have a nonempty targets array",
    )
    tags = set()
    for target in targets:
        object_keys(
            target,
            {
                "tag",
                "runner",
                "context",
                "dockerfile",
                "buildArgs",
                "timeoutMinutes",
            },
            "target",
        )
        target_tag = target.get("tag")
        require(
            isinstance(target_tag, str) and TAG.fullmatch(target_tag),
            f"Invalid target tag: {target_tag}",
        )
        require(target_tag not in tags, f"Duplicate target tag: {target_tag}")
        tags.add(target_tag)
        runner = target.get("runner")
        if isinstance(runner, list):
            require(bool(runner), "runner label array must not be empty")
            for label in runner:
                text(label, "runner label")
        else:
            text(runner, "runner")
        relative_path(target.get("dockerfile"), "dockerfile", root)
        if "context" in target:
            relative_path(target["context"], "target context", root, directory=True)
        arguments(target.get("buildArgs", {}))
        timeout = target.get("timeoutMinutes", 180)
        require(
            type(timeout) is int and 1 <= timeout <= 360,
            "timeoutMinutes must be between 1 and 360",
        )


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path.cwd()
    relative_path(args.config, "config", root)
    plan = make_plan(
        json.loads(
            (root / args.config).read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
        ),
        root,
    )
    write_json(args.output, plan)
    outputs = {
        "image": plan["reference"],
        "matrix": json.dumps(
            {
                "include": [
                    {k: v for k, v in group.items() if k != "builds"}
                    for group in plan["groups"]
                ]
            },
            separators=(",", ":"),
        ),
    }
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.writelines(f"{key}={value}\n" for key, value in outputs.items())
    print(
        f"Prepared {len(plan['builds'])} builds and {len(plan['publications'])} publications for {plan['image']}"
    )


if __name__ == "__main__":
    main()
