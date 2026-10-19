# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.3.1] - 2026-10-20

### Fixed

- Reduce publication registry requests by inspecting raw manifests rather than
  recursively loading every child image and attestation for each alias. Isolate
  publication credentials from preinstalled runner registry tokens.

## [3.3.0] - 2026-10-18

### Added

- Optional root-level `cleanupWindowsRunner` boolean in both configuration
  schemas. Disabled by default; when enabled, GitHub-hosted Windows build jobs
  remove only the two allowlisted Visual Studio installation directories before
  cache pulls or builds, log free space, and verify required host tools.

### Fixed

- Skip previous Windows image cache pulls when Dockerfile `FROM` analysis and
  registry metadata cannot establish the current final base's complete ordered
  layer chain as a prefix of the cached image. Eligible caches are pulled by
  immutable digest; unavailable metadata falls back to local caching.

## [3.2.0] - 2026-10-18

### Added

- Optional root-level `description` and `overviewFile` in both configuration
  schemas. Docker Hub metadata synchronizes once after successful tag publication,
  using a validated overview snapshot with repository-root links and images that
  follow the calling repository's default branch.
- Markdown, HTML, and URI parsing for documentation URLs, with GitHub Flavored
  Markdown support and fragment-only links kept relative to the Docker Hub overview.
- Validation of metadata types, caller-relative overview files, and UTF-8 content
  limits without truncation. Unpublished builds require no metadata credentials.

## [3.1.0] - 2026-10-16

### Added

- Recursive, labeled matrix maps using configuration schema version `3` in
  workflow v3. Axis defaults, tag overrides, Cartesian expansion, nested axes,
  and `{{matrix.label}}` substitution produce builds from complete paths.
- Automatic intermediate prefix indices and propagated aliases, assembled from
  immutable leaf digests with attestations and Windows metadata preserved.
- Validation for repeated axes, unknown matrix references, duplicate JSON keys,
  expanded tag collisions, and expansion limits before builds begin.
- Matrix example builds and migration documentation. Schema version `2` remains
  accepted for existing configurations, with its interface documented separately.

## [3.0.2] - 2026-10-10

### Fixed

- Show the variant before the target in build log groups, results, and errors,
  matching the published image tag order.

## [3.0.1] - 2026-10-10

### Fixed

- Resolve the Windows staging digest from the pushed registry tag. Docker's quiet
  push output is an image reference, and cached images can retain both an image
  digest and a product-index digest locally.

## [3.0.0] - 2026-10-10

### Fixed

- Record the immutable digest returned by a Windows image push when cached images
  retain both image and index repository digests.
- Record large Linux Bake metadata without exceeding the operating system's
  environment-variable size limit.

### Added

- Target-specific image tags in the form `<variant.tag>-<target.tag>`, including
  suffixed aliases, alongside the existing combined variant tags.
- Validation of generated tag collisions and Docker's 128-character tag limit.
- Monthly Dependabot updates for SHA-pinned GitHub Actions, grouped into one pull
  request.
- Pull request validation using the eight unpublished example images.
- This changelog, covering all published versioned tags.

### Changed

- Require configuration schema version `2` and rename target `id` to `tag`.
- Pull Windows build caches from the target-specific tag to select the intended
  Windows release on hosts compatible with multiple releases.
- Document the public workflow interface with generic examples and an explicit
  workflow input reference.
- Use generic configurations in planner and builder tests.

### Removed

- Support for the target `id` configuration property. Migrate callers to `@v3`,
  set `schemaVersion` to `2`, and replace each target's `id` with `tag`.

## [2.1.0] - 2026-10-10

### Added

- A `publish: false` mode that builds and verifies images without registry
  credentials, image pushes, or public tag changes.
- An example image and validation workflow covering two variants across Linux
  amd64, Linux arm64, Windows LTSC 2019, and Windows LTSC 2022.
- Platform and label verification for unpublished builds.

### Changed

- Group all variants for a target into one job, sharing its builder and local
  cache. Targets continue to build in parallel, with up to four concurrent jobs.
- Build Linux variants through Buildx Bake with sibling cache imports, separate
  cache export scopes, and at most two concurrent BuildKit operations per runner.
- Build Windows variants sequentially using the runner's existing Docker service
  and classic builder with Hyper-V isolation. Retain intermediate stages for
  reuse across variants.
- Apply each target's timeout to its entire job and reject inconsistent runner
  or timeout settings for repeated target IDs.

## [2.0.1] - 2026-10-10

### Fixed

- Name build jobs using only the target ID and variant tag instead of displaying
  the entire build matrix entry.

## [2.0.0] - 2026-10-10

### Added

- A configuration-driven reusable `docker.yml` workflow with a required
  caller-relative JSON configuration path and a minimal example caller.
- JSON Schema and semantic validation for targets, variants, tags, build
  arguments, paths, and runner settings.
- Environment variable substitution in JSON values using `.env` or an explicit
  dotenv path, with process environment overrides and literal dollar escaping.
- A publication namespace independent of the Docker Hub login username.
- Native Linux amd64/arm64 and Windows amd64 builds, including Linux-only,
  Windows-only, and multiple Windows LTSC targets.
- Product variants with explicit public tags and aliases, plus build argument
  overrides at global, variant, and target levels.
- Publication from immutable source digests with preflight checks, preservation
  of Linux attestations and Windows OS metadata, and final tag verification.
- Run-specific Windows staging tags and cleanup after publication or failure,
  including previous attempts of the same run.
- Configuration, publication, and cleanup tests, and separate documentation for
  the existing v1 `publish.yml` interface.

### Changed

- Move v2 build configuration into JSON so callers can use the same workflow
  definition. Runner labels pass unchanged into `runs-on`.
- Check out workflow helpers at the exact called workflow revision, separately
  from the caller's Docker build contexts.
- Require every configured build to succeed before publishing public tags, and
  reject ambiguous Windows platform combinations within a product tag.

### Removed

- Automatic OS, architecture, and LTSC suffix tags from the v2 interface; only
  configured variant tags and aliases are published. The v1 interface retains
  its compatibility tags.

## [1.2.0] - 2026-09-26

### Added

- Configurable Linux and Windows platform-to-runner mappings for native builds,
  including Linux arm64.
- A `latest` alias that references the same merged image as the primary tag
  without rebuilding.
- Operating-system compatibility tags combining all Linux platforms or Windows
  variants, with corresponding `latest-*` aliases when requested.

### Changed

- Include architecture suffixes in Linux platform tags when multiple Linux
  targets are configured.
- Verify the configured platform coverage and Windows OS versions in the merged
  image manifest.

## [1.1.3] - 2026-08-16

### Fixed

- Delete temporary Windows manifest-wrapper tags after the merge job, including
  failure paths, and report cleanup errors.

## [1.1.2] - 2026-08-01

### Fixed

- Wrap Windows variant images in annotated manifests before merging, preserving
  their architecture and OS version in the final multi-platform tag.

## [1.1.1] - 2026-08-01

### Fixed

- Preserve Windows `os.version` metadata when merging platform images and verify
  that each configured Windows variant has a distinct, nonempty OS version.

## [1.1.0] - 2026-08-01

### Added

- A `windows_variants` input for building multiple Windows base variants with
  separate tags, build arguments, and caches.
- Merge all configured Windows variants alongside Linux into the requested
  multi-platform tag and verify their distinct OS versions.

## [1.0.2] - 2026-07-26

### Changed

- Require callers to supply the `image` input and resolve dotenv configuration
  in their own workflow.

### Removed

- Automatic image-name resolution from the caller's `.env` inside the reusable
  workflow.

## [1.0.1] - 2026-07-26

### Added

- Resolve the image name from `DOCKER_IMAGE` in the caller's `.env` when the
  `image` input is omitted.
- Default the public image tag to `latest`.

### Changed

- Serialize publications by calling repository and tag.

## [1.0.0] - 2026-07-26

### Added

- Initial reusable `publish.yml` workflow for building Linux amd64 and Windows
  amd64 images in parallel and publishing them to Docker Hub.
- Configurable image names, tags, build arguments, Dockerfiles, and contexts.
- Linux Buildx builds with GitHub Actions caching, provenance, and SBOM
  attestations.
- Windows builds with Hyper-V isolation and previous-image layer caching.
- Linux and Windows platform tags, a merged multi-platform tag, and manifest
  verification after both builds succeed.
- Serialized publication and usage documentation.

[Unreleased]: https://github.com/Faulo/workflows-docker/compare/v3.2.0...HEAD
[3.2.0]: https://github.com/Faulo/workflows-docker/compare/v3.1.0...v3.2.0
[3.1.0]: https://github.com/Faulo/workflows-docker/compare/v3.0.2...v3.1.0
[3.0.2]: https://github.com/Faulo/workflows-docker/compare/v3.0.1...v3.0.2
[3.0.1]: https://github.com/Faulo/workflows-docker/compare/v3.0.0...v3.0.1
[3.0.0]: https://github.com/Faulo/workflows-docker/compare/v2.1.0...v3.0.0
[2.1.0]: https://github.com/Faulo/workflows-docker/compare/v2.0.1...v2.1.0
[2.0.1]: https://github.com/Faulo/workflows-docker/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/Faulo/workflows-docker/compare/v1.2.0...v2.0.0
[1.2.0]: https://github.com/Faulo/workflows-docker/compare/v1.1.3...v1.2.0
[1.1.3]: https://github.com/Faulo/workflows-docker/compare/v1.1.2...v1.1.3
[1.1.2]: https://github.com/Faulo/workflows-docker/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/Faulo/workflows-docker/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/Faulo/workflows-docker/compare/v1.0.2...v1.1.0
[1.0.2]: https://github.com/Faulo/workflows-docker/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/Faulo/workflows-docker/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/Faulo/workflows-docker/releases/tag/v1.0.0
