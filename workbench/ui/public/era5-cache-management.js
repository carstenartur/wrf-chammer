async function cacheRequestJson(path, init) {
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

function cacheEscape(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function cacheFormatBytes(bytes) {
  const value = Number(bytes || 0);
  if (!Number.isFinite(value) || value <= 0) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** index).toFixed(index > 1 ? 2 : 0)} ${units[index]}`;
}

function cacheFormatPeriod(period) {
  if (!period?.start || !period?.end) return 'Period unavailable';
  return `${period.start} → ${period.end}`;
}

function cacheDependencySummary(jobs, emptyMessage) {
  if (!jobs.length) return emptyMessage;
  return jobs.map((job) => {
    const marker = job.blocking ? ' — blocks deletion' : '';
    return `${job.id}: ${job.status}${marker}`;
  }).join(', ');
}

class Era5CacheManagement extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.entries = [];
    this.message = 'Loading managed ERA5 cache entries…';
    this.messageKind = 'pending';
  }

  connectedCallback() {
    this.render();
    this.refresh().catch((error) => this.showError(error));
  }

  async refresh() {
    this.message = 'Refreshing managed ERA5 cache…';
    this.messageKind = 'pending';
    this.render();
    try {
      const payload = await cacheRequestJson('/api/data/era5/cache');
      this.entries = payload.entries || [];
      this.message = this.entries.length
        ? `${this.entries.length} content-addressed ERA5 cache entr${this.entries.length === 1 ? 'y' : 'ies'} found.`
        : 'The managed ERA5 cache is empty.';
      this.messageKind = 'success';
      this.render();
    } catch (error) {
      this.showError(error);
    }
  }

  async deleteEntry(planKey) {
    const entry = this.entries.find((candidate) => candidate.plan_key === planKey);
    if (!entry || !entry.deletion?.allowed) return;
    const dependentDownloads = entry.deletion.confirmation?.dependent_job_ids || [];
    const dependentSimulations = entry.deletion.confirmation?.dependent_simulation_ids || [];
    const warning = [
      `Delete ERA5 cache entry ${planKey}?`,
      `This releases ${cacheFormatBytes(entry.storage?.size_bytes)} and permanently removes prepared requests, verified files and manifests.`,
      `${dependentDownloads.length} ERA5 download-job record(s) and ${dependentSimulations.length} persistent simulation record(s) reference this plan. Completed records remain in history, but their input files can no longer be reused or independently reverified from this cache.`,
      'This operation cannot be undone.',
    ].join('\n\n');
    if (!window.confirm(warning)) return;

    this.message = `Deleting cache entry ${planKey.slice(0, 12)}…`;
    this.messageKind = 'pending';
    this.render();
    try {
      const payload = await cacheRequestJson(
        `/api/data/era5/cache/${encodeURIComponent(planKey)}/delete`,
        {
          method: 'POST',
          body: JSON.stringify({
            confirm_plan_key: planKey,
            dependent_job_ids: dependentDownloads,
            dependent_simulation_ids: dependentSimulations,
          }),
        },
      );
      this.message = `Deleted ${planKey.slice(0, 12)} and released ${cacheFormatBytes(payload.deleted?.released_bytes)}.`;
      this.messageKind = 'success';
      await this.refresh();
    } catch (error) {
      this.showError(error);
    }
  }

  showError(error) {
    this.message = error instanceof Error ? error.message : 'ERA5 cache management failed.';
    this.messageKind = 'error';
    this.render();
  }

  bindEvents() {
    this.shadowRoot.getElementById('era5-cache-refresh')?.addEventListener('click', () => this.refresh());
    this.shadowRoot.querySelectorAll('[data-cache-delete]').forEach((button) => {
      button.addEventListener('click', () => this.deleteEntry(button.getAttribute('data-cache-delete')));
    });
  }

  renderEntry(entry) {
    const deletion = entry.deletion || {};
    const downloads = entry.dependencies?.download_jobs || [];
    const simulations = entry.dependencies?.simulation_jobs || [];
    const downloadSummary = cacheDependencySummary(
      downloads,
      'No persistent download jobs depend on this entry.',
    );
    const simulationSummary = cacheDependencySummary(
      simulations,
      'No persistent simulations depend on this entry.',
    );
    return `
      <article class="entry ${entry.status === 'invalid' ? 'invalid' : ''}">
        <div class="entry-heading">
          <div>
            <span class="status">${cacheEscape(entry.status)}</span>
            <h3><code>${cacheEscape(entry.plan_key)}</code></h3>
          </div>
          <button
            type="button"
            class="danger"
            data-cache-delete="${cacheEscape(entry.plan_key)}"
            ${deletion.allowed ? '' : 'disabled'}
          >Delete cache entry</button>
        </div>
        <div class="metrics">
          <div><span>Stored data</span><strong>${cacheFormatBytes(entry.storage?.size_bytes)}</strong><small>${Number(entry.storage?.file_count || 0)} files</small></div>
          <div><span>Coverage</span><strong>${Number(entry.coverage?.percent || 0)}%</strong><small>${Number(entry.coverage?.hits || 0)} / ${Number(entry.coverage?.total || 0)} requests</small></div>
          <div><span>Last used</span><strong>${cacheEscape(entry.last_used_at || 'unknown')}</strong><small>${entry.age_days == null ? 'age unknown' : `${entry.age_days} days ago`}</small></div>
          <div><span>Download jobs</span><strong>${Number(entry.dependencies?.download_job_count || 0)}</strong><small>${Number(entry.dependencies?.active_download_job_count || 0)} active</small></div>
          <div><span>Simulations</span><strong>${Number(entry.dependencies?.simulation_job_count || 0)}</strong><small>${Number(entry.dependencies?.blocking_simulation_job_count || 0)} blocking</small></div>
        </div>
        <dl>
          <dt>Period</dt><dd>${cacheEscape(cacheFormatPeriod(entry.period))}</dd>
          <dt>Source</dt><dd>${cacheEscape(entry.provenance?.source || 'unavailable')}</dd>
          <dt>Checksums</dt><dd>${entry.provenance?.checksums_available ? 'available' : 'not yet available'}</dd>
          <dt>Download dependencies</dt><dd>${cacheEscape(downloadSummary)}</dd>
          <dt>Simulation dependencies</dt><dd>${cacheEscape(simulationSummary)}</dd>
        </dl>
        ${deletion.blocked_reason ? `<p class="blocked">Deletion blocked: ${cacheEscape(deletion.blocked_reason)}</p>` : ''}
      </article>
    `;
  }

  render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; max-width: 1180px; margin: 1.25rem auto 3rem; padding: 0 1rem; color: #14213d; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
        section { border: 1px solid #d5dfeb; border-radius: 18px; background: #fbfcfe; padding: 1.35rem; box-shadow: 0 12px 30px rgba(28,57,94,.06); }
        header { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; flex-wrap: wrap; }
        .eyebrow { margin: 0 0 .3rem; color: #536d87; font-size: .76rem; font-weight: 750; letter-spacing: .04em; text-transform: uppercase; }
        h2 { margin: 0; font-size: 1.45rem; }
        header p:last-child { max-width: 760px; margin: .45rem 0 0; color: #40566d; }
        button { border: 1px solid #9eb4cb; border-radius: 9px; padding: .65rem .9rem; background: white; color: #214d76; font: inherit; font-weight: 700; cursor: pointer; }
        button.danger { border-color: #b65b5b; color: #8c2828; }
        button:disabled { cursor: not-allowed; opacity: .48; }
        .message { min-height: 1.3em; color: #40566d; }
        .message.error { color: #982424; font-weight: 650; }
        .message.success { color: #17663b; }
        .entries { display: grid; gap: .9rem; }
        .entry { border: 1px solid #d9e3ed; border-radius: 13px; background: white; padding: 1rem; }
        .entry.invalid { border-color: #d9a4a4; }
        .entry-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; flex-wrap: wrap; }
        .status { display: inline-block; border-radius: 999px; background: #eaf2fb; padding: .2rem .55rem; font-size: .75rem; font-weight: 750; text-transform: uppercase; }
        h3 { margin: .45rem 0 0; font-size: .95rem; overflow-wrap: anywhere; }
        .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(155px, 1fr)); gap: .65rem; margin: .9rem 0; }
        .metrics div { border-radius: 9px; background: #f4f7fa; padding: .65rem; }
        .metrics span, .metrics small { display: block; color: #566d83; }
        .metrics strong { display: block; margin: .15rem 0; }
        dl { display: grid; grid-template-columns: max-content 1fr; gap: .3rem .75rem; margin: 0; }
        dt { color: #566d83; font-weight: 650; }
        dd { margin: 0; overflow-wrap: anywhere; }
        .blocked { margin: .8rem 0 0; color: #8c2828; font-weight: 650; }
        .empty { margin: 1rem 0 0; color: #566d83; }
      </style>
      <section aria-labelledby="era5-cache-title">
        <header>
          <div>
            <p class="eyebrow">Data administration</p>
            <h2 id="era5-cache-title">Managed ERA5 cache</h2>
            <p>Inspect global storage, age, provenance, download jobs and persistent simulations. Deletion requires fresh dependency snapshots and is blocked while a download or simulation still needs the files.</p>
          </div>
          <button id="era5-cache-refresh" type="button">Refresh cache</button>
        </header>
        <p class="message ${cacheEscape(this.messageKind)}" aria-live="polite">${cacheEscape(this.message)}</p>
        <div class="entries">
          ${this.entries.length ? this.entries.map((entry) => this.renderEntry(entry)).join('') : '<p class="empty">No content-addressed ERA5 cache entries are currently stored.</p>'}
        </div>
      </section>
    `;
    this.bindEvents();
  }
}

if (!customElements.get('era5-cache-management')) {
  customElements.define('era5-cache-management', Era5CacheManagement);
}
