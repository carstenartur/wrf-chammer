const state = {
  selectedEvent: null,
  eventDetail: null,
  previewConfig: null,
  activeJobId: null,
};

const elements = {
  apiStatus: document.getElementById('api-status'),
  searchForm: document.getElementById('search-form'),
  eventQuery: document.getElementById('event-query'),
  eventResults: document.getElementById('event-results'),
  eventDetail: document.getElementById('event-detail'),
  domainSelect: document.getElementById('domain-select'),
  resolutionSelect: document.getElementById('resolution-select'),
  modeSelect: document.getElementById('mode-select'),
  domainBox: document.getElementById('domain-box'),
  domainLabel: document.getElementById('domain-label'),
  previewButton: document.getElementById('preview-job'),
  runButton: document.getElementById('run-job'),
  refreshButton: document.getElementById('refresh-job'),
  message: document.getElementById('job-message'),
  configPreview: document.getElementById('config-preview'),
  jobStatus: document.getElementById('job-status'),
  jobLogs: document.getElementById('job-logs'),
};

function setApiStatus(text, kind = '') {
  elements.apiStatus.textContent = text;
  elements.apiStatus.className = `status-pill ${kind}`.trim();
}

function setMessage(text, kind = '') {
  elements.message.textContent = text;
  elements.message.className = `message ${kind}`.trim();
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body ? {'Content-Type': 'application/json'} : undefined,
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    const message = payload?.error?.message || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

function formatJson(value) {
  return JSON.stringify(value, null, 2);
}

function eventPeriod(event) {
  if (event.period?.start && event.period?.end) {
    return `${event.period.start} → ${event.period.end}`;
  }
  if (event.start && event.end) {
    return `${event.start} → ${event.end}`;
  }
  return 'No period available';
}

function renderEvents(events) {
  elements.eventResults.innerHTML = '';
  if (!events.length) {
    elements.eventResults.innerHTML = '<div class="empty-state">No matching events found.</div>';
    return;
  }

  for (const event of events) {
    const card = document.createElement('article');
    card.className = 'event-card';
    card.innerHTML = `
      <h3>${event.name || event.id}</h3>
      <p>${event.description || 'No description available.'}</p>
      <button type="button" data-event-id="${event.id}">Select ${event.id}</button>
    `;
    card.querySelector('button').addEventListener('click', () => selectEvent(event.id));
    elements.eventResults.appendChild(card);
  }
}

function fillSelect(select, values, labelFn) {
  select.innerHTML = '';
  for (const value of values) {
    const option = document.createElement('option');
    option.value = value.id;
    option.textContent = labelFn(value);
    select.appendChild(option);
  }
  select.disabled = values.length === 0;
}

function preferredPresetId(event, items, preferredField) {
  const preferred = event?.[preferredField];
  if (preferred && items.some((item) => item.id === preferred)) {
    return preferred;
  }
  return items[0]?.id || '';
}

function renderEventDetail(detail) {
  const event = detail.event;
  elements.eventDetail.innerHTML = `
    <dl class="meta-list">
      <dt>Event</dt><dd>${event.name || event.id}</dd>
      <dt>Type</dt><dd>${event.event_type || 'n/a'}</dd>
      <dt>Period</dt><dd>${eventPeriod(event)}</dd>
      <dt>Outputs</dt><dd>${(event.suggested_outputs || []).join(', ') || 'n/a'}</dd>
    </dl>
  `;

  fillSelect(elements.domainSelect, detail.domain_presets || [], (domain) => `${domain.label || domain.id} (${domain.dx_km} km)`);
  fillSelect(elements.resolutionSelect, detail.resolution_presets || [], (preset) => `${preset.label || preset.id}`);

  elements.domainSelect.value = preferredPresetId(event, detail.domain_presets || [], 'default_domain');
  elements.resolutionSelect.value = preferredPresetId(event, detail.resolution_presets || [], 'default_resolution_preset');
  elements.previewButton.disabled = false;
  renderDomainPreview();
}

function selectedDomain() {
  const id = elements.domainSelect.value;
  return (state.eventDetail?.domain_presets || []).find((domain) => domain.id === id) || null;
}

function renderDomainPreview() {
  const domain = selectedDomain();
  if (!domain) {
    elements.domainBox.setAttribute('x', '80');
    elements.domainBox.setAttribute('y', '45');
    elements.domainBox.setAttribute('width', '200');
    elements.domainBox.setAttribute('height', '100');
    elements.domainLabel.textContent = 'No domain selected';
    return;
  }

  const cellCount = Math.max(1, Number(domain.e_we || 1) * Number(domain.e_sn || 1));
  const scale = Math.min(1, Math.sqrt(cellCount / 12000));
  const width = Math.max(90, 80 + 200 * scale);
  const height = Math.max(55, 50 + 110 * scale);
  elements.domainBox.setAttribute('x', String((360 - width) / 2));
  elements.domainBox.setAttribute('y', String((190 - height) / 2));
  elements.domainBox.setAttribute('width', String(width));
  elements.domainBox.setAttribute('height', String(height));
  elements.domainLabel.textContent = `${domain.id}: ${domain.e_we} × ${domain.e_sn} @ ${domain.dx_km} km`;
}

async function searchEvents(query) {
  const payload = await api(`/api/events?q=${encodeURIComponent(query)}`);
  renderEvents(payload.events || []);
}

async function selectEvent(eventId) {
  setMessage('', '');
  const detail = await api(`/api/events/${encodeURIComponent(eventId)}`);
  state.selectedEvent = detail.event.id;
  state.eventDetail = detail;
  state.previewConfig = null;
  state.activeJobId = null;
  elements.configPreview.textContent = 'No config preview yet.';
  elements.runButton.disabled = true;
  elements.refreshButton.disabled = true;
  elements.jobStatus.textContent = 'No job has been started.';
  elements.jobLogs.textContent = 'No logs yet.';
  renderEventDetail(detail);
}

function buildJobId() {
  const eventId = state.selectedEvent || 'event';
  return `${eventId}-ui-dry-run`;
}

async function previewJob() {
  if (!state.selectedEvent) return;
  setMessage('Generating preview…', 'warn');
  const payload = await api('/api/jobs/preview', {
    method: 'POST',
    body: JSON.stringify({
      event: state.selectedEvent,
      domain: elements.domainSelect.value,
      resolution: elements.resolutionSelect.value,
      mode: elements.modeSelect.value,
      job_id: buildJobId(),
    }),
  });
  state.previewConfig = payload.config;
  state.activeJobId = payload.config.id;
  elements.configPreview.textContent = formatJson(payload.config);
  elements.runButton.disabled = !payload.valid;
  setMessage(payload.valid ? 'Preview is valid and ready to run.' : `Preview has validation errors: ${(payload.errors || []).join('; ')}`, payload.valid ? 'good' : 'bad');
}

async function runJob() {
  if (!state.previewConfig) return;
  setMessage('Starting dry-run…', 'warn');
  const payload = await api('/api/jobs', {
    method: 'POST',
    body: JSON.stringify({config: state.previewConfig, start: true}),
  });
  state.activeJobId = payload.job.job_id;
  elements.refreshButton.disabled = false;
  renderJob(payload.job);
  await loadLogs(state.activeJobId);
  setMessage('Dry-run finished. Status and logs are available below.', payload.ok ? 'good' : 'bad');
}

function renderJob(job) {
  const status = job.status?.status || job.status?.state || 'unknown';
  const outputCount = job.outputs?.length || 0;
  const logCount = job.logs?.length || 0;
  elements.jobStatus.innerHTML = `
    <dl class="meta-list">
      <dt>Job</dt><dd>${job.job_id}</dd>
      <dt>Status</dt><dd>${status}</dd>
      <dt>Run dir</dt><dd>${job.run_dir || 'server managed'}</dd>
      <dt>Logs</dt><dd>${logCount}</dd>
      <dt>Outputs</dt><dd>${outputCount}</dd>
    </dl>
  `;
}

async function refreshJob() {
  if (!state.activeJobId) return;
  const payload = await api(`/api/jobs/${encodeURIComponent(state.activeJobId)}`);
  renderJob(payload.job);
  await loadLogs(state.activeJobId);
}

async function loadLogs(jobId) {
  const payload = await api(`/api/jobs/${encodeURIComponent(jobId)}/logs`);
  const logs = payload.logs || [];
  if (!logs.length) {
    elements.jobLogs.textContent = 'No logs returned.';
    return;
  }
  elements.jobLogs.textContent = logs.map((log) => `# ${log.name}\n${log.content || ''}`).join('\n\n');
}

async function checkHealth() {
  try {
    await api('/api/health');
    setApiStatus('API online', 'good');
  } catch (error) {
    setApiStatus('API unavailable', 'bad');
    setMessage(error.message, 'bad');
  }
}

elements.searchForm.addEventListener('submit', (event) => {
  event.preventDefault();
  searchEvents(elements.eventQuery.value.trim() || 'xaver').catch((error) => setMessage(error.message, 'bad'));
});

elements.domainSelect.addEventListener('change', () => {
  state.previewConfig = null;
  elements.runButton.disabled = true;
  elements.configPreview.textContent = 'Preset changed. Generate a new preview.';
  renderDomainPreview();
});

elements.resolutionSelect.addEventListener('change', () => {
  state.previewConfig = null;
  elements.runButton.disabled = true;
  elements.configPreview.textContent = 'Preset changed. Generate a new preview.';
});

elements.previewButton.addEventListener('click', () => previewJob().catch((error) => setMessage(error.message, 'bad')));
elements.runButton.addEventListener('click', () => runJob().catch((error) => setMessage(error.message, 'bad')));
elements.refreshButton.addEventListener('click', () => refreshJob().catch((error) => setMessage(error.message, 'bad')));

checkHealth().then(() => searchEvents(elements.eventQuery.value));
