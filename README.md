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
    publish:
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
            tag: ${{matrix.variant.tag}}
            build_args: ${{toJSON(matrix.variant.build_args)}}
        secrets:
            DOCKERHUB_USERNAME: ${{secrets.DOCKERHUB_USERNAME}}
            DOCKERHUB_TOKEN: ${{secrets.DOCKERHUB_TOKEN}}
```

Each matrix entry runs a Linux build and a Windows build in parallel. Given
`DOCKER_IMAGE=farah` in `.env` and `tag: 8.2`, the workflow publishes:

```text
<DOCKERHUB_USERNAME>/farah:8.2-linux
<DOCKERHUB_USERNAME>/farah:8.2-windows
<DOCKERHUB_USERNAME>/farah:8.2
```

For an image without variants, call the workflow directly. The tag defaults to
`latest`, and no `with` block is needed:

```yaml
jobs:
    publish:
        uses: Faulo/workflows-docker/.github/workflows/publish.yml@v1
        secrets:
            DOCKERHUB_USERNAME: ${{secrets.DOCKERHUB_USERNAME}}
            DOCKERHUB_TOKEN: ${{secrets.DOCKERHUB_TOKEN}}
```

## Requirements

The calling repository must define these GitHub Actions secrets:

- `DOCKERHUB_USERNAME`: Docker Hub username and image namespace.
- `DOCKERHUB_TOKEN`: Docker Hub access token with push permission.

The root `.env` must define the Docker Hub repository name:

```dotenv
DOCKER_IMAGE=example
```

The optional `image` input overrides `DOCKER_IMAGE`.

By default, the workflow expects:

```text
linux/Dockerfile
windows/Dockerfile
```

The Linux image is built for `linux/amd64`. The Windows Dockerfile must build on
the GitHub-hosted `windows-2022` runner using Hyper-V isolation.

Build argument names must be valid Docker `ARG` identifiers. Values should be
JSON scalars; strings are recommended.

## Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `image` | no | `DOCKER_IMAGE` from `.env` | Docker Hub repository name without the namespace |
| `tag` | no | `latest` | Tag for the merged image |
| `build_args` | no | `{}` | JSON object containing Docker build arguments |
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
runs. A merged tag is updated only after both platform builds succeed. The merge
job verifies that the resulting manifest contains `linux/amd64` and
`windows/amd64`.

The Linux build uses GitHub Actions caching and publishes provenance and SBOM
attestations. The Windows build restores its previous platform image as a Docker
layer cache.

## Versioning

Use the moving major tag for compatible updates:

```yaml
uses: Faulo/workflows-docker/.github/workflows/publish.yml@v1
```

For an immutable reference, use a release tag such as `v1.0.0` or a full commit
SHA. Breaking input or behavior changes will use a new major version.
