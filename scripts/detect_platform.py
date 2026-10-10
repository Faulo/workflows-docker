"""Detect the native build platform without interpreting runner labels."""

import json
import os
import subprocess

from configure import require


def native_platform(runner_os, runner_arch, daemon):
    operating_system = {"Linux": "linux", "Windows": "windows"}.get(runner_os)
    architecture = {"X64": "amd64", "ARM64": "arm64"}.get(runner_arch)
    require(
        operating_system and architecture,
        "Only Linux amd64/arm64 and Windows amd64 runners are supported",
    )
    require(
        operating_system != "windows" or architecture == "amd64",
        "Windows container builds require an amd64 runner",
    )
    require(
        daemon.get("OSType") == operating_system,
        "Docker daemon OS differs from the runner OS",
    )
    daemon_arch = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(daemon.get("Architecture"))
    require(
        daemon_arch == architecture,
        "Docker daemon architecture differs from the runner architecture",
    )
    return f"{operating_system}/{architecture}"


def main():
    context = (
        "default"
        if os.environ["RUNNER_OS"] == "Windows"
        else os.environ.get("DOCKER_CONTEXT", "default")
    )
    result = subprocess.run(
        ["docker", "--context", context, "info", "--format", "{{json .}}"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    platform = native_platform(
        os.environ["RUNNER_OS"], os.environ["RUNNER_ARCH"], json.loads(result.stdout)
    )
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"platform={platform}\n")
    print(f"Building natively for {platform}")


if __name__ == "__main__":
    main()
