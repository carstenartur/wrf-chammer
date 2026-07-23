const SIMULATION_ACTIVE = new Set([
  'PREPROCESSING',
  'INITIALIZING',
  'SIMULATING',
  'POSTPROCESSING',
  'CANCELLING',
]);

async function simulationRequestJson(path, init) {
  const response = await fetch(path, {
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error?.message || `HTTP ${response.status}`);
  }
  return payload;
}

function simulationEscape(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function simulationShort(value) {
  const text = String(value || '');
  return text.length > 22 ? `${text.slice(0, 14)}…${text.slice(-7)}` : text;
}

function simulationTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString();
}

class SimulationJobQueue extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.specifications = [];
    this.jobs = [];
    this.selectedSpecification = '';
    this.selectedJobId = '';
    this.selectedJobDetail = null;
    this.message = 'Loading immutable specifications and persistent simulations…';
    this.messageKind = 'pending';
    this.pollTimer = null;
  }

  connectedCallback() {
    this.render();
    this.refresh().catch((error) => this.showError(error));
  }

  disconnectedCallback() {
    if (this.pollTimer) clearTimeout(this.pollTimer);
  }

  get selectedJob() {
    if (this.selectedJobDetail?.id === this.selectedJobId) return this.selectedJobDetail;
    return this.jobs.find((job) => job.id === this.selectedJobId) || this.jobs[0] || null;
  }

  scheduleRefresh() {
    if (this.pollTimer) clearTimeout(this.pollTimer);
    if (this.jobs.some((job) => job.status === 'QUEUED' || SIMULATION_ACTIVE.has(job.status))) {
      this.pollTimer = setTimeout(() => {
        this.refresh({ quiet: true }).catch((error) => this.showError(error));
      }, 3000);
    }
  }

  async refreshSelectedJob() {
    if (!this.selectedJobId) {
      this.selectedJobDetail = null;
      return;
    }
    const payload = await simulationRequestJson(`/api/simulations/${encodeURIComponent(this.selectedJobId)}`);
    this.selectedJobDetail = payload.simulation;
    const index = this.jobs.findIndex((job) => job.id === this.selectedJobId);
    if (index >= 0) this.jobs[index] = { ...this.jobs[index], ...payload.simulation };
  }

  async refresh({ quiet = false } = {}) {
    if (!quiet) {
      this.message = 'Refreshing persistent simulation state…';
      this.messageKind = 'pending';
      this.render();
    }
    const [specifications, simulations] = await Promise.all([
      simulationRequestJson('/api/pipeline/specifications'),
      simulationRequestJson('/api/simulations'),
    ]);
    this.specifications = specifications.specifications || [];
    this.jobs = simulations.simulations || [];
    if (!this.specifications.some((spec) => spec.specification_key === this.selectedSpecification)) {
      this.selectedSpecification = this.specifications[0]?.specification_key || '';
    }
    if (!this.jobs.some((job) => job.id === this.selectedJobId)) {
      this.selectedJobId = this.jobs[0]?.id || '';
      this.selectedJobDetail = null;
    }
    await this.refreshSelectedJob();
    if (!quiet) {
      this.message = this.specifications.length
        ? 'Create a persistent job, then queue it explicitly. A separate worker is required for execution.'
        : 'Freeze an immutable real pipeline specification before creating a simulation job.';
      this.messageKind = this.specifications.length ? 'success' : 'warning';
    }
    this.render();
    this.scheduleRefresh();
  }

  async createJob() {
    if (!this.selectedSpecification) return;
    this.message = 'Creating a persistent READY simulation from the immutable specification…';
    this.messageKind = 'pending';
    this.render();
    try {
      const payload = await simulationRequestJson('/api/simulations', {
        method: 'POST',
        body: JSON.stringify({ specification_key: this.selectedSpecification }),
      });
      this.selectedJobId = payload.simulation.id;
      this.selectedJobDetail = payload.simulation;
      this.message = 'Simulation record created in READY state. No worker process has started.';
      this.messageKind = 'success';
      await this.refresh({ quiet: true });
    } catch (error) {
      this.showError(error);
    }
  }

  async act(jobId, action) {
    const labels = {
      enqueue: 'Queueing the simulation for a worker…',
      cancel: 'Requesting safe cancellation…',
      retry: 'Creating a retry attempt after the failed or cancelled job…',
      reproduce: 'Creating an exact READY reproduction from the immutable specification…',
    };
    this.message = labels[action] || 'Updating simulation…';
    this.messageKind = 'pending';
    this.render();
    try {
      const payload = await simulationRequestJson(`/api/simulations/${encodeURIComponent(jobId)}/${action}`, {
        method: 'POST',
      });
      this.selectedJobId = payload.simulation.id;
      this.selectedJobDetail = payload.simulation;
      this.message = action === 'enqueue'
        ? 'Simulation is queued. It will remain queued until a separate worker claims it.'
        : action === 'retry'
          ? 'A new READY retry attempt was created; the failed or cancelled source remains unchanged.'
          : action === 'reproduce'
            ? 'A new exact READY reproduction was created from the same immutable specification. It has not been queued.'
            : 'Cancellation state was persisted.';
      this.messageKind = 'success';
      await this.refresh({ quiet: true });
    } catch (error) {
      this.showError(error);
    }
  }

  async selectJob(jobId) {
    this.selectedJobId = jobId;
    this.selectedJobDetail = null;
    this.render();
    try {
      await this.refreshSelectedJob();
      this.render();
    } catch (error) {
      this.showError(error);
    }
  }

  showError(error) {
    this.message = error instanceof Error ? error.message : 'Persistent simulation operation failed.';
    this.messageKind = 'error';
    this.render();
  }

  bindEvents() {
    this.shadowRoot.getElementById('simulation-refresh')?.addEventListener('click', () => this.refresh());
    this.shadowRoot.getElementById('simulation-create')?.addEventListener('click', () => this.createJob());
    this.shadowRoot.getElementById('simulation-specification')?.addEventListener('change', (event) => {
      this.selectedSpecification = event.target.value;
      this.render();
    });
    this.shadowRoot.querySelectorAll('[data-select-job]').forEach((button) => {
      button.addEventListener('click', () => this.selectJob(button.dataset.selectJob));
    });
    this.shadowRoot.querySelectorAll('[data-job-action]').forEach((button) => {
      button.addEventListener('click', () => this.act(button.dataset.jobId, button.dataset.jobAction));
    });
  }

  renderActions(job) {
    if (!job) return '';
    const actions = [['reproduce', 'Reproduce exact run']];
    if (job.status === 'READY') actions.push(['enqueue', 'Queue for worker']);
    if (job.cancellable) actions.push(['cancel', job.status === 'READY' || job.status === 'QUEUED' ? 'Cancel' : 'Cancel safely']);
    if (job.retryable) actions.push(['retry', 'Create retry']);
    const manifestPath = `/api/simulations/${encodeURIComponent(job.id)}/run-manifest`;
    const manifestName = `wrf-chammer-run-manifest-${job.id}.json`;
    return [
      `<a class="action-link secondary" href="${simulationEscape(manifestPath)}" download="${simulationEscape(manifestName)}">Download run manifest</a>`,
      ...actions.map(([action, label]) => (
        `<button type="button" data-job-action="${action}" data-job-id="${simulationEscape(job.id)}" class="${action === 'cancel' ? 'danger' : action === 'reproduce' ? 'secondary' : ''}">${simulationEscape(label)}</button>`
      )),
    ].join('');
  }

  renderJobDetails(job) {
    if (!job) return '<p class="empty">No persistent simulation has been created.</p>';
    return `
      <article class="details">
        <div class="details-heading">
          <div><span class="status status-${simulationEscape(job.status.toLowerCase())}">${simulationEscape(job.status)}</span><h3>${simulationEscape(job.id)}</h3></div>
          <div class="actions">${this.renderActions(job)}</div>
        </div>
        <dl>
          <dt>Immutable specification</dt><dd><code>${simulationEscape(job.specification_key)}</code></dd>
          <dt>Retry of</dt><dd>${job.retry_of ? `<code>${simulationEscape(job.retry_of)}</code>` : '—'}</dd>
          <dt>Reproduced from</dt><dd>${job.reproduced_from ? `<code>${simulationEscape(job.reproduced_from)}</code>` : '—'}</dd>
          <dt>Exact reproductions</dt><dd>${(job.reproductions || []).length ? job.reproductions.map((id) => `<code>${simulationEscape(id)}</code>`).join('<br>') : '—'}</dd>
          <dt>Created</dt><dd>${simulationEscape(simulationTime(job.created_at))}</dd>
          <dt>Queued</dt><dd>${simulationEscape(simulationTime(job.queued_at))}</dd>
          <dt>Started</dt><dd>${simulationEscape(simulationTime(job.started_at))}</dd>
          <dt>Worker</dt><dd>${simulationEscape(job.worker_id || 'not claimed')}</dd>
          <dt>Current step</dt><dd>${simulationEscape(job.current_step_id || 'none')}</dd>
        </dl>
        ${job.error ? `<p class="error-box"><strong>${simulationEscape(job.error.code)}</strong> ${simulationEscape(job.error.message)}</p>` : ''}
        <h4>Pipeline steps</h4>
        <ol class="steps">
          ${(job.steps || []).map((step) => `
            <li class="step step-${simulationEscape(step.status.toLowerCase())}">
              <span>${Number(step.position) + 1}</span>
              <div><strong>${simulationEscape(step.label)}</strong><small>${simulationEscape(step.id)} · attempt ${simulationEscape(step.attempt)}</small></div>
              <b>${simulationEscape(step.status)}</b>
            </li>
          `).join('')}
        </ol>
        <div class="facts">
          <section><h4>Input datasets</h4><p>${(job.input_datasets || []).map((input) => `<code>${simulationEscape(simulationShort(input.plan_key))}</code>`).join('<br>') || '—'}</p></section>
          <section><h4>Runtime snapshots</h4><p>${(job.runtime_snapshots || []).map((runtime) => `${simulationEscape(runtime.name)}: <code>${simulationEscape(simulationShort(runtime.identity))}</code>`).join('<br>') || '—'}</p></section>
          <section><h4>Artifacts</h4><p>${simulationEscape((job.artifacts || []).length)} indexed</p></section>
          <section><h4>Measurements</h4><p>${simulationEscape((job.resource_measurements || []).length)} recorded</p></section>
        </div>
        <h4>Recent events</h4>
        <ol class="events">
          ${(job.events || []).slice(-8).reverse().map((event) => `<li><time>${simulationEscape(simulationTime(event.timestamp))}</time><strong>${simulationEscape(event.type)}</strong><span>${simulationEscape(event.message)}</span></li>`).join('') || '<li>No events</li>'}
        </ol>
        <p class="honesty"><strong>Exact reproduction is not execution.</strong> It creates a new READY record only when the same immutable specification and verified ERA5 input remain available. A separate worker must still be queued explicitly.</p>
      </article>
    `;
  }

  render() {
    const selectedJob = this.selectedJob;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; max-width:1180px; margin:1.25rem auto 3rem; padding:0 1rem; color:#14213d; font-family:Inter,ui-sans-serif,system-ui,sans-serif; }
        .panel { border:1px solid #cbd9e7; border-radius:18px; padding:1.35rem; background:#f7fbff; box-shadow:0 12px 32px rgba(28,57,94,.08); }
        header,.details-heading,.actions { display:flex; justify-content:space-between; gap:1rem; align-items:flex-start; flex-wrap:wrap; }
        .eyebrow { margin:0 0 .3rem; color:#315b85; font-size:.76rem; font-weight:750; text-transform:uppercase; letter-spacing:.04em; }
        h2,h3,h4,p { margin-top:0; } h3 { font-size:.95rem; overflow-wrap:anywhere; margin:.5rem 0; }
        button,.action-link { border:0; border-radius:9px; padding:.65rem .9rem; background:#1d5f9d; color:white; font:inherit; font-weight:700; cursor:pointer; }
        .action-link { display:inline-block; box-sizing:border-box; text-decoration:none; }
        button.secondary,.action-link.secondary { background:white; color:#1d5f9d; border:1px solid #9ebddd; } button.danger { background:#9a3636; } button:disabled { opacity:.48; cursor:not-allowed; }
        .create { display:grid; grid-template-columns:minmax(260px,1fr) max-content; gap:.75rem; align-items:end; margin:1rem 0; }
        label { display:grid; gap:.3rem; color:#40566d; font-weight:650; } select { min-width:0; border:1px solid #aebfd0; border-radius:8px; padding:.65rem; background:white; font:inherit; }
        .message { min-height:1.3rem; color:#40566d; } .message.success { color:#17663b; font-weight:650; } .message.warning { color:#845700; font-weight:650; } .message.error { color:#982424; font-weight:650; }
        .layout { display:grid; grid-template-columns:minmax(220px,.7fr) minmax(0,2fr); gap:1rem; margin-top:1rem; }
        .history { display:grid; gap:.5rem; align-content:start; max-height:720px; overflow:auto; }
        .job-card { display:grid; text-align:left; background:white; color:#14213d; border:1px solid #d8e3ed; border-radius:10px; padding:.65rem; }
        .job-card.selected { border-color:#1d5f9d; box-shadow:0 0 0 2px #d8eafa; } .job-card small { color:#64798d; margin-top:.2rem; }
        .details { background:white; border:1px solid #d8e3ed; border-radius:13px; padding:1rem; min-width:0; }
        .status { display:inline-block; border-radius:999px; padding:.2rem .55rem; font-size:.74rem; font-weight:800; background:#e6edf4; } .status-ready { background:#e8f3ff; color:#194e7d; } .status-queued { background:#fff3d3; color:#745300; } .status-succeeded { background:#e1f3e8; color:#17663b; } .status-failed,.status-cancelled { background:#f8e2e2; color:#862626; }
        dl { display:grid; grid-template-columns:max-content 1fr; gap:.3rem .75rem; } dt { color:#64798d; font-weight:650; } dd { margin:0; overflow-wrap:anywhere; }
        .steps,.events { list-style:none; padding:0; display:grid; gap:.45rem; } .step { display:grid; grid-template-columns:2rem 1fr max-content; gap:.6rem; align-items:center; border:1px solid #e1e8ef; border-radius:8px; padding:.55rem; } .step>span { width:1.7rem; height:1.7rem; border-radius:50%; display:grid; place-items:center; background:#edf3f8; font-weight:750; } .step small { display:block; color:#64798d; margin-top:.15rem; } .step b { font-size:.75rem; }
        .facts { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:.6rem; } .facts section { border:1px solid #e1e8ef; border-radius:8px; padding:.65rem; } .facts h4 { margin-bottom:.35rem; }
        .events li { display:grid; grid-template-columns:minmax(135px,max-content) minmax(110px,max-content) 1fr; gap:.55rem; font-size:.84rem; } .events time { color:#64798d; }
        .honesty { margin:.8rem 0 0; padding:.7rem; border-radius:8px; background:#fff7df; color:#674900; } .error-box { padding:.65rem; border-radius:8px; background:#f8e2e2; color:#862626; }
        .empty { color:#64798d; } code { overflow-wrap:anywhere; }
        @media(max-width:850px){ .layout{grid-template-columns:1fr}.history{max-height:260px}.create{grid-template-columns:1fr}.events li{grid-template-columns:1fr} }
      </style>
      <section class="panel" aria-labelledby="simulation-queue-title">
        <header><div><p class="eyebrow">Step 5 · Persistent execution queue</p><h2 id="simulation-queue-title">Create and queue real simulation jobs</h2><p>Every job references one immutable specification. Queueing persists intent; it does not pretend that WPS or WRF has executed.</p></div><button id="simulation-refresh" type="button" class="secondary">Refresh jobs</button></header>
        <div class="create"><label>Immutable specification<select id="simulation-specification" ${this.specifications.length ? '' : 'disabled'}>${this.specifications.length ? this.specifications.map((spec) => `<option value="${simulationEscape(spec.specification_key)}" ${spec.specification_key === this.selectedSpecification ? 'selected' : ''}>${simulationEscape(simulationShort(spec.specification_key))} · ${simulationEscape(spec.identity?.job?.name || spec.identity?.job?.id || 'real run')}</option>`).join('') : '<option value="">No immutable specification</option>'}</select></label><button id="simulation-create" type="button" ${this.selectedSpecification ? '' : 'disabled'}>Create READY job</button></div>
        <p class="message ${simulationEscape(this.messageKind)}" aria-live="polite">${simulationEscape(this.message)}</p>
        <div class="layout"><nav class="history" aria-label="Persistent simulations">${this.jobs.length ? this.jobs.map((job) => `<button type="button" class="job-card ${job.id === selectedJob?.id ? 'selected' : ''}" data-select-job="${simulationEscape(job.id)}"><strong>${simulationEscape(job.status)}</strong><span>${simulationEscape(simulationShort(job.id))}</span><small>${simulationEscape(simulationTime(job.created_at))}</small></button>`).join('') : '<p class="empty">No jobs yet.</p>'}</nav>${this.renderJobDetails(selectedJob)}</div>
      </section>
    `;
    this.bindEvents();
  }
}

if (!customElements.get('simulation-job-queue')) {
  customElements.define('simulation-job-queue', SimulationJobQueue);
}
