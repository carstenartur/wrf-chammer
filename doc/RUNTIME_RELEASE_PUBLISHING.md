# Publishing WRF Chammer runtime releases

This document applies to the WRF runtime source repository, not to the standalone
product repository. The product consumes published manifests and images; it does not
compile or publish WRF itself.

## Manual workflow

Use the GitHub Actions workflow **Publish Runtime Release**. It is intentionally
`workflow_dispatch` only.

Required inputs:

- `release` — immutable human-readable runtime release identifier;
- `product_source_revision` — full commit of the standalone Workbench release that
  will consume these images;
- `publish` — false performs builds and tests only; true additionally pushes to GHCR;
- `github_release_tag` — optional existing release that receives the final manifest.

## Build and acceptance sequence

The workflow performs these steps before any image is published:

1. build the WRF image from the selected runtime-source commit;
2. verify its runtime libraries and execute the WRF smoke simulation;
3. build and verify the WPS base image;
4. build the final WPS/ERA5 execution image;
5. execute the bundled GRIB → ungrib → metgrid integration test;
6. build and verify the postprocessing execution image;
7. optionally authenticate to GHCR and push the exact tested image objects;
8. read the registry digests returned after each push;
9. generate and validate the installable runtime release manifest;
10. upload build provenance and, when requested, attach the manifest to an existing
    GitHub release.

The final images are:

```text
ghcr.io/carstenartur/wrf-chammer-wrf:<release>
ghcr.io/carstenartur/wrf-chammer-wps:<release>
ghcr.io/carstenartur/wrf-chammer-postprocessing:<release>
```

The WPS image in the manifest is the final ERA5/WPS step-execution image, not merely
the intermediate WPS compiler/runtime base.

## Publication and visibility

GitHub Packages may create new container packages as private. Before advertising a
public installer, verify the package visibility and anonymous pull behavior for every
final image. Do not assume that a successful authenticated workflow means an anonymous
user can pull the image.

## Manifest identity

The generated manifest contains:

- the exact standalone product source revision supplied as input;
- the release label;
- all three registry references;
- all three registry SHA-256 digests.

The workflow never derives reproducible identity from `latest`, never emits placeholder
digests, and never writes a manifest before the pushes have returned verifiable
`RepoDigests`.

## Rollback

A rollback uses a previously published release manifest. It does not mutate an old tag
or replace an old digest. The product explicitly pulls and activates the older manifest.

## Remaining release acceptance

Publishing images does not close the real-Xaver acceptance issue. A release becomes
scientifically demonstrated only after the standard persistent worker completes a real
ERA5/WPS/WRF/postprocessing run and the resulting provenance, resources, and weather
products have been reviewed.
