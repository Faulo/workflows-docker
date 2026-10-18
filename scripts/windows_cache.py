"""Check classic Windows cache ancestry without downloading image layers."""

import io
import json
import re
import shlex
import subprocess
from pathlib import Path

DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
VARIABLE = re.compile(
    r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|"
    r"\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-+])([^{}$]*))?\})"
)


def expand(value, arguments):
    """Resolve common Docker ARG forms; refuse expressions we cannot prove."""

    def substitute(match):
        name = match[1] or match[2]
        operator, alternative = match[3], match[4]
        present = name in arguments
        if operator and operator.startswith(":"):
            present = present and bool(arguments.get(name))
        if operator:
            if operator.endswith("-"):
                return arguments[name] if present else alternative
            return alternative if present else ""
        if not present:
            raise ValueError("unresolved FROM argument")
        return arguments[name]

    # Never recursively expand substituted values or interpret shell commands.
    parts = []
    end = 0
    for match in VARIABLE.finditer(value):
        literal = value[end : match.start()]
        if "$" in literal:
            raise ValueError("unsupported FROM expression")
        parts.extend((literal, substitute(match)))
        end = match.end()
    if "$" in value[end:]:
        raise ValueError("unsupported FROM expression")
    return "".join(parts) + value[end:]


def final_base(path, build_args, platform):
    """Find the external base inherited by the final stage, not COPY sources."""
    from dockerfile_parse import DockerfileParser

    parser = DockerfileParser(fileobj=io.BytesIO(Path(path).read_bytes()))
    arguments, aliases = {}, {}
    stages = []
    for instruction in parser.structure:
        kind, value = instruction["instruction"], instruction["value"]
        if kind == "ARG" and not stages:
            fields = shlex.split(value)
            if len(fields) != 1:
                raise ValueError("unsupported global ARG")
            name, separator, default = fields[0].partition("=")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError("unsupported global ARG name")
            if name in build_args:
                arguments[name] = build_args[name]
            elif separator:
                arguments[name] = expand(default, arguments)
        elif kind == "FROM":
            fields = shlex.split(value)
            selected_platform = platform
            if fields and fields[0].lower().startswith("--platform="):
                selected_platform = expand(fields.pop(0).split("=", 1)[1], arguments)
            if len(fields) not in (1, 3) or (
                len(fields) == 3 and fields[1].upper() != "AS"
            ):
                raise ValueError("unsupported FROM syntax")
            source = expand(fields[0], arguments)
            if not source or any(char.isspace() for char in source) or "$" in source:
                raise ValueError("unresolved FROM reference")
            base = aliases.get(source.lower(), (source, selected_platform))
            stages.append(base)
            if len(fields) == 3:
                alias = fields[2].lower()
                if alias in aliases:
                    raise ValueError("ambiguous stage alias")
                aliases[alias] = base
    if not stages:
        raise ValueError("Dockerfile has no FROM")
    source, selected_platform = stages[-1]
    if source.lower() == "scratch" or selected_platform != platform:
        raise ValueError("final base has no supported native layer chain")
    return source


def inspect_manifest(reference):
    # Use Docker's existing credentials; keep registry response bodies out of logs.
    result = subprocess.run(
        [
            "docker",
            "--context",
            "default",
            "manifest",
            "inspect",
            "--verbose",
            reference,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def native_manifest(metadata, platform):
    os_name, architecture = platform.split("/")
    entries = metadata if isinstance(metadata, list) else [metadata]
    matches = []
    for entry in entries:
        descriptor = entry.get("Descriptor", {})
        native = descriptor.get("platform", {})
        if (native.get("os"), native.get("architecture")) == (os_name, architecture):
            matches.append(entry)
    # Never guess which Windows servicing release Docker would select.
    if len(matches) != 1:
        raise ValueError("missing or ambiguous native manifest")
    selected = matches[0]
    digest = selected["Descriptor"].get("digest", "")
    manifest = selected.get("SchemaV2Manifest") or selected.get("OCIManifest") or {}
    layers = [layer.get("digest", "") for layer in manifest.get("layers", [])]
    if (
        not DIGEST.fullmatch(digest)
        or not layers
        or not all(
            isinstance(layer, str) and DIGEST.fullmatch(layer) for layer in layers
        )
    ):
        raise ValueError("missing image digest or layer chain")
    return digest, layers


def eligible_cache(entry, cache_image, inspect=inspect_manifest):
    """Return an immutable eligible cache reference, or build without remote cache."""
    try:
        base = final_base(entry["dockerfile"], entry["buildArgs"], entry["platform"])
        _, base_layers = native_manifest(inspect(base), entry["platform"])
        digest, cached_layers = native_manifest(inspect(cache_image), entry["platform"])
        if cached_layers[: len(base_layers)] != base_layers:
            print(
                "Skipping remote Windows cache: current base chain is not its prefix."
            )
            return None
        print("Remote Windows cache contains the complete current base chain.")
        # Pin the checked publication so a moving tag cannot change what we pull.
        return f"{cache_image}@{digest}"
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        ImportError,
        subprocess.SubprocessError,
    ):
        print("Skipping remote Windows cache: base ancestry could not be established.")
        return None
