import React, { useEffect, useMemo, useState } from 'react';
import { EventSearch } from './features/events/EventSearch';
import {
  getEvent,
  getHealth,
  getJob,
  getLogs,
  previewJob,
  searchEvents,
  startJob,
} from './shared/api/workbenchApi';
import type {
  DomainPreset,
  EventDetailResponse,
  JobFile,
  JobSummary,
  PreviewResponse,
  WorkbenchEvent,
} from './shared/api/types';

const ERROR_MESSAGES = {
  search: 'Failed to search events.',
  eventDetail: 'Failed to load event details.',
  preview: 'Failed to generate preview.',
  run: 'Failed to run dry-run job.',
  refresh: 'Failed to refresh job status.',
};

function eventPeriod(event: WorkbenchEvent): string {
  if (event.period?.start && event.period?.end) {
    return `${event.period.start} → ${event.period.end}`;
  }
  if (event.start && event.end) {
    return `${event.start} → ${event.end}`;
  }
  return 'No period available';
}

function preferredPresetId(event: WorkbenchEvent | undefined, items: Array<{ id: string }>, preferredField: 'default_domain' | 'default_resolution_preset'): string {
  const preferred = event?.[preferredField];
  if (preferred && items.some((item) => item.id === preferred)) {
    return preferred;
  }
  return items[0]?.id || '';
}

function buildJobId(eventId: string | null): string {
  return `${eventId || 'event'}-ui-dry-run`;
}

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

function SelectPresets(props: {
  detail: EventDetailResponse | null;
  domain: string;
  resolution: string;
  mode: string;
  onDomain: (value: string) => void;
  onResolution: (value: string) => void;
  onMode: (value: string) => void;
}) {
  const event = props.detail?.event;
  const selectedDomain = props.detail?.domain_presets.find((domain) => domain.id === props.domain) || null;
  const cellCount = Math.max(1, Number(selectedDomain?.e_we || 1) * Number(selectedDomain?.e_sn || 1));
  const scale = Math.min(1, Math.sqrt(cellCount / 12000));
  const width = selectedDomain ? Math.max(90, 80 + 200 * scale) : 200;
  const height = selectedDomain ? Math.max(55, 50 + 110 * scale) : 100;
  const label = selectedDomain
    ? `${selectedDomain.id}: ${selectedDomain.e_we} × ${selectedDomain.e_sn} @ ${selectedDomain.dx_km} km`
    : 'No domain selected';

  return React.createElement(
    'section',
    { className: 'panel', 'aria-labelledby': 'event-detail-title' },
    React.createElement('h2', { id: 'event-detail-title' }, '2. Select presets'),
    React.createElement(
      'div',
      { id: 'event-detail', className: props.detail ? undefined : 'empty-state' },
      props.detail
        ? React.createElement(
            'dl',
            { className: 'meta-list' },
            React.createElement('dt', null, 'Event'),
            React.createElement('dd', null, event?.name || event?.id),
            React.createElement('dt', null, 'Type'),
            React.createElement('dd', null, event?.event_type || 'n/a'),
            React.createElement('dt', null, 'Period'),
            React.createElement('dd', null, event ? eventPeriod(event) : 'n/a'),
            React.createElement('dt', null, 'Outputs'),
            React.createElement('dd', null, event?.suggested_outputs?.join(', ') || 'n/a'),
          )
        : 'Select an event to see its defaults.',
    ),
    React.createElement(
      'div',
      { className: 'form-grid' },
      React.createElement('label', { htmlFor: 'domain-select' }, 'Domain preset'),
      React.createElement(
        'select',
        { id: 'domain-select', disabled: !props.detail, value: props.domain, onChange: (evt: React.ChangeEvent<HTMLSelectElement>) => props.onDomain(evt.currentTarget.value) },
        props.detail?.domain_presets.map((domain) => React.createElement('option', { key: domain.id, value: domain.id }, `${domain.label || domain.id} (${domain.dx_km} km)`)),
      ),
      React.createElement('label', { htmlFor: 'resolution-select' }, 'Resolution preset'),
      React.createElement(
        'select',
        { id: 'resolution-select', disabled: !props.detail, value: props.resolution, onChange: (evt: React.ChangeEvent<HTMLSelectElement>) => props.onResolution(evt.currentTarget.value) },
        props.detail?.resolution_presets.map((preset) => React.createElement('option', { key: preset.id, value: preset.id }, preset.label || preset.id)),
      ),
      React.createElement('label', { htmlFor: 'mode-select' }, 'Mode'),
      React.createElement(
        'select',
        { id: 'mode-select', value: props.mode, onChange: (evt: React.ChangeEvent<HTMLSelectElement>) => props.onMode(evt.currentTarget.value) },
        React.createElement('option', { value: 'dry-run' }, 'dry-run'),
      ),
    ),
    React.createElement(
      'div',
      { className: 'domain-preview', 'aria-label': 'Domain preview' },
      React.createElement(
        'svg',
        { viewBox: '0 0 360 190', role: 'img', 'aria-labelledby': 'domain-preview-title domain-preview-desc' },
        React.createElement('title', { id: 'domain-preview-title' }, 'Domain preview'),
        React.createElement('desc', { id: 'domain-preview-desc' }, 'Simplified rectangular preview of the selected simulation domain.'),
        React.createElement('rect', { className: 'map-bg', x: 10, y: 10, width: 340, height: 170, rx: 12 }),
        React.createElement('rect', { id: 'domain-box', className: 'domain-box', x: (360 - width) / 2, y: (190 - height) / 2, width, height, rx: 6 }),
        React.createElement('text', { id: 'domain-label', x: 180, y: 100, textAnchor: 'middle' }, label),
      ),
    ),
  );
}

function PreviewPanel(props: {
  canPreview: boolean;
  preview: PreviewResponse | null;
  message: string;
  messageKind: string;
  onPreview: () => void;
  onRun: () => void;
}) {
  const configText = props.preview?.config ? JSON.stringify(props.preview.config, null, 2) : 'No config preview yet.';
  return React.createElement(
    'section',
    { className: 'panel', 'aria-labelledby': 'job-title' },
    React.createElement('h2', { id: 'job-title' }, '3. Preview and run'),
    React.createElement(
      'div',
      { className: 'actions' },
      React.createElement('button', { id: 'preview-job', disabled: !props.canPreview, onClick: props.onPreview }, 'Preview job config'),
      React.createElement('button', { id: 'run-job', disabled: !props.preview?.valid, onClick: props.onRun }, 'Start dry-run'),
    ),
    React.createElement('div', { id: 'job-message', className: `message ${props.messageKind}`.trim(), 'aria-live': 'polite' }, props.message),
    React.createElement('h3', null, 'Generated config'),
    React.createElement('pre', { id: 'config-preview', className: 'code-box' }, configText),
  );
}

function StatusPanel(props: { job: JobSummary | null; logs: JobFile[]; onRefresh: () => void }) {
  const status = props.job?.status?.status || props.job?.status?.state || 'unknown';
  const logText = props.logs.length
    ? props.logs.map((log) => `# ${log.name}\n${(log as JobFile & { content?: string }).content || log.text || ''}`).join('\n\n')
    : 'No logs yet.';

  return React.createElement(
    'section',
    { className: 'panel wide', 'aria-labelledby': 'status-title' },
    React.createElement('h2', { id: 'status-title' }, '4. Job status and logs'),
    React.createElement(
      'div',
      { id: 'job-status', className: props.job ? undefined : 'empty-state' },
      props.job
        ? React.createElement(
            'dl',
            { className: 'meta-list' },
            React.createElement('dt', null, 'Job'),
            React.createElement('dd', null, props.job.job_id),
            React.createElement('dt', null, 'Status'),
            React.createElement('dd', null, status),
            React.createElement('dt', null, 'Run dir'),
            React.createElement('dd', null, props.job.run_dir || 'server managed'),
            React.createElement('dt', null, 'Logs'),
            React.createElement('dd', null, String(props.job.logs?.length || 0)),
            React.createElement('dt', null, 'Outputs'),
            React.createElement('dd', null, String(props.job.outputs?.length || 0)),
          )
        : 'No job has been started.',
    ),
    React.createElement('div', { className: 'actions' }, React.createElement('button', { id: 'refresh-job', disabled: !props.job, onClick: props.onRefresh }, 'Refresh status')),
    React.createElement('h3', null, 'Logs'),
    React.createElement('pre', { id: 'job-logs', className: 'code-box' }, logText),
  );
}

export function App() {
  const [apiStatus, setApiStatus] = useState('Checking API…');
  const [apiStatusKind, setApiStatusKind] = useState('');
  const [query, setQuery] = useState('Xaver');
  const [events, setEvents] = useState<WorkbenchEvent[]>([]);
  const [selectedEvent, setSelectedEvent] = useState<string | null>(null);
  const [detail, setDetail] = useState<EventDetailResponse | null>(null);
  const [domain, setDomain] = useState('');
  const [resolution, setResolution] = useState('');
  const [mode, setMode] = useState('dry-run');
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [job, setJob] = useState<JobSummary | null>(null);
  const [logs, setLogs] = useState<JobFile[]>([]);
  const [message, setMessage] = useState('');
  const [messageKind, setMessageKind] = useState('');

  const canPreview = useMemo(() => Boolean(selectedEvent && domain && resolution), [selectedEvent, domain, resolution]);

  async function runSearch(searchQuery = query) {
    try {
      const payload = await searchEvents(searchQuery.trim() || 'xaver');
      setEvents(payload.events || []);
    } catch (error) {
      setMessage(errorMessage(error, ERROR_MESSAGES.search));
      setMessageKind('bad');
    }
  }

  async function selectEvent(eventId: string) {
    try {
      setMessage('');
      setMessageKind('');
      const payload = await getEvent(eventId);
      setSelectedEvent(payload.event.id);
      setDetail(payload);
      setDomain(preferredPresetId(payload.event, payload.domain_presets, 'default_domain'));
      setResolution(preferredPresetId(payload.event, payload.resolution_presets, 'default_resolution_preset'));
      setPreview(null);
      setJob(null);
      setLogs([]);
    } catch (error) {
      setMessage(errorMessage(error, ERROR_MESSAGES.eventDetail));
      setMessageKind('bad');
    }
  }

  async function onPreview() {
    if (!selectedEvent) return;
    try {
      setMessage('Generating preview…');
      setMessageKind('warn');
      const payload = await previewJob({ event: selectedEvent, domain, resolution, mode, job_id: buildJobId(selectedEvent) });
      setPreview(payload);
      setMessage(payload.valid ? 'Preview is valid and ready to run.' : `Preview has validation errors: ${payload.errors.join('; ')}`);
      setMessageKind(payload.valid ? 'good' : 'bad');
    } catch (error) {
      setMessage(errorMessage(error, ERROR_MESSAGES.preview));
      setMessageKind('bad');
    }
  }

  async function onRun() {
    if (!preview?.config) return;
    try {
      setMessage('Starting dry-run…');
      setMessageKind('warn');
      const payload = await startJob(preview.config);
      setJob(payload.job);
      const logPayload = await getLogs(payload.job.job_id);
      setLogs(logPayload.logs || []);
      setMessage('Dry-run finished. Status and logs are available below.');
      setMessageKind(payload.ok ? 'good' : 'bad');
    } catch (error) {
      setMessage(errorMessage(error, ERROR_MESSAGES.run));
      setMessageKind('bad');
    }
  }

  async function onRefresh() {
    if (!job?.job_id) return;
    try {
      const payload = await getJob(job.job_id);
      setJob(payload.job);
      const logPayload = await getLogs(job.job_id);
      setLogs(logPayload.logs || []);
    } catch (error) {
      setMessage(errorMessage(error, ERROR_MESSAGES.refresh));
      setMessageKind('bad');
    }
  }

  useEffect(() => {
    getHealth()
      .then(() => {
        setApiStatus('API online');
        setApiStatusKind('good');
      })
      .catch((error) => {
        setApiStatus('API unavailable');
        setApiStatusKind('bad');
        setMessage(error.message);
        setMessageKind('bad');
      });
    runSearch('Xaver').catch((error) => {
      setMessage(errorMessage(error, ERROR_MESSAGES.search));
      setMessageKind('bad');
    });
  }, []);

  return React.createElement(
    React.Fragment,
    null,
    React.createElement(
      'header',
      { className: 'app-header' },
      React.createElement(
        'div',
        null,
        React.createElement('p', { className: 'eyebrow' }, 'Local WRF Workbench'),
        React.createElement('h1', null, 'Event to simulation'),
        React.createElement('p', { className: 'lede' }, 'Search an event, preview a job configuration, start a dry-run and inspect status/logs from the local API.'),
      ),
      React.createElement('span', { id: 'api-status', className: `status-pill ${apiStatusKind}`.trim() }, apiStatus),
    ),
    React.createElement(
      'main',
      { className: 'layout' },
      React.createElement(EventSearch, { query, events, onQueryChange: setQuery, onSearch: () => runSearch(), onSelect: selectEvent }),
      React.createElement(SelectPresets, {
        detail,
        domain,
        resolution,
        mode,
        onDomain: (value: string) => { setDomain(value); setPreview(null); },
        onResolution: (value: string) => { setResolution(value); setPreview(null); },
        onMode: setMode,
      }),
      React.createElement(PreviewPanel, { canPreview, preview, message, messageKind, onPreview, onRun }),
      React.createElement(StatusPanel, { job, logs, onRefresh }),
    ),
    React.createElement('footer', { className: 'app-footer' }, React.createElement('p', null, 'Same-origin local UI served by workbench.server.server. The browser calls only the local Workbench API.')),
  );
}
