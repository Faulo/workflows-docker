# Docker Publishing Workflow

Reusable GitHub Actions workflow for building matching Linux and Windows Docker
images, publishing their platform tags to Docker Hub, and merging them into one
multi-platform tag.

## Usage

Create `.github/workflows/docker.yml` in the calling repository:

```yaml
name: Docker

on:
    push:
        branches:
            - main
        paths:
            - "linux/**"
            - "windows/**"
            - ".github/workflows/docker.yml"
    workflow_dispatch:
    schedule:
        - cron: "0 0 1 * *"

permissions:
    contents: read

concurrency:
    group: docker-publish
    cancel-in-progress: false

jobs:
    configuration:
        runs-on: ubuntu-24.04
        outputs:
            image: ${{steps.image.outputs.DOCKER_IMAGE}}
        steps:
            - name: Checkout
              uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
            - name: Read image name
              id: image
              shell: bash
              run: grep -m1 '^DOCKER_IMAGE=' .env >> "${GITHUB_OUTPUT}"

    publish:
        needs: configuration
        strategy:
            fail-fast: false
            matrix:
                variant:
                    - tag: "8.2"
                      build_args:
                          PHP_VERSION: "8.2"
                          DEBIAN: bookworm
                    - tag: "8.3"
                      build_args:
                          PHP_VERSION: "8.3"
                          DEBIAN: bookworm

        uses: Faulo/workflows-docker/.github/workflows/publish.yml@v1
        with:
            image: ${{needs.configuration.outputs.image}}
            tag: ${{matrix.variant.tag}}
            build_args: ${{toJSON(matrix.variant.build_args)}}
            linux_platforms: >-
                {
                  "linux/amd64":"ubuntu-24.04",
                  "linux/arm64":"ubuntu-24.04-arm"
                }
        secrets:
            DOCKERHUB_USERNAME: ${{secrets.DOCKERHUB_USERNAME}}
            DOCKERHUB_TOKEN: ${{secrets.DOCKERHUB_TOKEN}}
```

Each matrix entry runs a Linux build and a Windows build in parallel. Given
`DOCKER_IMAGE=farah` in `.env` and `tag: 8.2`, the workflow publishes:

```text
<DOCKERHUB_USERNAME>/farah:8.2-linux-amd64
<DOCKERHUB_USERNAME>/farah:8.2-linux-arm64
<DOCKERHUB_USERNAME>/farah:8.2-windows
<DOCKERHUB_USERNAME>/farah:8.2
```

For an image without variants, keep the configuration job and call the workflow
once. The tag defaults to `latest`:

```yaml
publish:
    needs: configuration
    uses: Faulo/workflows-docker/.github/workflows/publish.yml@v1
    with:
        image: ${{needs.configuration.outputs.image}}
    secrets:
        DOCKERHUB_USERNAME: ${{secrets.DOCKERHUB_USERNAME}}
        DOCKERHUB_TOKEN: ${{secrets.DOCKERHUB_TOKEN}}
```

To publish multiple Windows base variants in the same merged tag, pass a JSON
array through `windows_variants`. Each entry gets its own cache and published
platform tag:

```yaml
publish:
    needs: configuration
    uses: Faulo/workflows-docker/.github/workflows/publish.yml@v1
    with:
        image: ${{needs.configuration.outputs.image}}
        windows_variants: >-
            [
              {"tag":"windows-1809","build_args":{"OS_BASE":"1809"}},
              {"tag":"windows-20H2","build_args":{"OS_BASE":"20H2"}}
            ]
    secrets:
        DOCKERHUB_USERNAME: ${{secrets.DOCKERHUB_USERNAME}}
        DOCKERHUB_TOKEN: ${{secrets.DOCKERHUB_TOKEN}}
```

This example publishes `latest-windows-1809` and `latest-windows-20H2`, then
places both Windows manifests alongside `latest-linux` in `latest`.

Platform inputs are JSON objects mapping each Docker platform to the native
GitHub Actions runner that builds it. Pass multiple entries through
`linux_platforms` to publish more than the default `linux/amd64` platform:

```yaml
with:
    image: example
    linux_platforms: >-
        {
          "linux/amd64":"ubuntu-24.04",
          "linux/arm64":"ubuntu-24.04-arm"
        }
```

Windows base variants and platforms are independent dimensions. Every platform
in `windows_platforms` is built for every entry in `windows_variants`. Microsoft
currently publishes Windows container base images for amd64 only:

```yaml
with:
    image: example
    windows_platforms: '{"windows/amd64":"windows-2022"}'
```

Linux platform tags include the architecture when several targets are requested,
for example `latest-linux-amd64` and `latest-linux-arm64`. With only one target,
the architecture suffix is omitted for backward compatibility.

## Requirements

The calling repository must define these GitHub Actions secrets:

- `DOCKERHUB_USERNAME`: Docker Hub username and image namespace.
- `DOCKERHUB_TOKEN`: Docker Hub access token with push permission.

The calling workflow must pass the Docker Hub repository name through `image`.
The example reads it from the caller's root `.env`:

```dotenv
DOCKER_IMAGE=example
```

By default, the workflow expects:

```text
linux/Dockerfile
windows/Dockerfile
```

The Linux image is built for `linux/amd64` on `ubuntu-24.04` by default. The
default Windows platform is `windows/amd64`, built on the GitHub-hosted
`windows-2022` runner using Hyper-V isolation. Each additional target requires
a native runner with a working Docker daemon.

Build argument names must be valid Docker `ARG` identifiers. Values should be
JSON scalars; strings are recommended.

## Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `image` | yes | none | Docker Hub repository name without the namespace |
| `tag` | no | `latest` | Tag for the merged image |
| `build_args` | no | `{}` | JSON object containing Docker build arguments |
| `linux_platforms` | no | `{"linux/amd64":"ubuntu-24.04"}` | JSON object mapping Linux Docker platforms to native runner labels |
| `windows_variants` | no | `[{"tag":"windows","build_args":{}}]` | JSON array of Windows tag suffixes and variant-specific build arguments |
| `windows_platforms` | no | `{"windows/amd64":"windows-2022"}` | JSON object mapping the Windows amd64 Docker platform to its native runner label |
| `linux_context` | no | `linux` | Linux build context |
| `linux_dockerfile` | no | `linux/Dockerfile` | Linux Dockerfile path |
| `windows_context` | no | `windows` | Windows build context |
| `windows_dockerfile` | no | `windows/Dockerfile` | Windows Dockerfile path |

Override paths when a project uses a different layout:

```yaml
with:
    image: example
    build_args: ${{toJSON(matrix.variant.build_args)}}
    linux_context: docker
    linux_dockerfile: docker/Dockerfile.linux
    windows_context: docker
    windows_dockerfile: docker/Dockerfile.windows
```

## Publication Behavior

Publication is serialized per calling repository and tag. Calling workflows
should also use workflow-level concurrency, as shown above, to queue consecutive
runs. A merged tag is updated only after the Linux build and every Windows
variant succeed. The merge job verifies that the resulting manifest contains
every requested Linux platform plus every requested Windows architecture and
the expected number of distinct Windows OS versions for each architecture.
Temporary Windows manifest-wrapper tags used during the merge are deleted after
the final manifest has been verified.

Linux targets build independently on their configured native runners, use
GitHub Actions caching, and publish provenance and SBOM attestations. The
Windows build restores its previous platform image as a Docker layer cache.

## Versioning

Use the moving major tag for compatible updates:

```yaml
uses: Faulo/workflows-docker/.github/workflows/publish.yml@v1
```

For an immutable reference, use a release tag such as `v1.0.0` or a full commit
SHA. Breaking input or behavior changes will use a new major version.
