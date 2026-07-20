import React, { useCallback, useEffect, useState } from 'react';
import './job-queue-panel.css';

type JobState =
  | 'DRAFT'
  | 'VALIDATING'
  | 'WAITING_FOR_DATA'
  | 'DOWNLOADING_DATA'
  | 'READY'
  | 'QUEUED'
  | 'PREPROCESSING'
  | 'INITIALIZING'
  | 'SIMULATING'
  | 'POSTPROCESSING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLING'
  | 'CANCELLED';

type PersistentJob = {
  job_id: string;
  state: JobState;
  attempt: number;
  priority: number;
  cancel_requested: boolean;
  worker_id?: string | null;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  ended_at?: string | null;
  error?: { code?: string | null; message?: string | null } | null;
  steps?: Array<{
    id: number;
    attempt: number;
    name: string;
    state: string;
    started_at?: string | null;
    ended_at?: string | null;
    exit_code?: number | null;
    log_path?: string | null;
    error?: { code?: string | null; message?: string | null } | null;
  }>;
  artifacts?: Array<{
    id: number;
    attempt: number;
    artifact_type: string;
    relative_path: string;
    size_bytes: number;
    sha256?: string | null;
  }>;
};

type JobEvent = {
  id: number;
  created_at: string;
  event_type: string;
  state?: string | null;
  step_name?: string | null;
  message: string;
};

const TERMINAL = new Set<JobState>(['SUCCEEDED', 'FAILED', 'CANCELLED']);
const RETRYABLE = new Set<JobState>(['FAILED', 'CANCELLED']);

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  });
  const payload = await response.json();
  if (!response.ok) {
    const message = payload?.error?.message || payload?.errors?.join('; ') || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload as T;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** index).toFixed(index > 1 ? 2 : 0)} ${units[index]}`;
}

function stateKind(state: JobState): string {
  if (state === 'SUCCEEDED') return 'success';
  if (state === 'FAILED') return 'error';
  if (state === 'CANCELLED') return 'cancelled';
  if (state === 'CANCELLING') return 'warning';
  return 'active';
}

export function JobQueuePanel() {
  const [jobs, setJobs] = useState<PersistentJob[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<PersistentJob | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [message, setMessage] = useState('Persistent queue is ready.');
  const [messageKind, setMessageKind] = useState('');
  const [busy, setBusy] = useState(false);

  const loadJob = useCallback(async (jobId: string) => {
    const [jobPayload, eventPayload] = await Promise.all([
      requestJson<{ ok: boolean; job: PersistentJob }>(`/api/jobs/${encodeURIComponent(jobId)}`),
      requestJson<{ ok: boolean; events: JobEvent[] }>(`/api/jobs/${encodeURIComponent(jobId)}/events`),
    ]);
    setSelected(jobPayload.job);
    setEvents(eventPayload.events);
  }, []);

  const refresh = useCallback(async (preferredId?: string | null) => {
    try {
      const payload = await requestJson<{ ok: boolean; jobs: PersistentJob[] }>('/api/jobs?limit=100');
      setJobs(payload.jobs);
      const target = preferredId || selectedId || payload.jobs[0]?.job_id || null;
      if (target && payload.jobs.some((job) => job.job_id === target)) {
        setSelectedId(target);
        await loadJob(target);
      } else {
        setSelectedId(null);
        setSelected(null);
        setEvents([]);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not load persistent jobs.');
      setMessageKind('error');
    }
  }, [loadJob, selectedId]);

  useEffect(() => {
    refresh().catch(() => undefined);
    const interval = window.setInterval(() => refresh().catch(() => undefined), 2500);
    const onQueued = (event: Event) => {
      const detail = (event as CustomEvent<{ jobId?: string }>).detail;
      refresh(detail?.jobId || null).catch(() => undefined);
    };
    window.addEventListener('wrf-chammer:persistent-job', onQueued);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener('wrf-chammer:persistent-job', onQueued);
    };
  }, [refresh]);

  async function selectJob(jobId: string) {
    setSelectedId(jobId);
    setBusy(true);
    try {
      await loadJob(jobId);
      setMessage(`Loaded ${jobId}.`);
      setMessageKind('');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not load job details.');
      setMessageKind('error');
    } finally {
      setBusy(false);
    }
  }

  async function runAction(action: 'cancel' | 'retry') {
    if (!selectedId) return;
    setBusy(true);
    setMessageKind('pending');
    setMessage(action === 'cancel' ? 'Requesting cancellation…' : 'Queueing a new attempt…');
    try {
      const payload = await requestJson<{ ok: boolean; job: PersistentJob }>(
        `/api/jobs/${encodeURIComponent(selectedId)}/${action}`,
        { method: 'POST', body: '{}' },
      );
      setMessage(action === 'cancel'
        ? `Cancellation state: ${payload.job.state}.`
        : `Attempt ${payload.job.attempt} is queued.`);
      setMessageKind('success');
      await refresh(selectedId);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `Could not ${action} job.`);
      setMessageKind('error');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="job-queue-panel" aria-labelledby="job-queue-title">
      <header className="job-queue-heading">
        <div>
          <p className="eyebrow">Step 3 · Persistent execution</p>
          <h2 id="job-queue-title">Queue and job history</h2>
          <p>Jobs remain visible across browser, API, and worker restarts. Long-running work is never executed inside an HTTP request.</p>
        </div>
        <button type="button" className="job-secondary" onClick={() => refresh()} disabled={busy}>Refresh jobs</button>
      </header>

      <p id="job-queue-message" className={`job-queue-message ${messageKind}`.trim()} aria-live="polite">{message}</p>

      <div className="job-queue-layout">
        <div className="job-list" role="list" aria-label="Persistent jobs">
          {jobs.length === 0 ? (
            <p className="job-empty">No persistent jobs yet. Queue a validated simulation from the guided planner.</p>
          ) : jobs.map((job) => (
            <button
              type="button"
              role="listitem"
              key={job.job_id}
              className={`job-list-item ${selectedId === job.job_id ? 'selected' : ''}`}
              onClick={() => selectJob(job.job_id)}
              aria-pressed={selectedId === job.job_id}
            >
              <span>
                <strong>{job.job_id}</strong>
                <small>Attempt {job.attempt} · priority {job.priority}</small>
              </span>
              <span className={`job-state ${stateKind(job.state)}`}>{job.state}</span>
            </button>
          ))}
        </div>

        <div className="job-detail" id="persistent-job-detail">
          {!selected ? (
            <p className="job-empty">Select a persistent job to inspect attempts, events, and artifacts.</p>
          ) : (
            <>
              <div className="job-detail-title">
                <div>
                  <h3>{selected.job_id}</h3>
                  <p>Created {selected.created_at} · updated {selected.updated_at}</p>
                </div>
                <span className={`job-state ${stateKind(selected.state)}`}>{selected.state}</span>
              </div>

              <dl className="job-detail-grid">
                <dt>Attempt</dt><dd>{selected.attempt}</dd>
                <dt>Worker</dt><dd>{selected.worker_id || 'not assigned'}</dd>
                <dt>Started</dt><dd>{selected.started_at || 'not started'}</dd>
                <dt>Ended</dt><dd>{selected.ended_at || 'not finished'}</dd>
                <dt>Cancel requested</dt><dd>{selected.cancel_requested ? 'yes' : 'no'}</dd>
              </dl>

              {selected.error ? (
                <div className="job-error"><strong>{selected.error.code || 'Job error'}</strong><p>{selected.error.message}</p></div>
              ) : null}

              <div className="job-actions">
                <button
                  type="button"
                  id="persistent-job-cancel"
                  disabled={busy || TERMINAL.has(selected.state)}
                  onClick={() => runAction('cancel')}
                >Cancel job</button>
                <button
                  type="button"
                  id="persistent-job-retry"
                  className="job-secondary"
                  disabled={busy || !RETRYABLE.has(selected.state)}
                  onClick={() => runAction('retry')}
                >Retry job</button>
              </div>

              <div className="job-detail-columns">
                <div>
                  <h4>Events</h4>
                  <ol className="job-events">
                    {events.map((event) => (
                      <li key={event.id}>
                        <strong>{event.event_type}</strong>
                        <span>{event.message}</span>
                        <small>{event.created_at}{event.state ? ` · ${event.state}` : ''}</small>
                      </li>
                    ))}
                  </ol>
                </div>
                <div>
                  <h4>Artifacts</h4>
                  <ul className="job-artifacts">
                    {(selected.artifacts || []).map((artifact) => (
                      <li key={artifact.id}>
                        <strong>{artifact.relative_path}</strong>
                        <span>{artifact.artifact_type} · {formatBytes(artifact.size_bytes)}</span>
                        <small>{artifact.sha256 ? `SHA-256 ${artifact.sha256.slice(0, 16)}…` : 'No checksum'}</small>
                      </li>
                    ))}
                    {(selected.artifacts || []).length === 0 ? <li>No artifacts recorded yet.</li> : null}
                  </ul>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
