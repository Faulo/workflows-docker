"""Generic configurations for planner and builder tests."""


def matrix_example():
    return {
        "schemaVersion": 3,
        "namespace": "example",
        "image": "app",
        "matrix": {
            "version": {
                "buildArgs": {"APP_VERSION": "{{matrix.version}}"},
                "tags": {"1.0": {}, "2.0": {"aliases": ["latest"]}},
            },
            "os": {
                "dockerfile": "{{matrix.os}}/Dockerfile",
                "runner": "{{matrix.os}}-latest",
                "tags": {
                    "linux": {
                        "matrix": {
                            "architecture": {
                                "tags": {
                                    "amd64": {"runner": "ubuntu-latest"},
                                    "arm64": {"runner": "ubuntu-24.04-arm"},
                                }
                            }
                        }
                    },
                    "windows": {
                        "matrix": {
                            "release": {
                                "buildArgs": {"OS_BASE": "{{matrix.release}}"},
                                "tags": {"ltsc2019": {}, "ltsc2022": {}},
                            }
                        }
                    },
                },
            },
        },
    }


def example(kind="multi-platform"):
    config = {
        "schemaVersion": 2,
        "namespace": "example",
        "image": "app",
        "context": ".",
    }
    windows_target = {
        "tag": "windows-2019",
        "dockerfile": "windows/Dockerfile",
        "runner": "windows-latest",
    }
    if kind == "multi-platform":
        config["targets"] = [
            {
                "tag": "linux-amd64",
                "dockerfile": "linux/Dockerfile",
                "runner": "ubuntu-latest",
            },
            {
                "tag": "linux-arm64",
                "dockerfile": "linux/Dockerfile",
                "runner": "ubuntu-24.04-arm",
            },
            windows_target,
        ]
        config["variants"] = [
            {"tag": f"1.{minor}", "buildArgs": {"APP_VERSION": f"1.{minor}"}}
            for minor in range(7)
        ]
        config["variants"][-1]["aliases"] = ["latest"]
    elif kind == "windows-families":
        config["variants"] = []
        for family in ("windows", "windowsservercore"):
            base_images = (
                {2019: "windows", 2022: "windows/server"}
                if family == "windows"
                else {2019: "windows/servercore", 2022: "windows/servercore"}
            )
            variant = {
                "tag": family,
                "targets": [
                    {
                        **windows_target,
                        "tag": f"windows-{release}",
                        "buildArgs": {
                            "OS_BASE": f"ltsc{release}",
                            "BASE_IMAGE": (
                                f"mcr.microsoft.com/{base_images[release]}:ltsc{release}"
                            ),
                        },
                    }
                    for release in (2019, 2022)
                ],
            }
            if family == "windows":
                variant["aliases"] = ["latest"]
            config["variants"].append(variant)
    elif kind == "windows-only":
        config["targets"] = [windows_target]
        config["variants"] = [{"tag": "latest"}]
    else:
        raise ValueError(f"Unknown fixture: {kind}")
    return config
