# Heavy workflow trigger policy

Three workflows compile WRF or WPS from source and can consume substantial runner
time:

| Workflow | Purpose | Local equivalent |
|---|---|---|
| `docker-build.yml` | Build and smoke-test the reproducible WRF image | `docker build -t wrf-reproducible .` followed by the runtime and smoke scripts |
| `docker-wps-build.yml` | Build and verify the reproducible WPS image | `docker build -f Dockerfile.wps -t wps-reproducible .` |
| `wps-integration-test.yml` | Run the bundled ERA5 mini-GRIB through ungrib and metgrid | `sh ci/test-era5-wps-integration.sh` after building the WPS and ERA5 images |

These workflows must run when their scientific source, Dockerfiles, runtime scripts or
integration fixtures change. They must not run merely because a new Workbench API,
UI, test, screenshot or documentation file was added.

## Why dependency-based `paths` are used

The earlier policy used long `paths-ignore` lists containing individual Workbench
Python modules and focused workflow names. That design regressed whenever a new file
was added: the unknown file did not match the ignore list and therefore started all
three expensive builds.

The current policy starts with an inclusive repository baseline and then excludes
stable fork-owned categories:

```text
doc/**
workbench/**
visualization/**
ci/**
.github/workflows/**
```

Each heavy workflow then re-includes only the files from those categories that it
actually copies, invokes or validates. Pattern order is intentional: GitHub evaluates
positive and negative `paths` patterns in sequence, so a later positive dependency
can re-include a file after its containing directory was excluded.

Examples:

- `workbench/simulation_run_manifest.py` does not rebuild WRF or WPS;
- `.github/workflows/simulation-api-tests.yml` does not rebuild WRF or WPS;
- `workbench/validate.py` still starts the WRF build because the Workbench smoke test
  invokes it;
- `ci/prepare-era5-wps.py` still starts the ERA5/WPS integration test because it is
  copied into `Dockerfile.era5`;
- changes below the upstream WRF source tree remain included by default;
- each heavy workflow definition triggers itself so its YAML and build steps are
  exercised when changed.

## Regression contract

Run from a plain checkout:

```bash
python3 ci/verify-heavy-workflow-triggers.py
```

The verifier parses both the `pull_request` and `push` path lists, requires them to be
identical, checks cancellation of obsolete runs and evaluates representative positive
and negative paths in GitHub pattern order.

A focused GitHub workflow runs the same command whenever the trigger policy, verifier
or this documentation changes. The test is intentionally named `*-tests.yml`; the
heavy workflows exclude unrelated workflow definitions, so future focused test
workflows do not reintroduce the original regression.

When one of the three heavy workflow definitions changes, that workflow is deliberately
re-included and runs once on the policy pull request. This validates the actual YAML
and build invocation before the new filtering policy reaches `master`.

## Maintenance rule

When a heavy Dockerfile or integration script begins using another file from an
excluded category, add that exact dependency to the corresponding workflow and add a
positive example to `ci/verify-heavy-workflow-triggers.py`. Do not return to enumerating
every unrelated Workbench module in `paths-ignore`.
