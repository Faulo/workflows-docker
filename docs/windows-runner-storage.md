# Windows runner storage investigation

Measured on 2026-10-18 using the read-only
[disk audit](https://github.com/Faulo/workflows-docker/actions/runs/39162365352),
commit `cef6ea1a298978214036119dd542aba6ae446691`. The runner was
`windows-2025-vs2026`, image version `20261007.264.1`, matching the image version
in the failing Unreal build. [Raw measurements](windows-runner-storage.json)
retain exact byte counts, file counts, hardlink counts, and scan errors.

All GB figures below are decimal (1 GB = 1,000,000,000 bytes).

## Actual available capacity

| Volume | Capacity | Initially free |
| --- | ---: | ---: |
| `C:` | 160.47 GB | 30.37 GB |
| `D:` | 236.21 GB | 236.10 GB |

The failed build exhausted `C:\ProgramData\docker`, although its repository
checkout was on `D:`. The documented standard-runner storage specification is
14 GB, not a reliable estimate of the actual capacity of either volume in this
particular image. These measured capacities are a snapshot, not a guarantee for
future runners.

## Initial host development-tool shortlist

| Directory | Logical file size | Stored file size |
| --- | ---: | ---: |
| `C:\Program Files\Microsoft Visual Studio` | 33.08 GB | 33.08 GB |
| `C:\Program Files (x86)\Microsoft Visual Studio` | 0.68 GB | 0.68 GB |
| `C:\Program Files (x86)\Windows Kits` | 2.62 GB | 2.62 GB |
| `C:\Program Files\dotnet\sdk` | 4.53 GB | 1.84 GB |
| `C:\Program Files\dotnet\packs` | 6.23 GB | 2.14 GB |
| `C:\Program Files\dotnet\sdk-manifests` and `templates` | 0.07 GB | 0.07 GB |
| `C:\Program Files (x86)\Android` | 14.72 GB | 6.03 GB |
| **Total (without overlapping parents)** | **61.95 GB** | **46.47 GB** |

These targets had zero scan errors and no reported hardlinked files. Four
redirecting entries inside Visual Studio were skipped; their destinations are
excluded. Removing all measured files in this shortlist would suggest roughly
76.84 GB free on `C:`. This is an estimate, not a measured deletion result or an
end-to-end build capacity requirement.

GitHub compresses Android and .NET files with LZX during image preparation.
Counting logical lengths would substantially overstate potential recovery.
`C:\Android\android-sdk` is a symbolic link to the Android installation above;
it is not another independent source of space.

## Other measured development tools

These are additional, disjoint candidates for later evaluation, not a reason to
delete entire Program Files trees:

| Directory | Stored file size |
| --- | ---: |
| `C:\Program Files (x86)\Microsoft SDKs` | 5.73 GB |
| `C:\ghcup` (Haskell tools) | 4.54 GB |
| `C:\Program Files\Azure Cosmos DB Emulator` | 2.62 GB |
| `C:\Program Files\LLVM` | 2.54 GB |
| `C:\Program Files\MongoDB` | 2.08 GB |
| `C:\Program Files\PostgreSQL` | 1.06 GB |

The hosted tool cache is another 5.13 GB, but contains the Python needed by the
workflow and must not be removed wholesale. The Chocolatey installation and
package cache occupy 3.06 GB and 1.75 GB respectively; dependencies and running
tools must be checked before considering either for removal.

## Consumer dependencies

The nine sibling repositories inspected were `docker-choco`, `docker-ci-tools`,
`docker-compose-unity`, `docker-farah`, `docker-godot`, `docker-jenkins-agent`,
`docker-unity`, `docker-unreal`, and `docker-unreal-ddc`.

Their production `.github/workflows/docker.yml` callers delegate directly to the
shared v3 workflow. Their Dockerfiles get SDKs and compilers from image stages or
install them inside containers. `COPY` inputs come from the repository or image
stages, not the runner's installed development tools. No host SDK mounts were
found. For example, .NET launchers build in SDK image stages; the Jenkins agent's
Windows stage downloads its own Java toolchain; Unreal installs its Visual Studio
and Windows SDK dependencies inside its final image.

This supports targeted removal of unused host development installations in these
disposable build jobs. It does not justify deleting everything except Docker:
the workflow also needs Git, Python and its dependencies, PowerShell, the Actions
runner and its bundled runtimes, Docker CLI/daemon, and Windows container and
Hyper-V services. Preserve shared host runtimes and Windows components. Custom
consumer jobs and self-hosted runners have a separate dependency contract.

## Limits and next validation

The opt-in implementation was subsequently validated in
[fixture run 39167173231](https://github.com/Faulo/workflows-docker/actions/runs/39167173231)
at commit `ac9bf0179a7572d05be63948429b4ff046950a93`, on the same runner image
version. Both Windows target jobs removed only the two Visual Studio directories:

| Target job | Free before | Free after | Actual increase |
| --- | ---: | ---: | ---: |
| Windows LTSC 2019 | 30.59 GB | 64.56 GB | 33.97 GB |
| Windows LTSC 2022 | 33.45 GB | 67.42 GB | 33.97 GB |

The operation took about two minutes per job, including path/content inspection
and host-tool checks. Docker, Git, Python, and PowerShell checks passed after
removal. All eight fixture images built and verified; Linux skipped cleanup.
The actual increase differs from the initial file-size estimate because it also
reflects filesystem allocation and concurrent runner activity.

The audit uses `GetCompressedFileSizeW` and includes WOF-compressed files while
skipping redirecting reparse points. File identities are deduplicated within each
target. File sizes are not a deletion experiment: cluster allocation, filesystem
metadata, outside hardlinks, and concurrent runner activity can affect recovered
free space. The broad scan had one access error in Windows Defender Advanced
Threat Protection, which is outside the shortlist and should be preserved.

The original read-only audit deleted no tools; the subsequent fixture run performed
the opt-in Visual Studio removal described above. Neither operation pruned Docker
data. The full Unreal build still needs its own retry. Record peak build storage
separately; a 25.66 GB resulting image is not a peak-space budget. Docker's existing
storage location remains unchanged, without a dependency on `D:` capacity.
