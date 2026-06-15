# WRF Workbench Architecture

This document describes the modern, user-oriented architecture of the WRF
Workbench — a platform that wraps the WRF modelling system (including WPS
preprocessing, WRF simulation, and output post-processing) behind a managed
service layer.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Component Map](#2-component-map)
3. [Backend Service](#3-backend-service)
4. [Worker Processes](#4-worker-processes)
5. [Storage](#5-storage)
6. [REST API](#6-rest-api)
7. [Event Catalogue](#7-event-catalogue)
8. [Job Execution Model](#8-job-execution-model)
9. [Deployment Model](#9-deployment-model)
10. [Security Considerations](#10-security-considerations)

---

## 1. System Overview

The WRF Workbench exposes WRF as a service.  Users submit simulation requests
through a REST API or a web UI; the platform handles data acquisition,
preprocessing, simulation execution, and output delivery without requiring any
knowledge of WRF internals.

```
                 ┌─────────────────────────────────────┐
  Users / CI ───►│          REST API Gateway           │
                 └───────────────┬─────────────────────┘
                                 │
                 ┌───────────────▼─────────────────────┐
                 │           Backend Service            │
                 │  (job manager · scheduler · events)  │
                 └────┬──────────┬──────────────┬───────┘
                      │          │              │
          ┌───────────▼──┐  ┌────▼───────┐  ┌──▼────────────┐
          │  Job Queue   │  │  Metadata  │  │  Event Bus    │
          │  (work items)│  │  Database  │  │  (Pub/Sub)    │
          └──────┬───────┘  └────────────┘  └───────────────┘
                 │
       ┌─────────┼──────────────────┐
       │         │                  │
  ┌────▼────┐ ┌──▼──────┐  ┌───────▼──────┐
  │  WPS    │ │  WRF    │  │  Post-proc   │
  │ Worker  │ │ Worker  │  │   Worker     │
  └────┬────┘ └──┬──────┘  └───────┬──────┘
       │         │                  │
       └────────►│◄─────────────────┘
                 │
         ┌───────▼────────┐
         │  Object / File  │
         │    Storage      │
         └────────────────┘
```

---

## 2. Component Map

| Component | Role | Technology hint |
|---|---|---|
| REST API Gateway | Entry point for all user requests | HTTP/2, OpenAPI 3 |
| Backend Service | Orchestrates jobs, persists state | Python / Go |
| Job Queue | Distributes work to workers | Redis Streams / RabbitMQ |
| Metadata Database | Stores job records and namelist configs | PostgreSQL |
| Event Bus | Publishes lifecycle events | Kafka / Redis Pub-Sub |
| WPS Worker | Runs `geogrid`, `ungrib`, `metgrid` | Docker container (Dockerfile.wps) |
| WRF Worker | Runs `real.exe` + `wrf.exe` | Docker container (Dockerfile) |
| Post-proc Worker | Converts / packages WRF output | Docker container |
| Object Storage | Holds input data and output NetCDF files | S3-compatible / local volume |

---

## 3. Backend Service

The backend service is the central coordinator.  It owns the authoritative
record of every job and drives state transitions.

### Responsibilities

- Accept and validate job submissions from the API Gateway.
- Resolve and fetch required input datasets (ERA5 via the Copernicus CDS API,
  static GEOG data, observation files).
- Write job records to the Metadata Database.
- Push work items onto the Job Queue.
- Consume lifecycle events from the Event Bus and advance job state.
- Expose job status and output artefact URLs back to callers.
- Enforce resource quotas and concurrency limits.

### Job State Machine

```
SUBMITTED ──► ACQUIRING_DATA ──► PREPROCESSING ──► SIMULATING ──► POSTPROCESSING ──► COMPLETED
                                                                                     /
     └────────────────── FAILED ◄─────────────────────────────────────────────────┘
                          │
                          ▼
                       CANCELLED
```

---

## 4. Worker Processes

Workers are stateless processes.  Each worker type is packaged as a separate
Docker image (see `Dockerfile`, `Dockerfile.wps`, `Dockerfile.era5`) so that
image versions and resource profiles can be managed independently.

### 4.1 Data-Acquisition Worker

Built on top of `Dockerfile.era5`.

Responsibilities:
- Download ERA5 pressure-level and single-level GRIB files from the Copernicus
  CDS API using the configuration described in `ci/era5/`.
- Validate downloads against an expected manifest.
- Upload raw GRIB files to Object Storage.
- Emit a `data.acquired` event on completion.

### 4.2 WPS Worker

Built on top of `Dockerfile.wps`.

Responsibilities:
- Retrieve GRIB input and `namelist.wps` from Object Storage.
- Execute `geogrid.exe`, `ungrib.exe`, and `metgrid.exe` in sequence.
- Upload `met_em.d*` files to Object Storage.
- Emit a `preprocessing.completed` event on success.

### 4.3 WRF Simulation Worker

Built on top of `Dockerfile` (the CMake-based simulation image).

Responsibilities:
- Retrieve `met_em.d*` files and `namelist.input` from Object Storage.
- Execute `real.exe` to produce `wrfinput_d01` and `wrfbdy_d01`.
- Execute `wrf.exe` (optionally via `mpirun` for distributed runs).
- Stream `rsl.out.*` log tails to the Event Bus.
- Upload `wrfout_d*` NetCDF files to Object Storage.
- Emit a `simulation.completed` event on success.

### 4.4 Post-processing Worker

Responsibilities:
- Convert `wrfout_d*` NetCDF files to user-requested formats (GeoTIFF, CSV
  time-series, compressed NetCDF).
- Generate summary statistics and diagnostics.
- Upload derived products to Object Storage.
- Emit a `postprocessing.completed` event.

### Worker Contract

Every worker reads its configuration from environment variables and a
path to a job-specific JSON manifest object in Object Storage.  It writes
status updates to the Event Bus and exits with code `0` on success or a
non-zero code on failure (which triggers an automatic retry or a `FAILED`
state transition in the backend).

---

## 5. Storage

### 5.1 Object Storage (blobs)

All large binary artefacts are stored in an S3-compatible object store.

Bucket layout:

```
wrf-workbench/
  jobs/
    {job-id}/
      input/
        era5/          # raw GRIB files
        geog/          # static geographic data (linked, not copied)
      intermediate/
        met_em/        # WPS output
        wrfinput/      # real.exe output
      output/
        wrfout/        # wrf.exe NetCDF output
        products/      # post-processed derived files
      logs/
        wps.log
        real.log
        wrf.log
```

### 5.2 Metadata Database

A relational store (PostgreSQL) holds structured job metadata, namelist
parameters, and user/organisation records.

Key tables:

| Table | Purpose |
|---|---|
| `jobs` | One row per simulation job; tracks state, timestamps, artefact URLs |
| `namelists` | Versioned copies of `namelist.input` and `namelist.wps` |
| `datasets` | Catalogue of reusable input datasets (ERA5 tiles, GEOG data) |
| `events` | Append-only audit log mirrored from the Event Bus |
| `users` | User and API-key records |
| `quotas` | Per-user resource limits |

### 5.3 Ephemeral Worker Storage

Each worker container is given a temporary work directory (a mounted `emptyDir`
volume in Kubernetes, or a named Docker volume otherwise).  The directory is
removed when the container terminates.  All persistent data is written to Object
Storage before the container exits.

---

## 6. REST API

The API follows OpenAPI 3.  All resources are versioned under `/v1/`.

### Endpoints

#### Jobs

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/jobs` | Submit a new simulation job |
| `GET` | `/v1/jobs` | List jobs (paginated, filterable by state) |
| `GET` | `/v1/jobs/{id}` | Get job details and state |
| `DELETE` | `/v1/jobs/{id}` | Cancel a queued or running job |
| `GET` | `/v1/jobs/{id}/logs` | Stream or download worker logs |
| `GET` | `/v1/jobs/{id}/outputs` | List output artefact URLs |

#### Namelists

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/namelists` | Upload a new namelist (wps or wrf) |
| `GET` | `/v1/namelists/{id}` | Retrieve a stored namelist |

#### Datasets

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/datasets` | List available input datasets |
| `GET` | `/v1/datasets/{id}` | Get dataset metadata |
| `POST` | `/v1/datasets/era5` | Trigger an ERA5 download |

#### Events

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/jobs/{id}/events` | SSE or WebSocket stream of job events |

### Job Submission Payload

```json
{
  "name": "my-simulation",
  "wps_namelist_id": "nl-abc123",
  "wrf_namelist_id": "nl-def456",
  "era5_config": {
    "start_date": "2024-01-15T00:00:00Z",
    "end_date":   "2024-01-16T00:00:00Z",
    "pressure_levels": [1000, 850, 500, 200],
    "bounding_box": { "north": 55, "south": 45, "west": 5, "east": 15 }
  },
  "resources": {
    "wrf_mpi_np": 16,
    "wrf_openmp_threads": 2
  }
}
```

---

## 7. Event Catalogue

All lifecycle events share a common envelope:

```json
{
  "event_id":  "evt-0001",
  "job_id":    "job-abc123",
  "event":     "<event-type>",
  "timestamp": "2024-01-15T08:32:00Z",
  "source":    "<worker-name>",
  "payload":   { }
}
```

### Event Types

| Event type | Emitted by | Payload fields |
|---|---|---|
| `job.submitted` | Backend | `name`, `namelist_ids` |
| `job.queued` | Backend | `queue_position` |
| `data.acquiring` | Data-Acq Worker | `dataset_ids`, `total_bytes_expected` |
| `data.acquired` | Data-Acq Worker | `grib_paths`, `manifest_url` |
| `preprocessing.started` | WPS Worker | `step`: `geogrid`\|`ungrib`\|`metgrid` |
| `preprocessing.step_completed` | WPS Worker | `step`, `duration_s` |
| `preprocessing.completed` | WPS Worker | `met_em_count`, `output_prefix` |
| `simulation.started` | WRF Worker | `np`, `threads` |
| `simulation.progress` | WRF Worker | `current_time`, `wallclock_s` |
| `simulation.completed` | WRF Worker | `wrfout_count`, `duration_s` |
| `postprocessing.started` | Post-proc Worker | `formats` |
| `postprocessing.completed` | Post-proc Worker | `product_urls` |
| `job.completed` | Backend | `output_urls`, `total_duration_s` |
| `job.failed` | Backend / Worker | `stage`, `error`, `retryable` |
| `job.cancelled` | Backend | `cancelled_by` |

---

## 8. Job Execution Model

### 8.1 Submission flow

1. Client sends `POST /v1/jobs` with the job description.
2. Backend validates the payload, creates a job record in `SUBMITTED` state,
   and returns a `job_id`.
3. Backend enqueues a `data-acquisition` work item; job transitions to
   `ACQUIRING_DATA`.
4. Data-Acquisition Worker downloads ERA5 data and emits `data.acquired`.
5. Backend enqueues a `preprocessing` work item; job transitions to
   `PREPROCESSING`.
6. WPS Worker runs WPS pipeline and emits `preprocessing.completed`.
7. Backend enqueues a `simulation` work item; job transitions to `SIMULATING`.
8. WRF Worker runs `real.exe` + `wrf.exe` and emits `simulation.completed`.
9. Backend enqueues a `postprocessing` work item; job transitions to
   `POSTPROCESSING`.
10. Post-proc Worker processes outputs and emits `postprocessing.completed`.
11. Backend transitions job to `COMPLETED` and emits `job.completed`.

### 8.2 Retry policy

| Stage | Max retries | Retry delay |
|---|---|---|
| Data acquisition | 3 | Exponential back-off, 30 s base |
| Preprocessing | 2 | 60 s fixed |
| Simulation | 1 | No automatic retry (requires human review) |
| Post-processing | 3 | 30 s fixed |

### 8.3 Concurrency and resource limits

- Worker pools are sized independently via replica counts (see §9).
- MPI parallelism within a single WRF job is controlled by the `wrf_mpi_np`
  field in the job submission payload.
- A global concurrency limit prevents more than *N* simultaneous WRF simulation
  pods from running.  Jobs above this limit remain in `SIMULATING` (queued)
  until capacity is free.

---

## 9. Deployment Model

### 9.1 Container images

| Image | Base | Purpose |
|---|---|---|
| `wrf-workbench/backend` | Python slim | Backend service and API Gateway |
| `wrf-workbench/wrf-worker` | `Dockerfile` (this repo) | WRF simulation worker |
| `wrf-workbench/wps-worker` | `Dockerfile.wps` (this repo) | WPS preprocessing worker |
| `wrf-workbench/era5-worker` | `Dockerfile.era5` (this repo) | ERA5 data-acquisition worker |
| `wrf-workbench/postproc-worker` | Python slim + xarray/wrf-python | Output post-processing |

### 9.2 Kubernetes deployment

The recommended production deployment runs on Kubernetes.

```
Namespace: wrf-workbench
│
├── Deployment: backend          (replicas: 2)
├── Deployment: era5-worker      (replicas: 2)
├── Deployment: wps-worker       (replicas: 2)
├── Deployment: wrf-worker       (replicas: variable, HPA-managed)
├── Deployment: postproc-worker  (replicas: 2)
│
├── StatefulSet: postgresql      (1 primary + 1 standby)
├── StatefulSet: redis           (1 primary + 1 replica)
│
├── Service: backend-svc         (ClusterIP → Ingress)
├── Ingress: api-gateway         (TLS termination)
│
├── PersistentVolumeClaim: wrf-scratch   (ReadWriteMany, for job workdirs)
└── ExternalSecret: cds-api-key         (Copernicus CDS credentials)
```

WRF simulation pods may request GPU or high-core-count node pools via
`nodeSelector` / `tolerations` when running at scale.

### 9.3 Docker Compose (local / development)

A `docker-compose.yml` at the repository root is used for local development and
integration testing.  It starts all services on a single host with in-process
Redis and a local MinIO bucket as the S3-compatible object store.

```yaml
# Sketch — actual file to be committed separately
services:
  backend:
    image: wrf-workbench/backend
    environment:
      DATABASE_URL: "postgresql://<user>:<password>@db:5432/wrf"
      REDIS_URL:    redis://redis:6379
      S3_ENDPOINT:  http://minio:9000
  wps-worker:   { image: wrf-workbench/wps-worker, … }
  wrf-worker:   { image: wrf-workbench/wrf-worker, … }
  era5-worker:  { image: wrf-workbench/era5-worker, … }
  postproc:     { image: wrf-workbench/postproc-worker, … }
  db:           { image: postgres:16, … }
  redis:        { image: redis:7-alpine, … }
  minio:        { image: minio/minio, … }
```

### 9.4 CI/CD pipeline

The existing GitHub Actions workflows are extended:

| Workflow | Trigger | Purpose |
|---|---|---|
| `ci.yml` | push / PR | ERA5 offline checks; compilation tests |
| `docker-build.yml` | push to `master` | Build and push `wrf-worker` image |
| `docker-wps-build.yml` | push to `master` | Build and push `wps-worker` image |
| `docker-era5-pipeline.yml` | manual | Download ERA5 + WPS integration test |
| `deploy-staging.yml` *(new)* | push to `master` | Deploy all images to staging namespace |
| `deploy-production.yml` *(new)* | release tag | Deploy all images to production |

---

## 10. Security Considerations

- All API requests require a bearer token (JWT or API key).
- Copernicus CDS credentials are stored as Kubernetes `ExternalSecret` objects
  and injected as environment variables at pod start; they are never written to
  Object Storage or logs.
- Worker pods run as non-root users.
- Object Storage bucket policies enforce per-job path isolation: each job's
  service account can only read/write its own prefix.
- Network policies restrict inter-pod traffic to declared service dependencies.
- Log output is scanned for credential patterns before forwarding to the event
  stream.
