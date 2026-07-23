#!/usr/bin/env python3
"""Static contract tests for the manual GHCR runtime release workflow."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-runtime-release.yml"


def require(text: str, value: str) -> None:
    if value not in text:
        raise AssertionError(f"Runtime release workflow is missing: {value}")


def main() -> int:
    text = WORKFLOW.read_text(encoding="utf-8")

    require(text, "  workflow_dispatch:")
    if "pull_request:" in text or "push:" in text:
        raise AssertionError("Runtime publication must remain manual-only")

    require(text, "packages: write")
    require(text, "contents: write")
    require(text, "cancel-in-progress: false")
    require(text, "product_source_revision")
    require(text, "publish:")

    for dockerfile in (
        "Dockerfile",
        "Dockerfile.wps",
        "Dockerfile.era5",
        "Dockerfile.postprocessing",
    ):
        require(text, dockerfile)
    require(text, "/usr/local/bin/smoke-test-wrf.sh")
    require(text, "ci/test-era5-wps-integration.sh")
    require(text, "run-postprocessing-step.py --help")

    for image in (
        "wrf-chammer-wrf:${RELEASE}",
        "wrf-chammer-wps:${RELEASE}",
        "wrf-chammer-postprocessing:${RELEASE}",
    ):
        require(text, image)

    for remote in ("wrf_remote=", "wps_remote=", "postprocessing_remote="):
        require(text, remote)
    require(text, 'for remote_image in "${wrf_remote}" "${wps_remote}" "${postprocessing_remote}"')
    require(text, "docker manifest inspect")
    require(text, "Refusing to overwrite existing immutable runtime tag")
    require(text, "docker push")
    if text.index("for remote_image in") > text.index('docker push "${remote_image}"'):
        raise AssertionError("All immutable tags must be preflighted before the first push")

    require(text, ".RepoDigests")
    require(text, "^sha256:[0-9a-f]{64}$")
    require(text, "release-manifest.json")
    require(text, "load_release_manifest")
    require(text, "sha256sum")
    require(text, "actions/upload-artifact@v4")

    generated_manifest_region = text.split(
        "- name: Generate and validate installable runtime manifest", 1
    )[1]
    if ":latest" in generated_manifest_region:
        raise AssertionError("Generated installable manifest must not use latest tags")
    if "0000000000000000000000000000000000000000000000000000000000000000" in text:
        raise AssertionError("Publishing workflow must not emit placeholder digests")

    print("Runtime release publishing workflow contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
