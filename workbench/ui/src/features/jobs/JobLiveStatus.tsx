import React, { useEffect, useRef, useState } from 'react';
import './job-live-status.css';

type JobSummary = {
  job_id: string;
  state: string;
  attempt: number;
};

type JobEvent = {
  id: number;
  event_type: string;
  state?: string | null;
  message: string;
  created_at: string;
};

type ResourcePayload = {
  ok: boolean;
  snapshots: Array<{
    phase: string;
    logical_cores?: number | null;
    available_memory_bytes?: number | null;
    free_disk_bytes?: number | null;
  }>;
  measurements: Array<{
    metric: string;
    value: number;
    unit: string;
  }>;
};

async function jsonRequest<T>(path: string): Promise<T> {
  const response = await fetch(path);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error?.message || `HTTP ${response.status}`);
  }
  return payload as T;
}

function formatBytes(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return 'not measured';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** index).toFixed(index > 1 ? 2 : 0)} ${units[index]}`;
}

export function JobLiveStatus() {
  const [latest, setLatest] = useState<JobSummary | null>(null);
  const [connection, setConnection] = useState('waiting');
  const [lastEvent, setLastEvent] = useState<JobEvent | null>(null);
  const [eventCount, setEventCount] = useState(0);
  const [resources, setResources] = useState<ResourcePayload | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let stopped = false;
    async function discover() {
      try {
        const payload = await jsonRequest<{ jobs: JobSummary[] }>('/api/jobs?limit=1');
        if (!stopped) setLatest(payload.jobs[0] || null);
      } catch {
        if (!stopped) setConnection('unavailable');
      }
    }
    discover().catch(() => undefined);
    const timer = window.setInterval(() => discover().catch(() => undefined), 2000);
    const queued = () => discover().catch(() => undefined);
    window.addEventListener('wrf-chammer:persistent-job', queued);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      window.removeEventListener('wrf-chammer:persistent-job', queued);
    };
  }, []);

  useEffect(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
    setLastEvent(null);
    setEventCount(0);
    setResources(null);
    if (!latest) {
      setConnection('waiting');
      return undefined;
    }

    const jobId = latest.job_id;
    const source = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/events/stream`);
    sourceRef.current = source;
    setConnection('connecting');

    source.onopen = () => setConnection('live');
    source.onerror = () => setConnection('reconnecting');
    source.addEventListener('job-event', (message) => {
      const event = JSON.parse((message as MessageEvent).data) as JobEvent;
      setLastEvent(event);
      setEventCount((count) => count + 1);
      setLatest((current) => current ? { ...current, state: event.state || current.state } : current);
    });
    source.addEventListener('job-complete', async (message) => {
      const complete = JSON.parse((message as MessageEvent).data) as JobSummary;
      setLatest((current) => current ? { ...current, state: complete.state, attempt: complete.attempt } : current);
      setConnection('complete');
      source.close();
      try {
        const telemetry = await jsonRequest<ResourcePayload>(
          `/api/jobs/${encodeURIComponent(jobId)}/resources`,
        );
        setResources(telemetry);
      } catch {
        setResources(null);
      }
    });

    return () => {
      source.close();
      if (sourceRef.current === source) sourceRef.current = null;
    };
  }, [latest?.job_id]);

  const finished = resources?.snapshots.find((snapshot) => snapshot.phase === 'finished');
  const wallClock = resources?.measurements.find((measurement) => measurement.metric === 'wall_clock_seconds');
  const maximumMemory = resources?.measurements.find((measurement) => measurement.metric === 'maximum_resident_bytes');

  return (
    <section className="job-live-status" aria-labelledby="job-live-title">
      <div>
        <p className="eyebrow">Live persistent execution</p>
        <h2 id="job-live-title">Latest job event stream</h2>
        <p>Durable events are replayed after reconnect; completed attempts expose measured resources.</p>
      </div>
      <div className="job-live-grid" id="job-live-grid">
        <article>
          <span>Job</span>
          <strong>{latest?.job_id || 'No persistent job'}</strong>
          <small>{latest ? `Attempt ${latest.attempt} · ${latest.state}` : 'Queue a validated plan to start.'}</small>
        </article>
        <article>
          <span>Connection</span>
          <strong id="job-stream-connection">{connection}</strong>
          <small>{eventCount} durable event(s) received</small>
        </article>
        <article>
          <span>Latest event</span>
          <strong>{lastEvent?.event_type || 'waiting'}</strong>
          <small>{lastEvent?.message || 'No event received yet.'}</small>
        </article>
        <article>
          <span>Measured wall clock</span>
          <strong>{wallClock ? `${wallClock.value.toFixed(2)} s` : 'pending'}</strong>
          <small>{finished ? `${finished.logical_cores || '?'} logical cores visible` : 'Available after completion'}</small>
        </article>
        <article>
          <span>Measured peak child memory</span>
          <strong>{formatBytes(maximumMemory?.value)}</strong>
          <small>Process-tree measurement for the completed attempt</small>
        </article>
        <article>
          <span>Free disk after attempt</span>
          <strong>{formatBytes(finished?.free_disk_bytes)}</strong>
          <small>Recorded in the persistent runtime snapshot</small>
        </article>
      </div>
    </section>
  );
}
