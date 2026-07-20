import React, { useCallback, useEffect, useState } from 'react';
import './job-queue-launcher.css';

type LatestPreview = {
  ok: boolean;
  available: boolean;
  preview?: {
    valid: boolean;
    config: Record<string, unknown> & { id?: string; name?: string };
  } | null;
};

type QueuedResponse = {
  ok: boolean;
  execution: string;
  job: { job_id: string; state: string; attempt: number };
};

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

function queuedId(base: string): string {
  const clean = base.toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '') || 'wrf-job';
  const timestamp = new Date().toISOString().replace(/[-:.TZ]/g, '').toLowerCase();
  return `${clean}-queued-${timestamp}`;
}

export function JobQueueLauncher() {
  const [latest, setLatest] = useState<LatestPreview | null>(null);
  const [message, setMessage] = useState('Create a valid guided simulation preview before queueing a persistent job.');
  const [kind, setKind] = useState('');
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const payload = await requestJson<LatestPreview>('/api/wizard/latest');
      setLatest(payload);
      if (payload.available && payload.preview?.valid) {
        setMessage(`Validated plan ${payload.preview.config.id || 'is'} ready to queue.`);
        setKind('success');
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not read the latest guided plan.');
      setKind('error');
    }
  }, []);

  useEffect(() => {
    refresh().catch(() => undefined);
    const interval = window.setInterval(() => refresh().catch(() => undefined), 2000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  async function queueLatest() {
    const preview = latest?.preview;
    if (!latest?.available || !preview?.valid) return;
    setBusy(true);
    setKind('pending');
    setMessage('Adding the validated plan to the persistent queue…');
    try {
      const config = JSON.parse(JSON.stringify(preview.config)) as Record<string, unknown> & { id?: string; name?: string };
      config.id = queuedId(config.id || 'wrf-job');
      config.name = `${config.name || 'WRF job'} · persistent queue`;
      const payload = await requestJson<QueuedResponse>('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({ execution: 'queued', start: true, config }),
      });
      setMessage(`Job ${payload.job.job_id} entered state ${payload.job.state}.`);
      setKind('success');
      window.dispatchEvent(new CustomEvent('wrf-chammer:persistent-job', {
        detail: { jobId: payload.job.job_id },
      }));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not queue the guided plan.');
      setKind('error');
    } finally {
      setBusy(false);
    }
  }

  const available = Boolean(latest?.available && latest.preview?.valid);
  return (
    <section className="job-launcher" aria-labelledby="job-launcher-title">
      <div>
        <p className="eyebrow">Persistent execution</p>
        <h2 id="job-launcher-title">Queue the validated simulation</h2>
        <p>The server stores an immutable job and returns immediately. A separate worker performs the run.</p>
      </div>
      <div className="job-launcher-actions">
        <button type="button" className="job-secondary" onClick={refresh} disabled={busy}>Refresh plan</button>
        <button type="button" id="queue-latest-job" onClick={queueLatest} disabled={busy || !available}>Queue latest plan</button>
      </div>
      <p id="job-launcher-message" className={`job-launcher-message ${kind}`.trim()} aria-live="polite">{message}</p>
    </section>
  );
}
