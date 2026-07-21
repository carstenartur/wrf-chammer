# Integrated per-job result viewer

A successful persistent simulation can be opened directly from the Workbench job panel. No separate `serve.sh` process and no manually entered artifact directory are required.

## User flow

1. Create and queue an immutable real simulation.
2. Wait until all eight pipeline steps have succeeded.
3. Select the successful job in the persistent queue.
4. Choose **View results**.
5. The Workbench validates the result manifest and opens:

```text
/jobs/{simulation-id}/results/
```

The existing WRF Weather Viewer then provides its layer selection, time slider, play/pause controls and point inspection against that job's products.

## Security and provenance boundary

The server does not expose a simulation directory or accept a host path from the browser. It requires:

- job state `SUCCEEDED`,
- exactly one persisted `result-index` artifact,
- a result-index checksum and byte size matching the persisted artifact record,
- the result-index specification key matching the selected job,
- repository revision, ERA5 plan key and the complete pinned WPS/WRF/postprocessing runtime identity map matching the immutable run specification,
- `artificial_weather_data: false`,
- visualization provenance with `mode: wrf` and non-empty `wrfout_files`,
- `metadata.json` with exactly the same WRF-output provenance,
- every served product to be listed below `visualizations/` in the result index,
- the current product bytes to match the indexed SHA-256 and size,
- no symbolic links, absolute paths, traversal components, duplicate products or unindexed files.

Available endpoints are:

```http
GET /api/simulations/{id}/results
GET /jobs/{id}/results/
GET /jobs/{id}/results/metadata.json
GET /jobs/{id}/results/layers/{layer}.json
```

A product is read once, checked in memory and then sent from those verified bytes. This avoids a check/read race between integrity verification and HTTP delivery.

The Workbench accepts only the exact viewer URL derived from the currently selected job. A response cannot redirect the UI to another simulation's result route.

## Honest states

- `READY`, `QUEUED` and active jobs do not receive a result action.
- A `SUCCEEDED` database state alone is insufficient; missing or inconsistent index/provenance data is rejected.
- The viewer displays **Model result, not an observation** together with the simulation and immutable specification identifiers.
- Fixture-mode or artificial-weather metadata cannot enter this product route.

## Current scope

This slice integrates the existing viewer and its current time navigation and point inspection. PNG/CSV/geographic exports, richer map context and result-history comparison remain follow-up work in issue #47.
