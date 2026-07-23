# Standalone product repository migration

The WRF Chammer Workbench is being separated from the upstream-near WRF fork.
The migration is deliberately staged: the existing fork remains usable until the
standalone product repository has passed its own build, installation, and real-data
acceptance tests.

## Target responsibilities

The standalone product repository owns:

- Workbench CLI, API, browser UI, and worker;
- ERA5 planning, download, cache, and provenance;
- immutable run specifications and resolved namelists;
- persistent simulations, events, cancellation, retry, and recovery;
- result postprocessing, viewer integration, and exports;
- run manifests, resources, comparison/reproduction features;
- setup, update, rollback, release manifests, and product documentation.

The WRF source tree is a runtime source dependency. It is not copied into the product
repository.

## Migration artifacts

- `migration/standalone-product.json` is the machine-readable extraction boundary.
- `migration/runtime-source-baseline.json` records the exact runtime source baseline.
- `migration/standalone-root/README.md` becomes the root README of the product repository.
- `migration/export-standalone-product.py` performs the history-preserving export.
- `ci/verify-standalone-product-extraction.py` verifies source and exported boundaries.
- `migration/audit-wrf-runtime-diff.py` identifies non-product fork changes that remain
  relative to a selected upstream WRF commit.
- `runtime/release-manifest.schema.json` defines digest-pinned runtime delivery.

## Prerequisite

The history rewrite uses `git-filter-repo`. Install it using a trusted operating-system
package, `pipx`, or the documented upstream installation method. The network-free plan
and boundary checks do not require it.

## Review the exact export plan

From a clean checkout:

```bash
python3 migration/export-standalone-product.py \
  ../wrf-chammer-workbench \
  --plan
```

The plan contains the resolved full source commit, retained paths, root overlays,
fork-only files removed after filtering, intended destination, and optional target
remote. No repository is modified in plan mode.

## Create and verify a local export

```bash
python3 migration/export-standalone-product.py \
  ../wrf-chammer-workbench
```

This creates a history-preserving product repository and writes
`migration-report.json`. The exporter rejects:

- WRF core roots such as `phys`, `dyn_em`, `main`, or `share`;
- unexpected top-level files;
- missing Workbench and runtime-release entrypoints;
- source revisions older than the migration baseline;
- unsafe destinations that overlap the source checkout;
- known direct dependencies on the full WRF fork.

Fork-only WRF/WPS build workflows and the extraction workflow itself are removed in a
separate, visible migration commit. Product CI and file history remain.

## Runtime delivery after separation

The normal product CLI no longer compiles WRF or WPS. A release manifest binds the
exact product revision to WPS, WRF, and postprocessing image digests:

```bash
python3 wrf-chammer images pull \
  --manifest /path/to/release-manifest.json
```

The product repository can therefore be created before the first public images exist.
A usable end-user release, however, must not be announced until its real GHCR images
have been built, tested, published, and written into a release manifest. The bundled
example manifest contains deliberately unusable zero digests.

Source builds remain in the runtime source repository as explicit developer/audit
operations; they are not an automatic fallback in the installed product.

## Push to the target repository

The target repository must already exist and be empty:

```bash
python3 migration/export-standalone-product.py \
  ../wrf-chammer-workbench \
  --target-url git@github.com:carstenartur/wrf-chammer-workbench.git \
  --push
```

Creating repositories is a separate GitHub administrative operation; the exporter
intentionally never creates or deletes remote repositories.

## Audit whether a WRF fork remains necessary

Fetch or add the official WRF upstream remote and choose an exact upstream commit:

```bash
python3 migration/audit-wrf-runtime-diff.py \
  --upstream-ref upstream/master \
  --fork-ref master \
  --output workbench-runs/migration/wrf-runtime-diff.json
```

The audit excludes all product paths. Every remaining change must be classified as a
required scientific WRF-core change, portable build fix, upstream candidate, or
obsolete fork residue. An empty remaining diff means a permanent WRF fork is not
required; a non-empty diff still requires item-by-item review.

## Issue and history migration

Git history for retained files is rewritten into the target repository. GitHub issues,
pull requests, releases, package visibility, and repository settings are not Git
objects and require a separate migration step.

Until issue migration is complete:

- the current fork remains the authoritative issue tracker;
- new product issues reference the standalone migration issue;
- closed implementation history remains available in the fork;
- no existing issue is deleted merely because its code moved.

## Acceptance before reducing the fork

Do not remove product paths from the current fork until:

- standalone CI is green;
- setup works on a fresh supported Linux environment;
- digest-pinned runtime images can be pulled anonymously or through documented access;
- `doctor` verifies the installed stack;
- one complete real Xaver run succeeds through the standard worker path;
- run manifest, resources, result viewer, and exports are verified in the new repository;
- rollback to the previous product release is tested.

The migration tooling does not claim that public images or the real Xaver acceptance
already exist.
