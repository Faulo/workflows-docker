# Docker building and publishing workflow

A reusable GitHub Actions workflow for building native Linux and Windows Docker
images and publishing multi-platform tags to Docker Hub. A JSON configuration
selects runners, Dockerfiles, build arguments, image variants, and public tags.
The workflow handles validation, caching, registry authentication, manifest
publication, verification, and temporary tag cleanup.

Supported platforms are Linux amd64, Linux arm64, and Windows amd64, including
multiple Windows LTSC releases. Builds can also run without publication or
registry credentials. Image preparation and compilation belong in Dockerfile
stages; application tests run separately from this workflow.

Use `v3` for compatible updates, or a version tag / full commit SHA for an immutable
reference. The existing `publish.yml` interface remains available for v1 consumers;
its documentation is in [docs/v1.md](docs/v1.md).

See the [changelog](CHANGELOG.md) for release history and migration notes.

## Quick start

Copy [examples/docker.yml](examples/docker.yml) to `.github/workflows/docker.yml`:

```yaml
name: Docker

on:
  push:
    branches: [main]
  workflow_dispatch:
  schedule:
    - cron: "17 3 1 * *"

permissions:
  contents: read

concurrency:
  group: docker-publish
  cancel-in-progress: false

jobs:
  docker:
    uses: Faulo/workflows-docker/.github/workflows/docker.yml@v3
    with:
      config: docker.json
    secrets:
      DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}
      DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}
```

Create a `docker.json` configuration using the example below and add the two
Docker Hub secrets to your repository. The caller above runs on pushes to `main`,
manual dispatch, and a monthly schedule. Adjust its triggers for your project.

### Workflow inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `config` | yes | none | Configuration file path relative to the calling repository root |
| `publish` | no | `true` | Publish images and tags to Docker Hub; set to `false` for builds without publication |

Consumers supply Docker Hub login credentials and a token with push and temporary
tag deletion permission. The publication namespace is configured independently of
the login username, including organization namespaces.

## Configuration

Workflow v3 supports recursive matrices with configuration `schemaVersion: 3`.
The [JSON Schema](docker.schema.json) describes both this format and the legacy
schema version `2`, which remains accepted for existing configurations. The
previous variants/targets interface is documented in [docs/schema-v2.md](docs/schema-v2.md).

```json
{
  "schemaVersion": 3,
  "namespace": "$DOCKER_NAMESPACE",
  "image": "$DOCKER_IMAGE",
  "description": "Native Linux and Windows images for your application",
  "overviewFile": "README.md",
  "context": ".",
  "matrix": {
    "version": {
      "buildArgs": {"APP_VERSION": "{{matrix.version}}"},
      "tags": {
        "1.0": {},
        "2.0": {"aliases": ["latest"]}
      }
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
                "arm64": {"runner": "ubuntu-24.04-arm"}
              }
            }
          }
        },
        "windows": {
          "matrix": {
            "release": {
              "buildArgs": {"OS_BASE": "{{matrix.release}}"},
              "tags": {"ltsc2019": {}, "ltsc2022": {}}
            }
          }
        }
      }
    }
  }
}
```

For variable-based names, add a caller-relative `.env`:

```dotenv
DOCKER_NAMESPACE=your-dockerhub-namespace
DOCKER_IMAGE=your-image
```

### Recursive expansion and public tags

`matrix` is a nonempty map of labeled axes. Map declaration order defines axis
order and therefore tag order; tools editing this JSON must preserve that order.
Each axis contains a nonempty `tags` map whose keys are tag segments. Axis labels
such as `version` and `os` appear in matrix variables and build metadata, and do
not contribute to tags. Labels must be identifiers and cannot repeat on one path.

Axes multiply in order. A choice can contain another `matrix`, which expands
immediately after that choice, before subsequent outer axes. This example builds
2 versions times 4 targets: eight images in four shared target jobs.

Complete paths build images. Every prefix publishes an index containing exactly
its descendant images and their associated attestations. The example produces
`2.0`, `2.0-linux`, `2.0-linux-amd64`, `2.0-linux-arm64`, `2.0-windows`,
`2.0-windows-ltsc2019`, and `2.0-windows-ltsc2022`, as well as the corresponding
`latest` aliases. Intermediate indices and aliases add no builds.

A choice's optional `aliases` list supplies alternative segments. Parent and child
aliases multiply, and every alias for a prefix references that prefix's index
digest. Matrix variables always contain the selected primary key, even when an
alias names the published tag.

Every generated public name must be a valid Docker tag of at most 128 characters.
Duplicate names, ambiguous concatenations, duplicate JSON keys, empty matrices or
tag maps, and reserved `wf-v3-` names fail before builds start. Expansion
is limited to 256 target jobs, 4096 builds, 4096 alias combinations per prefix,
16384 publications, and the workflow's 400 KB plan budget.

### Inheritance and substitution

Build configuration can be set at the root, on an axis, or on a tag choice:

| Field | Meaning |
| --- | --- |
| `context` | Consumer-relative build context; defaults to `.` |
| `dockerfile` | Consumer-relative Dockerfile; required on each completed path |
| `runner` | Runner string or label array; required on each completed path |
| `buildArgs` | Build arguments; merged by key down the path |
| `timeoutMinutes` | Entire target job timeout, 180 by default; from 1 to 360 |

The root additionally requires `namespace`, `image`, and `schemaVersion`. Optional
`envFile` selects a caller-relative dotenv file; otherwise `.env` is read when
present. Namespace and image can instead be literal lowercase Docker Hub names.

Configuration inherits along each path. At each step, axis settings override
inherited settings, then choice settings override axis settings. Deeper values
win; `buildArgs` merge by key. Unknown properties and malformed build arguments
are rejected, including on intermediate nodes.

`{{matrix.axisLabel}}` substitutes the selected primary key. Templates resolve
once, after the completed path's configuration has been merged. This permits an
earlier field to reference a later axis, and a leaf to override an earlier runner
or path before validation. Missing matrix references and malformed expressions
in the final configuration fail before builds. Templates apply to string values,
including runner label arrays and build arguments; keys and aliases are literal.

Existing `$NAME` and `${NAME}` environment substitution still runs first on JSON
string values. Process environment values override dotenv assignments. Missing
variables fail; `$$` escapes a literal dollar. Dotenv assignments remain literal:
commands and references inside them are never executed or recursively expanded.
Explicit `envFile` paths must exist. Dotenv supports quotes, escaped quotes,
`export`, and comments, but not multiline assignments.

### Docker Hub descriptions

Optional root-level `description` and `overviewFile` fields apply to the configured
`namespace` / `image` repository. Both schema versions `2` and `3` support them;
they cannot be placed on axes, choices, variants, or targets.

| Field | Meaning |
| --- | --- |
| `description` | Single-line Docker Hub short description, at most 100 UTF-8 bytes after environment substitution |
| `overviewFile` | UTF-8 Markdown overview file, at most 25,000 UTF-8 bytes after URL resolution |

Configure either field independently. Omitting a field preserves its existing
Docker Hub value. An explicit empty `description` or empty overview file clears
the corresponding value. Oversized content fails with its size and limit;
documentation is never truncated.

Resolve `overviewFile` from the **calling repository root**, even if `docker.json`
is in a subdirectory or `context` points elsewhere. For example,
`"overviewFile": "docs/docker.md"` reads the caller's `docs/docker.md`. Absolute
paths, parent traversal, and symlinks outside the repository are rejected. The
file must exist and contain readable UTF-8 during configuration, including when
publication is disabled. Markdown contents are read literally, without dotenv
or matrix substitution.

The overview is parsed as GitHub Flavored Markdown and serialized back to
Markdown after rewriting links. Formatting and reference notation may normalize;
code contents remain literal. Embedded HTML URL attributes are handled by an HTML
parser, and destinations are resolved with URI parsing.

Relative links and images resolve from the **calling repository root**, regardless
of the overview file's directory. For example, `guide.md` in `docs/docker.md`
links to the root `guide.md`. Links use GitHub file or directory URLs; images use
raw content URLs. Both follow the repository's **default branch**, so documentation
links continue to show current files instead of being pinned to a publication
commit. Fragment-only links such as `#install` stay relative to the Docker Hub
overview. Existing absolute URLs stay absolute; protocol-relative URLs such as
`//example.com/logo.svg` become HTTPS URLs. These links require
the source repository to be accessible to the reader; private source images
cannot be displayed publicly on Docker Hub.

Configuration validates and snapshots the overview before any builds start.
After every image and public tag has been published and verified, the publication
job performs one Docker Hub metadata update with only the configured fields.
The destination is never inferred from the GitHub repository name. With
`publish: false`, no metadata authentication or updates occur and no registry
credentials are required. Metadata failures fail the workflow with a clear error;
published tags remain published, and staging cleanup still runs. Rerun the workflow
after correcting the problem. Documentation-only publication is not supported.

Metadata synchronization reuses `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`.
Use a personal access token with **Repo Read, Write, Delete** permission and an
account allowed to edit the destination repository, including for organization
namespaces. Push access alone does not establish permission to edit repository
information. The helper authenticates through `/v2/auth/token` and updates the
legacy `/v2/repositories/{namespace}/{image}/` endpoint; organization access tokens
are not supported for this integration. See Docker's
[repository information](https://docs.docker.com/docker-hub/repos/manage/information/)
and [access token reference](https://docs.docker.com/security/access-tokens/reference/).
Neither credentials nor API response bodies are included in synchronization errors.

### Native builds and compatibility

Targets do not declare OS, architecture, Windows release, or isolation. Runner
labels pass unchanged into `runs-on`. Runtime detection verifies the runner
against its native Docker daemon. Supported platforms are Linux amd64/arm64 and
Windows amd64. Windows builds use Hyper-V isolation; Dockerfiles and build
arguments choose the image release. Custom runners require Python and Docker;
Linux additionally requires Buildx and GitHub Actions cache access.

Internally, the first selected segment identifies the product variant. Remaining
segments identify the target; with only one axis, its segment also identifies
the target. Variants sharing a target run together and must use the same runner
and timeout. Dockerfiles, contexts, and arguments can differ between variants.
Build metadata records each leaf's selected matrix values.

Full Windows and Server Core families must remain under separate top-level
choices. Every prefix combines all its descendants, so placing ambiguous Windows
families beneath one prefix cannot be repaired by adding more axes. Before any
public tag moves, publication rejects duplicate OS/architecture/Windows build
prefix combinations and preserves each image's Windows OS-version metadata.

### Adopting schema version 3

Keep the workflow reference at `@v3`. Existing schema version `2` configurations
can continue unchanged. To adopt recursive matrices, set `schemaVersion` to `3`
and replace `variants` / `targets` with labeled axes and their `tags` maps. Move
variant aliases onto the corresponding first-axis choice, shared configuration
onto the root or axes, and target overrides onto their leaf choices. Use
`{{matrix.label}}` instead of repeating selected values in build arguments.

The new format publishes every intermediate prefix automatically. Review generated
names when migrating: for example, splitting `windows-ltsc2019` into OS and release
axes adds `latest-windows` alongside `latest-windows-ltsc2019`. Historical public
tags are retained, including when a matrix branch is removed. Versioned workflow tags retain their published implementation.

## Publication behavior

Linux target jobs generate one Buildx Bake definition for all their variants.
They share one builder, publish by digest, use GitHub Actions layer caching, and
include provenance and SBOM attestations. Every variant imports the target's
sibling caches, while each owns a distinct cache export scope. BuildKit executes
at most two build operations concurrently per runner. Identical compiler stages
and common layers can be reused within a target when their inputs match.

Windows target jobs build variants sequentially on one Docker daemon,
retaining intermediate compiler stages locally. They use the previous target-specific
tag as a best-effort cache across runs and push to run-specific staging tags.
Final-image caching does not retain separate compiler stages across fresh runners.
Windows platform metadata and a nonempty OS version are checked before pushing.

Windows builds use the existing Docker service supplied by `windows-latest`.
The workflow starts that service if needed and uses classic `docker build` with
Hyper-V isolation. It does not install containerd, BuildKit, or networking plugins.

Set workflow input `publish: false` to build disposable `tmp/` images without
registry login, image pushes, public tag updates, or publication cleanup. Bake
exports local OCI archives and verifies their platform metadata. Classic Windows
builds retain local images and verify their metadata. Both paths upload metadata
records, including labels for unpublished builds. No registry secrets are needed
when publication is disabled. Publication still requires both Docker Hub secrets.
This validates image construction; application tests run separately.

Build results and the publication plan are passed between jobs as short-lived
artifacts. All configured builds must succeed before public tags move. The
publisher inspects every immutable source digest and dry-runs every product index
before publication. It preserves Linux attestations and Windows OS metadata,
then verifies each public tag against the expected image digests and platforms.
Aliases must reference the same final index digest as their product tag. A
publication summary lists the resulting tags, index digests, and image platforms.

Windows staging tags begin with reserved `wf-v3-` names and are deleted after
publication or a build failure, including earlier attempts of the same run. Because
runner labels are opaque, cleanup checks reserved staging names for all targets;
Linux names return 404. A cleanup failure is reported as a workflow failure.
Runner loss or cancellation can interrupt cleanup, and untagged image blobs
remain subject to Docker Hub's retention policy. Cleanup never deletes historical
or configured public tags.

Publications are serialized per consumer repository. Caller and callee use
different concurrency groups to avoid cancelling one another. Multiple product
publications are not a registry-wide transaction: a registry failure during
publication can leave earlier products updated. Verification detects failures;
rerunning the workflow completes publication.

The workflow checks out its implementation using `job.workflow_repository` and
`job.workflow_sha`, so helpers match the exact called workflow revision, including
when a consumer pins to an immutable SHA. Caller source and workflow helpers use
separate directories, keeping helper files out of Docker build contexts. These
workflow identity contexts require GitHub.com; GitHub Enterprise Server is not
supported by this implementation.

## Example and workflow validation

[examples/test-image](examples/test-image) contains a small image with a shared
`AS build` stage and two product tags, `1.0` and `2.0`. Each tag builds Linux
amd64, Linux arm64, Windows LTSC 2019, and Windows LTSC 2022: eight images in four
target jobs. Linux uses Alpine; Windows uses NanoServer on `windows-latest`.
The fixture exercises an explicit configuration path, dotenv substitution,
inherited build arguments, native architectures, shared stages, Windows release
selection, recursive axes, matrix substitution, intermediate OS tags, and optional
Docker Hub description configuration with a caller-relative overview file.

The **Test image** workflow runs on pull requests and pushes to `main` that change
its tracked workflows, scripts, or fixture paths, and supports manual dispatch.
It calls the same reusable workflow with
`publish: false`, without registry secrets. A final verification job checks all
eight metadata records, their native platforms, Windows OS versions, and labels
carrying the selected variant and substituted arguments. Images remain on the
disposable runners; only metadata and build diagnostics become artifacts.
Registry publication and manifest assembly remain covered separately by the
publisher tests.

## Development checks

Dependabot checks GitHub Actions dependencies weekly and groups available updates
into one pull request. Action references remain pinned to commit SHAs. Updates to
the v3 workflow and its validation workflow are checked by the unpublished
example builds.

Native build and publication helpers use Python's standard library. Configuration
additionally uses pinned Markdown parser/serializer dependencies, installed only
in the configuration job. Install these before running the local tests:

```powershell
python -m pip install -r scripts/requirements-metadata.txt
python -m unittest discover -s tests -v
```

Lint Python with Ruff. Lint the reusable workflow and example caller with
actionlint. Version 1.7.12 does not yet recognize the documented
`job.workflow_repository` and `job.workflow_sha` properties; suppress only those
two diagnostics:

```powershell
actionlint -ignore 'property "workflow_(repository|sha)" is not defined' .github/workflows/docker.yml examples/docker.yml
```

Schema validation can additionally use `jsonschema` during development. Actual
native builds and publication require compatible runners and registry secrets;
local unit tests do not establish end-to-end publication coverage.
