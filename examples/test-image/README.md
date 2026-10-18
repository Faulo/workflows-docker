# Workflow test image

Small Linux and Windows images used to validate the reusable Docker workflow.
The [configuration](/examples/test-image/docker.json) builds two variants across four native targets.
See the [Linux Dockerfile](/examples/test-image/linux/Dockerfile) and
[Windows Dockerfile](/examples/test-image/windows/Dockerfile) for their shared build stages.

The example caller runs with `publish: false`, so neither images nor Docker Hub
descriptions are published. With publication enabled, this file becomes the
configured Docker Hub repository's overview after all public tags are verified.
Its path in `docker.json` and documentation URLs resolve from the calling
repository root. URLs follow the default branch rather than a publication commit.
