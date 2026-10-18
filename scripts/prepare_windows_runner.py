"""Opt-in removal of Visual Studio from disposable GitHub-hosted Windows runners."""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

TARGETS = (
    Path(r"C:\Program Files\Microsoft Visual Studio"),
    Path(r"C:\Program Files (x86)\Microsoft Visual Studio"),
)
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
READONLY = getattr(stat, "FILE_ATTRIBUTE_READONLY", 1)
WOF = 0x80000017
JUNCTION = 0xA0000003


def is_reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & REPARSE_POINT
    )


def validate_target(path):
    """Refuse redirected ancestors and targets before any recursive removal."""
    if path not in TARGETS or not path.is_absolute():
        raise ValueError("Cleanup target is not an allowlisted absolute path")
    for parent in [*reversed(path.parents), path]:
        try:
            info = parent.lstat()
        except FileNotFoundError:
            if parent == path:
                return False
            raise ValueError(f"Missing cleanup parent: {parent}") from None
        if is_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(
                f"Cleanup path must be an unredirected directory: {parent}"
            )
    if os.path.normcase(str(path.resolve())) != os.path.normcase(str(path)):
        raise ValueError(
            f"Cleanup target resolves outside its allowlisted path: {path}"
        )
    return True


def classify(info):
    if stat.S_ISLNK(info.st_mode):
        return "symlink"
    if is_reparse(info):
        tag = getattr(info, "st_reparse_tag", 0)
        if tag == JUNCTION:
            return "junction"
        if tag != WOF or not stat.S_ISREG(info.st_mode):
            raise ValueError("Unsupported reparse point in Visual Studio installation")
    if stat.S_ISDIR(info.st_mode):
        return "directory"
    if stat.S_ISREG(info.st_mode):
        return "file"
    raise ValueError("Unexpected file type in Visual Studio installation")


def remove_readonly(action, path):
    try:
        action(path)
    except PermissionError:
        info = path.lstat()
        # Never change permissions through a link, or suppress unrelated errors.
        if classify(info) not in ("file", "directory") or not (
            getattr(info, "st_file_attributes", 0) & READONLY
            or not info.st_mode & stat.S_IWUSR
        ):
            raise
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        action(path)


def inventory(path):
    """Inspect the whole tree first; do not descend into links or junctions."""
    pending = [path]
    while pending:
        directory = pending.pop()
        if classify(directory.lstat()) != "directory":
            raise ValueError(
                f"Cleanup directory changed during inspection: {directory}"
            )
        with os.scandir(directory) as children:
            for child in children:
                item = Path(child.path)
                info = item.lstat()
                kind = classify(info)
                if kind == "directory":
                    pending.append(item)


def remove_installation(path):
    if not validate_target(path):
        raise ValueError(f"Cleanup installation disappeared: {path}")

    def on_error(action, name, exception):
        if not isinstance(exception, PermissionError):
            raise exception
        remove_readonly(action, Path(name))

    # Python 3.12+ rmtree removes nested symlinks and Windows junctions without
    # traversing their destinations. The hosted runner supplies Python 3.12+.
    shutil.rmtree(path, onexc=on_error)


def verify_host_tools():
    for command in (
        ["docker", "--version"],
        ["git", "--version"],
        [sys.executable, "--version"],
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-Command",
            "$PSVersionTable.PSVersion.ToString()",
        ],
    ):
        subprocess.run(command, check=True, timeout=30)


def free_space(label):
    available = shutil.disk_usage(TARGETS[0].anchor).free
    print(
        f"{label}: {available} bytes free ({available / 1_000_000_000:.2f} GB)",
        flush=True,
    )
    return available


def main():
    if not (
        os.name == "nt"
        and os.environ.get("RUNNER_OS") == "Windows"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("CLEANUP_WINDOWS_RUNNER") == "true"
    ):
        print(
            "Visual Studio cleanup skipped: requires opt-in and a GitHub-hosted Windows runner."
        )
        return
    before = free_space("Before Visual Studio cleanup")
    try:
        verify_host_tools()
        # Validate all targets and their contents before removing anything.
        installations = []
        for target in TARGETS:
            if validate_target(target):
                inventory(target)
                installations.append(target)
            else:
                print(f"Visual Studio directory absent: {target}", flush=True)
        for target in installations:
            print(f"Removing Visual Studio installation: {target}", flush=True)
            initial = free_space("Before directory removal")
            remove_installation(target)
            recovered = free_space("After directory removal") - initial
            print(
                f"Recovered {recovered} bytes ({recovered / 1_000_000_000:.2f} GB)",
                flush=True,
            )
        verify_host_tools()
    finally:
        after = free_space("After Visual Studio cleanup")
        print(f"Total free-space change: {after - before} bytes", flush=True)


if __name__ == "__main__":
    main()
