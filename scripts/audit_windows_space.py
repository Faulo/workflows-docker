"""Report hosted Windows tool storage without deleting files or following links."""

import ctypes
import json
import os
import shutil
import stat
from pathlib import Path

CANDIDATES = [
    r"C:\Program Files\Microsoft Visual Studio",
    r"C:\Program Files (x86)\Microsoft Visual Studio",
    r"C:\Program Files (x86)\Windows Kits",
    r"C:\Program Files\Windows Kits",
    r"C:\Program Files\dotnet\sdk",
    r"C:\Program Files\dotnet\packs",
    r"C:\Program Files\dotnet\sdk-manifests",
    r"C:\Program Files\dotnet\templates",
    r"C:\Program Files (x86)\Android",
    r"C:\hostedtoolcache",
    r"C:\ProgramData\Package Cache",
    r"C:\ProgramData\chocolatey",
    r"C:\ProgramData\docker",
    r"C:\vcpkg",
    r"C:\tools",
    r"C:\Rustup",
    r"C:\ghcup",
    r"C:\ghc",
]


def linked(info):
    # WOF is transparent file compression, not a path redirection. The runner
    # image uses compact /EXE:LZX for Android and .NET; include those files.
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        and getattr(info, "st_reparse_tag", 0) != 0x80000017
    )


def directory_usage(root, allocated_size):
    result = {
        "path": str(root),
        "logicalBytes": 0,
        "allocatedBytes": 0,
        "hardlinkedAllocatedBytes": 0,
        "files": 0,
        "skippedLinks": 0,
        "errors": 0,
    }
    try:
        for parent in [*reversed(root.parents), root]:
            if linked(parent.lstat()):
                result["skippedLinks"] += 1
                return result
        if not stat.S_ISDIR(root.lstat().st_mode):
            result["errors"] += 1
            return result
    except FileNotFoundError:
        result["missing"] = True
        return result
    except OSError:
        result["errors"] += 1
        return result
    pending, seen = [root], set()
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    try:
                        # DirEntry.stat can return zero file IDs/link counts on
                        # Windows. os.stat retrieves the IDs for deduplication.
                        info = os.stat(entry.path, follow_symlinks=False)
                        if linked(info):
                            result["skippedLinks"] += 1
                        elif stat.S_ISDIR(info.st_mode):
                            pending.append(Path(entry.path))
                        elif stat.S_ISREG(info.st_mode):
                            identity = info.st_dev, info.st_ino
                            if identity in seen:
                                continue
                            seen.add(identity)
                            allocated = allocated_size(entry.path)
                            result["logicalBytes"] += info.st_size
                            result["allocatedBytes"] += allocated
                            result["files"] += 1
                            if info.st_nlink > 1:
                                result["hardlinkedAllocatedBytes"] += allocated
                    except OSError:
                        result["errors"] += 1
        except OSError:
            result["errors"] += 1
    return result


def main():
    if os.name != "nt" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        print("Disk audit skipped: requires a GitHub-hosted Windows runner.")
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    size = kernel.GetCompressedFileSizeW
    size.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    size.restype = ctypes.c_uint32

    def allocated_size(path):
        high = ctypes.c_uint32()
        ctypes.set_last_error(0)
        low = size(path, ctypes.byref(high))
        if low == 0xFFFFFFFF and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        return (high.value << 32) | low

    report = {"imageVersion": os.environ.get("ImageVersion"), "volumes": {}}
    for drive in ("C:\\", "D:\\"):
        if Path(drive).exists():
            usage = shutil.disk_usage(drive)
            report["volumes"][drive] = {
                "totalBytes": usage.total,
                "freeBytes": usage.free,
            }
    print(json.dumps(report), flush=True)
    print(
        "Allocated bytes are estimates; hardlinks outside a target can retain storage.",
        flush=True,
    )
    # Discover other installed products as well as the initial SDK shortlist.
    # Parent and child reports overlap; they must not be added together.
    candidates = list(CANDIDATES)
    for parent in (Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")):
        if parent.is_dir() and not linked(parent.lstat()):
            candidates.extend(
                str(child) for child in parent.iterdir() if child.is_dir()
            )
    for candidate in dict.fromkeys(candidates):
        print(json.dumps(directory_usage(Path(candidate), allocated_size)), flush=True)


if __name__ == "__main__":
    main()
