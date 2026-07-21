# Persistent simulation event streaming and resource preflight

This layer completes two remaining local-orchestration requirements without introducing another job database or queue.

## Reconnectable events

Every simulation state transition is already persisted in `job_event` with a monotonically increasing per-job `sequence`. The API exposes that sequence directly:

```http
GET /api/simulations/{job-id}/events?after=12
```

The response contains only events with `sequence > 12`.

For near-real-time delivery:

```http
GET /api/simulations/{job-id}/events/stream
Accept: text/event-stream
Last-Event-ID: 12
```

The stream emits:

```text
id: 13
event: simulation-event
data: {"sequence":13,...}
```

Browser `EventSource` reconnects with `Last-Event-ID`, so already rendered events are not replayed. The server sends heartbeats during quiet periods and a final `simulation-complete` event after `SUCCEEDED`, `FAILED` or `CANCELLED` has no undispatched persistent events.

The stream uses the same loopback-only security boundary as the rest of the Workbench API. Invalid cursors are rejected before streaming headers are sent.

## GUI behavior

`simulation-job-stream.js` attaches to the existing persistent queue custom element.

- `READY` and terminal jobs do not open a stream.
- `QUEUED` and active jobs use `EventSource`.
- Each persistent event refreshes the selected job detail.
- A terminal event performs one full list/detail refresh and closes the stream.
- Selecting another job closes the old stream and opens the new one.
- Polling is disabled while the SSE channel is active.
- If SSE is unavailable, the existing polling behavior resumes.

The canonical browser source remains `workbench/web/simulation-job-stream.js`; the Vite public copy must be byte-identical.

## Resource estimates

Admission reads the frozen estimate from:

```text
identity.job.metadata.resource_estimate
```

Expected fields are the same fields produced by the simulation wizard:

```json
{
  "estimated_ram_gb": {
    "minimum": 8,
    "recommended": 12
  },
  "estimated_storage_gb": {
    "working_total": 20
  }
}
```

The worker compares minimum RAM and working disk requirements with current local availability. Default safety headroom is:

```text
memory: 15%
disk:   10%
```

If no frozen estimate is available, the local MVP admits the job and records `estimate_available: false`. It does not fabricate a requirement.

If the frozen minimum cannot be met, the queued job becomes:

```text
FAILED / INSUFFICIENT_RESOURCES
```

No step is started, no runtime container is launched, `started_at` remains empty, and the complete structured assessment is persisted in a preflight resource measurement and job event.

## Concurrency

The worker claims jobs with an atomic SQLite transaction. Before a job transitions out of `QUEUED`, the store counts active simulations and enforces:

```text
WRF_CHAMMER_MAX_ACTIVE_SIMULATIONS
```

The default is `1`. If no slot is available, the job remains `QUEUED` and continues to display that it is waiting for a worker. No failure event is generated merely because another simulation is active.

## Persisted preflight

An admitted job receives:

```text
resource_preflight_passed
```

and a `ResourceMeasurement` whose metadata contains:

- phase `preflight`;
- worker ID;
- whether an estimate was available;
- frozen requested bytes;
- current available memory and disk;
- applied headroom;
- admission reasons.

A rejected job receives `resource_preflight_failed` with the same structured assessment.

No host path, environment secret or raw process output is persisted.

## Tests

```bash
python3 workbench/server/tests/test_simulation_stream_and_preflight.py -v
python3 workbench/server/tests/test_simulation_event_stream_api.py -v
node workbench/server/tests/test_simulation_job_stream_ui.js
```

The focused tests cover:

- monotonically ordered event cursors;
- reconnect without duplicate events;
- terminal SSE completion;
- invalid cursor rejection;
- real HTTP `text/event-stream` behavior;
- atomic concurrency limits;
- resource admission with and without estimates;
- rejection before any step starts;
- persisted preflight events and measurements;
- GUI stream lifecycle and polling fallback;
- all existing worker lifecycle and cancellation regressions.

## Scope boundary

This is local admission, not an HPC scheduler. It intentionally does not reserve kernel memory or disk blocks. It prevents obviously impossible starts, limits concurrent heavy simulations and records the decision transparently. Distributed scheduling and multi-host resource reservations remain outside the local MVP.
