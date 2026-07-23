# Runtime release manifests

Normal Workbench installations do not compile WRF or WPS locally. A product release
provides a small JSON manifest that binds the exact Workbench source revision to three
OCI images:

- WPS/ERA5 execution runtime;
- WRF `real.exe` and `wrf.exe` runtime;
- postprocessing and result-indexing runtime.

Every image entry contains a human-readable registry reference and a mandatory
`sha256:` registry digest. The Workbench always pulls and executes the combined
`reference@digest` selector.

## Pull and activate

```bash
python3 wrf-chammer images pull \
  --manifest /path/to/release-manifest.json
```

The command:

1. validates the manifest structure;
2. rejects a manifest for a different Workbench revision when the installed revision
   can be determined;
3. pulls every exact digest;
4. verifies the local image metadata contains that digest;
5. atomically writes `workbench-runs/.runtime/runtime-images.json`;
6. makes the same references and digests available to readiness checks and immutable
   run specifications.

Inspect the active release:

```bash
python3 wrf-chammer images status --json
```

`update-images` remains as a compatibility alias for `images pull`; it no longer starts
a local compiler build.

## Publication boundary

`release-manifest.example.json` is deliberately unpublished and contains zero-value
placeholders. It documents the format only and cannot produce a usable runtime.

A release workflow must replace all placeholders with image references and digests
that were successfully built, smoke-tested, and pushed to the registry. The resulting
manifest must use the exact product commit distributed by the installer.

## Source builds

Source builds remain an explicit developer and audit workflow in the WRF runtime
source repository. They are not a fallback silently performed by the installed
Workbench. A locally built runtime can be used only after creating an equivalent
manifest/activation record with the inspected image identities.

## Security and reproducibility

- mutable tags alone are never accepted as runtime identity;
- client requests cannot override runtime snapshots in a frozen job;
- an activation record has its own canonical SHA-256 integrity value;
- a corrupt or incomplete activation blocks real-run readiness;
- changing a release or image digest creates a different immutable run specification;
- pulling an older release is an explicit rollback action, never an automatic downgrade.
