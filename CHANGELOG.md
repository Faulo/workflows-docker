# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Historical entries were reconstructed from Git history. Dates use annotated tag
timestamps where available and tagged commit dates for lightweight tags. The
moving `v1` and `v2` tags are release aliases and have no separate entries.

## [Unreleased]

### Added

- Weekly Dependabot updates for SHA-pinned GitHub Actions, grouped into one pull
  request.
- Pull request validation using the eight unpublished example images.
- This changelog, covering all published versioned tags.

### Changed

- Document the public workflow interface with generic examples and an explicit
  workflow input reference.
- Use generic configurations in planner and builder tests.

### Removed

- Project-specific configuration examples and references to external consumers.

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

[Unreleased]: https://github.com/Faulo/workflows-docker/compare/v2.1.0...HEAD
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
