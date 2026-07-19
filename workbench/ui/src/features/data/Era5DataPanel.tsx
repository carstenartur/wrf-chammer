import React, { useEffect, useState } from 'react';
import './era5-data-panel.css';

type Era5Status = {
  ok: boolean;
  credentials: {
    configured: boolean;
    status: 'ready' | 'warning' | 'error';
    summary: string;
    remediation?: string;
  };
  cache: {
    path: string;
    exists: boolean;
    writable: boolean;
    plan_count: number;
    size_bytes: number;
  };
  wizard_preview: {
    available: boolean;
    job_id?: string | null;
  };
};

type Era5Plan = {
  ok: boolean;
  plan_key: string;
  period: {
    start: string;
    end: string;
    interval_hours: number;
    time_points: number;
  };
  requests: Array<{
    name: string;
    dataset: string;
    target: string;
    request_key: string;
    estimated_size_bytes: number;
  }>;
  estimated_download: {
    bytes: number;
    gigabytes: number;
    note: string;
  };
  cache: {
    root: string;
    plan_directory: string;
    status: 'complete' | 'partial' | 'missing';
    hits: number;
    partial_entries: number;
    total: number;
    coverage_percent: number;
  };
  provenance: {
    source: string;
    datasets: string[];
    artificial_weather_data: boolean;
  };
};

type PrepareResponse = {
  ok: boolean;
  plan: Era5Plan;
  prepared: {
    plan: string;
    download_config: string;
    download_started: boolean;
  };
};

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  });
  const payload = await response.json();
  if (!response.ok) {
    const message = payload?.errors?.join('; ') || payload?.error?.message || `HTTP ${response.status}`;
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

function StatusItem(props: { label: string; value: string; detail?: string }) {
  return (
    <div className="era5-data-stat">
      <span>{props.label}</span>
      <strong>{props.value}</strong>
      {props.detail ? <small>{props.detail}</small> : null}
    </div>
  );
}

export function Era5DataPanel() {
  const [status, setStatus] = useState<Era5Status | null>(null);
  const [plan, setPlan] = useState<Era5Plan | null>(null);
  const [prepared, setPrepared] = useState<PrepareResponse['prepared'] | null>(null);
  const [message, setMessage] = useState('Create a valid guided simulation preview, then plan the required real ERA5 data.');
  const [messageKind, setMessageKind] = useState('');
  const [busy, setBusy] = useState(false);

  async function refreshStatus() {
    try {
      const payload = await requestJson<Era5Status>('/api/data/era5/status');
      setStatus(payload);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Failed to read ERA5 status.');
      setMessageKind('error');
    }
  }

  async function planData() {
    setBusy(true);
    setMessage('Planning the real ERA5 boundary data for the latest guided simulation…');
    setMessageKind('pending');
    try {
      const payload = await requestJson<Era5Plan>('/api/data/era5/plan', {
        method: 'POST',
        body: JSON.stringify({
          source: 'latest-wizard-preview',
          interval_hours: 1,
          margin_degrees: 1,
        }),
      });
      setPlan(payload);
      setPrepared(null);
      setMessage('ERA5 requests are planned. No download has been started.');
      setMessageKind('success');
      await refreshStatus();
    } catch (error) {
      setPlan(null);
      setPrepared(null);
      setMessage(error instanceof Error ? error.message : 'ERA5 planning failed.');
      setMessageKind('error');
    } finally {
      setBusy(false);
    }
  }

  async function prepareData() {
    setBusy(true);
    setMessage('Writing the reproducible ERA5 plan and downloader configuration…');
    setMessageKind('pending');
    try {
      const payload = await requestJson<PrepareResponse>('/api/data/era5/prepare', {
        method: 'POST',
        body: JSON.stringify({
          source: 'latest-wizard-preview',
          interval_hours: 1,
          margin_degrees: 1,
        }),
      });
      setPlan(payload.plan);
      setPrepared(payload.prepared);
      setMessage('Plan files are prepared in the managed cache. No network download has been started.');
      setMessageKind('success');
      await refreshStatus();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Preparing ERA5 plan files failed.');
      setMessageKind('error');
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    const onWizardPreview = () => {
      setPlan(null);
      setPrepared(null);
      setMessage('A new guided simulation preview is ready for ERA5 planning.');
      setMessageKind('');
      refreshStatus().catch(() => undefined);
    };
    refreshStatus().catch(() => undefined);
    window.addEventListener('wrf-chammer:wizard-preview', onWizardPreview);
    return () => window.removeEventListener('wrf-chammer:wizard-preview', onWizardPreview);
  }, []);

  const credentialsKind = status?.credentials.configured ? 'ready' : 'warning';
  const cacheDetail = status
    ? `${status.cache.plan_count} plan(s), ${formatBytes(status.cache.size_bytes)}`
    : 'Checking managed cache…';

  return (
    <section className="era5-data-panel" aria-labelledby="era5-data-title">
      <header className="era5-data-heading">
        <div>
          <p className="eyebrow">Step 2 · Real input data</p>
          <h2 id="era5-data-title">Plan ERA5 boundary data</h2>
          <p>
            The Workbench derives real Copernicus ERA5 requests from the latest server-validated map plan.
            It never generates replacement weather fields.
          </p>
        </div>
        <button type="button" className="era5-secondary" onClick={refreshStatus} disabled={busy}>
          Refresh data status
        </button>
      </header>

      <div className="era5-data-status-grid">
        <article className={`era5-status-card ${credentialsKind}`}>
          <span>CDS credentials</span>
          <strong>{status?.credentials.configured ? 'Configured' : 'Not configured'}</strong>
          <small>{status?.credentials.summary || 'Checking credential status…'}</small>
          {!status?.credentials.configured && status?.credentials.remediation
            ? <p>{status.credentials.remediation}</p>
            : null}
        </article>
        <article className={`era5-status-card ${status?.cache.writable ? 'ready' : 'warning'}`}>
          <span>Managed cache</span>
          <strong>{status?.cache.path || '.era5-cache'}</strong>
          <small>{cacheDetail}</small>
        </article>
        <article className={`era5-status-card ${status?.wizard_preview.available ? 'ready' : 'warning'}`}>
          <span>Guided simulation preview</span>
          <strong>{status?.wizard_preview.available ? 'Available' : 'Required'}</strong>
          <small>{status?.wizard_preview.job_id || 'Plan a domain in the guided simulation section first.'}</small>
        </article>
      </div>

      <div className="era5-data-actions">
        <button
          type="button"
          id="era5-plan-data"
          onClick={planData}
          disabled={busy || !status?.wizard_preview.available}
        >
          Plan real ERA5 data
        </button>
        <button
          type="button"
          id="era5-prepare-data"
          className="era5-secondary"
          onClick={prepareData}
          disabled={busy || !plan}
        >
          Prepare download files
        </button>
      </div>

      <p id="era5-data-message" className={`era5-data-message ${messageKind}`} aria-live="polite">
        {message}
      </p>

      {plan ? (
        <div id="era5-plan-result" className="era5-plan-result">
          <div className="era5-data-stats">
            <StatusItem label="ERA5 requests" value={String(plan.requests.length)} detail="Pressure and single levels split by UTC day" />
            <StatusItem label="Boundary time points" value={String(plan.period.time_points)} detail={`${plan.period.interval_hours}-hour cadence, including endpoints`} />
            <StatusItem label="Estimated download" value={`${plan.estimated_download.gigabytes} GB`} detail={plan.estimated_download.note} />
            <StatusItem label="Cache coverage" value={`${plan.cache.coverage_percent}%`} detail={`${plan.cache.hits}/${plan.cache.total} complete; ${plan.cache.partial_entries} partial`} />
          </div>

          <dl className="era5-plan-details">
            <dt>Plan key</dt>
            <dd><code>{plan.plan_key}</code></dd>
            <dt>Source</dt>
            <dd>{plan.provenance.source}</dd>
            <dt>Datasets</dt>
            <dd>{plan.provenance.datasets.join(', ')}</dd>
            <dt>Cache state</dt>
            <dd>{plan.cache.status}</dd>
            <dt>Artificial weather data</dt>
            <dd>{plan.provenance.artificial_weather_data ? 'yes' : 'no'}</dd>
          </dl>

          {prepared ? (
            <div id="era5-prepared-files" className="era5-prepared-files">
              <h3>Prepared files</h3>
              <p><code>{prepared.plan}</code></p>
              <p><code>{prepared.download_config}</code></p>
              <p>Download started: <strong>{prepared.download_started ? 'yes' : 'no'}</strong></p>
            </div>
          ) : null}
        </div>
      ) : null}

      <p className="era5-data-caveat">
        Planning and preparing files do not contact the CDS. Network download, progress, cancellation, and retry
        belong to the persistent worker flow and require an explicit later action.
      </p>
    </section>
  );
}
