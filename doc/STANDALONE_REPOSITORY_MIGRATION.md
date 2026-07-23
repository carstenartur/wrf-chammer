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

The plan contains:

- the resolved full source commit;
- every retained path;
- the root overlay rename;
- the intended destination;
- whether a target remote or push was requested.

No repository is modified in plan mode.

## Create a local staging export

```bash
python3 migration/export-standalone-product.py \
  ../wrf-chammer-workbench \
  --allow-known-couplings
```

This creates a history-preserving staging repository and writes
`migration-report.json`. The temporary compatibility flag is required while known
full-fork runtime build paths remain. A staging export cannot be pushed by the exporter.

The export verifier rejects:

- WRF core roots such as `phys`, `dyn_em`, `main`, or `share`;
- unexpected top-level files;
- missing Workbench entrypoints;
- source revisions older than the migration baseline;
- final canonical pushes while known full-fork runtime couplings remain.

## Resolve runtime coupling

Before the target repository becomes canonical:

1. publish versioned WRF, WPS, ERA5, and postprocessing images;
2. create a release manifest with full image digests;
3. make the normal user command pull those digests;
4. retain source builds only as an explicit developer/audit path;
5. remove or relocate fork-only WRF/WPS image workflows from the product export;
6. rerun the exporter without `--allow-known-couplings`.

The exporter will then permit a push:

```bash
python3 migration/export-standalone-product.py \
  ../wrf-chammer-workbench \
  --target-url git@github.com:carstenartur/wrf-chammer-workbench.git \
  --push
```

The target repository must already exist and be empty. Creating repositories is a
separate GitHub administrative operation; the exporter intentionally never creates or
deletes remote repositories.

## Audit whether a WRF fork remains necessary

Fetch or add the official WRF upstream remote and choose an exact upstream commit. Then
run:

```bash
python3 migration/audit-wrf-runtime-diff.py \
  --upstream-ref upstream/master \
  --fork-ref master \
  --output workbench-runs/migration/wrf-runtime-diff.json
```

The audit excludes all product paths from the extraction manifest. Every remaining
change must be classified as one of:

- required scientific WRF-core change;
- portable build or platform fix;
- candidate for contribution upstream;
- obsolete fork residue that can be removed.

An empty remaining diff means the product can use a pinned official WRF revision and a
permanent fork is not required. A non-empty diff does not automatically justify a fork;
each item still needs review.

## Issue and history migration

Git history for retained files is rewritten into the target repository. GitHub issues,
pull requests, releases, package visibility, and repository settings are not Git
objects and therefore require a separate migration step.

Until issue migration is complete:

- the current fork remains the authoritative issue tracker;
- new product issues should reference the standalone migration issue;
- closed implementation history remains available in the fork;
- no existing issue is deleted merely because its code moved.

## Acceptance before reducing the fork

Do not remove product paths from the current fork until all of the following hold:

- standalone CI is green;
- setup works on a fresh supported Linux environment;
- digest-pinned runtime images can be pulled anonymously or through documented access;
- `doctor` verifies the installed stack;
- one complete real Xaver run succeeds through the standard worker path;
- run manifest, resources, result viewer, and exports are verified in the new repository;
- rollback to the previous product release is tested.

The migration tooling does not claim that the real Xaver acceptance has already run.
