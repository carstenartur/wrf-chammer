import React, { useEffect, useState } from 'react';
import './system-readiness.css';

type ReadinessCheck = {
  id: string;
  status: 'ready' | 'warning' | 'error';
  summary: string;
  remediation?: string;
};

type ReadinessResponse = {
  ok: boolean;
  status: 'ready' | 'warning' | 'error';
  generated_at: string;
  summary: Record<string, number>;
  checks: ReadinessCheck[];
};

async function loadReadiness(): Promise<ReadinessResponse> {
  const response = await fetch('/api/readiness');
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error?.message || `HTTP ${response.status}`);
  }
  return payload as ReadinessResponse;
}

function statusLabel(status: ReadinessCheck['status']): string {
  if (status === 'ready') return 'Ready';
  if (status === 'warning') return 'Limited';
  return 'Action required';
}

export function SystemReadiness() {
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  async function refresh() {
    setLoading(true);
    setError('');
    try {
      setReadiness(await loadReadiness());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to load system readiness.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh().catch(() => undefined);
  }, []);

  const overall = readiness?.status || 'warning';
  const checks = readiness?.checks || [];
  return React.createElement(
    'section',
    { className: 'readiness-shell', 'aria-labelledby': 'system-readiness-title' },
    React.createElement(
      'div',
      { className: 'readiness-heading' },
      React.createElement(
        'div',
        null,
        React.createElement('p', { className: 'eyebrow' }, 'Before a real simulation'),
        React.createElement('h2', { id: 'system-readiness-title' }, 'System readiness'),
        React.createElement(
          'p',
          { className: 'readiness-intro' },
          'Dry-runs work without Docker. Real ERA5/WPS/WRF jobs need the checks below to be ready.',
        ),
      ),
      React.createElement(
        'div',
        { className: 'readiness-actions' },
        React.createElement(
          'span',
          { className: `status-pill ${overall === 'ready' ? 'good' : overall === 'error' ? 'bad' : 'warn'}` },
          loading ? 'Checking…' : error ? 'Unavailable' : statusLabel(overall),
        ),
        React.createElement('button', { type: 'button', className: 'secondary-button', onClick: refresh, disabled: loading }, 'Check again'),
      ),
    ),
    error
      ? React.createElement('div', { className: 'message bad', role: 'alert' }, error)
      : React.createElement(
          'div',
          { className: 'readiness-grid', 'aria-live': 'polite' },
          ...checks.map((check) =>
            React.createElement(
              'article',
              { key: check.id, className: `readiness-check ${check.status}` },
              React.createElement(
                'div',
                { className: 'readiness-check-header' },
                React.createElement('strong', null, check.id),
                React.createElement('span', null, statusLabel(check.status)),
              ),
              React.createElement('p', null, check.summary),
              check.remediation ? React.createElement('p', { className: 'readiness-remediation' }, check.remediation) : null,
            ),
          ),
        ),
  );
}
