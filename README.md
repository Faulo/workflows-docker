# Docker building and publishing workflow

Version 2 keeps each consumer's `.github/workflows/docker.yml` identical and puts
project configuration in JSON. This repository owns configuration validation,
native builds, caching, registry login, publication, manifest verification, and
temporary tag cleanup. Project tests run in Jenkins. Image preparation belongs in
the project's Dockerfile, including compilation in intermediate stages.

Use `v2` for compatible updates, or a version tag / full commit SHA for an immutable
reference. The existing `publish.yml` interface remains available for v1 consumers;
its documentation is in [docs/v1.md](docs/v1.md).

## Identical consumer workflow

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
    uses: Faulo/workflows-docker/.github/workflows/docker.yml@v2
    with:
      config: docker.json
    secrets:
      DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}
      DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}
```

`config` is a required caller-relative file path. The workflow does not assume a
fixed configuration location. The fleet uses `docker.json` to keep callers
identical. This baseline builds on every push to `main`, on manual dispatch, and
monthly. There are no project-specific trigger filters or test jobs.

Consumers supply Docker Hub login credentials and a token with push and temporary
tag deletion permission. The publication namespace is configured independently of
the login username, including organization namespaces.

## Configuration

The [JSON Schema](docker.schema.json) describes the configuration. Configuration
schema version `1` is the first schema for workflow major version `2`.

```json
{
  "schemaVersion": 1,
  "namespace": "$DOCKER_NAMESPACE",
  "image": "$DOCKER_IMAGE",
  "context": ".",
  "targets": [
    {
      "id": "linux-amd64",
      "dockerfile": "linux/Dockerfile",
      "runner": "ubuntu-latest"
    },
    {
      "id": "windows-2019",
      "dockerfile": "windows/Dockerfile",
      "runner": "windows-latest",
      "buildArgs": {"OS_BASE": "ltsc2019"}
    }
  ],
  "variants": [{"tag": "latest"}]
}
```

| Field | Meaning |
| --- | --- |
| `namespace` | Docker Hub publication namespace; usually `$DOCKER_NAMESPACE` |
| `image` | Repository name without its namespace; usually `$DOCKER_IMAGE` |
| `envFile` | Optional caller-relative dotenv path; defaults to `.env` when present |
| `context` | Default build context, relative to the consumer root; defaults to `.` |
| `buildArgs` | Optional build arguments shared by all product variants |
| `targets` | Default targets for variants that do not define their own |
| `variants` | Product variants, each with a required public `tag` |
| Variant `aliases` | Additional public tags pointing to the same final index |
| Variant `buildArgs` | Overrides for the shared build arguments |
| Variant `targets` | Replaces the default targets for this product variant |

| Target field | Meaning |
| --- | --- |
| `id` | Unique lowercase identifier within the product variant |
| `dockerfile` | Required Dockerfile path relative to the consumer root |
| `context` | Optional override of the default build context |
| `buildArgs` | Final overrides of global and product build arguments |
| `runner` | Required runner string or label array; passed unchanged into `runs-on` |
| `timeoutMinutes` | Entire target job timeout, 180 by default; between 1 and 360 |

JSON string values support `$NAME` and `${NAME}` substitution. The configuration
job reads the consumer's `.env` when present; `envFile` can select another file.
An explicit `envFile` must exist. Process environment values override dotenv
values. Missing variables fail configuration validation. Substitution happens
once after JSON parsing, so quotes/backslashes in values cannot break JSON.
Use `$$` for a literal dollar sign. Dotenv values are literal: shell commands and
references inside them are never executed or recursively expanded. Quoted values,
escaped quotes, `export` assignments, and comments are supported; multiline
assignments are not supported. The schema checks raw structure; resolved names
and semantic constraints are checked by the planner.

Targets do not declare their OS, architecture, Windows release, or isolation.
Runner labels are not interpreted or rewritten. At runtime, the workflow checks
the runner OS/architecture against the Docker daemon and builds natively for
Linux amd64/arm64 or Windows amd64. Image metadata supplies the Windows OS version.
Build arguments and Dockerfiles select the image's LTSC release.

The fleet examples use `ubuntu-latest`, `ubuntu-24.04-arm`, and `windows-latest`.
Custom runner strings or label arrays work unchanged and require Python plus a
working native Docker daemon. Linux runners must support Buildx and GitHub Actions
cache access. Windows runners need Hyper-V isolation support. All Windows builds
use `--isolation=hyperv`, allowing LTSC 2019 and 2022 images on a compatible newer
host; the workflow does not choose a host based on the container release.

One matrix covers all targets, including Windows-only and Linux-only projects.
Each target gets one job containing all its product variants, with at most 256
target jobs and four concurrent jobs. Repeated target IDs across variants must
use the same runner and timeout; their Dockerfiles, contexts, and build arguments
can differ. Linux Dockerfiles must
support the runner's native architecture; choosing an arm64 runner does not make
an x64 application portable.

Build argument precedence is global, then variant, then target. Values are
single-line strings, numbers, or booleans. Credentials belong in secrets, not
build arguments. Unknown properties, invalid tags, duplicate public tags,
duplicate target ids, and missing contexts/Dockerfiles fail before builds start.
The schema handles structural validation; the planner also checks semantic
constraints and the filesystem.

## Product variants and Windows releases

Product variants select the image's contents. Platforms select architecture and
operating system compatibility underneath a product tag.

- Farah's PHP releases are product variants: `7.4`, `8.0` through `8.5`. Only `8.5`
  declares the `latest` alias. Each variant builds Linux amd64/arm64 and Windows
  amd64.
- Choco's `windows` and `windowsservercore` families are product variants. Each
  contains Windows amd64 builds for LTSC 2019 and 2022. The proposed default is
  `windows`, which declares `latest`; change that alias to select Server Core.
- Ordinary projects have one product variant tagged `latest`.

Different Windows base families must not share one product tag. A manifest cannot
reliably choose full Windows versus Server Core when both match the same platform
and OS release. After builds complete, targets with the same platform and Windows
OS build prefix within one product variant are rejected before any public tag moves.

Every product variant publishes only its declared tag and aliases. No `-linux`,
`-windows`, architecture, or LTSC compatibility tags are created. Existing
downstream Dockerfiles and Jenkins configurations referencing those old tags must
be migrated. A Windows release is selected from the manifest by the runtime's
platform matching; applications that need an exact base can pin an image digest.

Proposed configurations for all nine consumers are in [examples/fleet](examples/fleet).
They preserve the currently requested architecture coverage; only Farah currently
requests arm64. Choco's migration additionally requires moving SHELL32 proxy
generation into Dockerfile stages before adopting the thin caller.

## Publication behavior

Linux target jobs generate one Buildx Bake definition for all their variants.
They share one builder, publish by digest, use GitHub Actions layer caching, and
include provenance and SBOM attestations. Every variant imports the target's
sibling caches, while each owns a distinct cache export scope. BuildKit executes
at most two build operations concurrently per runner. Identical compiler stages
and common layers can be reused within a target when their inputs match.

Windows target jobs build variants sequentially on one Docker daemon by default,
retaining intermediate compiler stages locally. They use the previous product
tag as a best-effort cache across runs and push to run-specific staging tags.
Final-image caching does not retain separate compiler stages across fresh runners.
Windows platform metadata and a nonempty OS version are checked before pushing.

Windows builds use the existing Docker service supplied by `windows-latest`.
The workflow starts that service if needed and uses classic `docker build` with
Hyper-V isolation. It does not install containerd, BuildKit, or networking plugins.

For builder validation, workflow input `publish: false` builds disposable `tmp/`
images and skips registry login, image pushes, public tag updates, and publication cleanup. Bake
exports local OCI archives and verifies their platform metadata. Classic Windows
builds retain local images and verify their metadata. Both paths upload metadata
records, including labels for unpublished builds. No registry secrets are needed
when publication is disabled. Publication still requires both Docker Hub secrets.
This validates image
construction; project tests continue to run outside this pipeline.

## Unpublished workflow fixture

[examples/test-image](examples/test-image) contains a small image with a shared
`AS build` stage and two product tags, `1.0` and `2.0`. Each tag builds Linux
amd64, Linux arm64, Windows LTSC 2019, and Windows LTSC 2022: eight images in four
target jobs. Linux uses Alpine; Windows uses NanoServer on `windows-latest`.
The fixture exercises an explicit configuration path, dotenv substitution,
global/variant/target build arguments, native architectures, shared stages, and
Windows release selection.

Run the **Test image** workflow manually, or push changes to its tracked workflow,
scripts, or fixture paths. It calls the same reusable workflow with
`publish: false`, without registry secrets. A final verification job checks all
eight metadata records, their native platforms, Windows OS versions, and labels
carrying the selected variant and substituted arguments. Images remain on the
disposable runners; only metadata and build diagnostics become artifacts.
Registry publication and manifest assembly remain covered separately by the
publisher tests.

Build results and the publication plan are passed between jobs as short-lived
artifacts. All configured builds must succeed before public tags move. The
publisher inspects every immutable source digest and dry-runs every product index
before publication. It preserves Linux attestations and Windows OS metadata,
then verifies each public tag against the expected image digests and platforms.
Aliases must reference the same final index digest as their product tag. A
publication summary lists the resulting tags, index digests, and image platforms.

Windows staging tags begin with reserved `wf-v2-` names and are deleted after
publication or a build failure, including earlier attempts of the same run. Because
runner labels are opaque, cleanup checks reserved staging names for all targets;
Linux names return 404. A
cleanup failure is reported as a workflow failure. Runner loss or cancellation
can interrupt cleanup, and untagged image blobs remain subject to Docker Hub's
retention policy. Cleanup never deletes historical or configured public tags.

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

## Development checks

The implementation uses Python's standard library on runners. Run its focused
configuration, publication, and cleanup tests locally:

```powershell
python -m unittest discover -s tests -v
```

Lint Python with Ruff. Lint the new reusable workflow and canonical caller with
actionlint. Version 1.7.12 does not yet recognize the documented
`job.workflow_repository` and `job.workflow_sha` properties; suppress only those
two diagnostics:

```powershell
actionlint -ignore 'property "workflow_(repository|sha)" is not defined' .github/workflows/docker.yml examples/docker.yml
```

Schema validation can additionally use `jsonschema` during development. Actual
native builds and publication require compatible runners and registry secrets;
local unit tests do not establish end-to-end publication coverage.
