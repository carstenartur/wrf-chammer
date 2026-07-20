const ACTIVE_STATUSES = new Set(['QUEUED', 'RUNNING', 'CANCELLING']);

async function requestJson(path, init) {
  const response = await fetch(path, {
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error?.message || payload?.errors?.join('; ') || `HTTP ${response.status}`);
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function statusLabel(status) {
  return {
    QUEUED: 'Waiting for worker',
    RUNNING: 'Downloading / verifying',
    CANCELLING: 'Cancelling',
    CANCELLED: 'Cancelled',
    FAILED: 'Failed',
    SUCCEEDED: 'Ready',
  }[status] || status || 'Not started';
}

class Era5DownloadControl extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.download = null;
    this.events = [];
    this.readiness = null;
    this.message = 'No ERA5 download has been started from this browser session.';
    this.messageKind = '';
    this.pollTimer = null;
  }

  connectedCallback() {
    this.render();
    this.refresh().catch((error) => this.showError(error));
  }

  disconnectedCallback() {
    this.stopPolling();
  }

  async refresh() {
    const [status, downloads] = await Promise.all([
      requestJson('/api/data/era5/status'),
      requestJson('/api/data/era5/downloads'),
    ]);
    this.readiness = status;
    const currentId = this.download?.id;
    const current = currentId
      ? downloads.downloads.find((entry) => entry.id === currentId)
      : downloads.downloads[0];
    this.download = current || null;
    if (this.download) {
      await this.refreshCurrent(false);
    } else {
      this.render();
    }
  }

  async refreshCurrent(renderImmediately = true) {
    if (!this.download?.id) {
      if (renderImmediately) this.render();
      return;
    }
    const encodedId = encodeURIComponent(this.download.id);
    const [jobPayload, eventPayload] = await Promise.all([
      requestJson(`/api/data/era5/downloads/${encodedId}`),
      requestJson(`/api/data/era5/downloads/${encodedId}/events`),
    ]);
    this.download = jobPayload.download;
    this.events = eventPayload.events || [];
    if (ACTIVE_STATUSES.has(this.download.status)) {
      this.startPolling();
    } else {
      this.stopPolling();
    }
    this.render();
  }

  async startDownload() {
    this.message = 'Queuing the real ERA5 download…';
    this.messageKind = 'pending';
    this.render();
    try {
      const payload = await requestJson('/api/data/era5/downloads', {
        method: 'POST',
        body: JSON.stringify({
          source: 'latest-wizard-preview',
          interval_hours: 1,
          margin_degrees: 1,
        }),
      });
      this.download = payload.download;
      this.events = [];
      this.message = 'The download job is persistent. You may reload or close the browser.';
      this.messageKind = 'success';
      this.startPolling();
      await this.refreshCurrent();
    } catch (error) {
      this.showError(error);
    }
  }

  async cancelDownload() {
    if (!this.download?.id) return;
    this.message = 'Requesting a controlled stop…';
    this.messageKind = 'pending';
    this.render();
    try {
      const payload = await requestJson(
        `/api/data/era5/downloads/${encodeURIComponent(this.download.id)}/cancel`,
        { method: 'POST', body: '{}' },
      );
      this.download = payload.download;
      await this.refreshCurrent();
    } catch (error) {
      this.showError(error);
    }
  }

  async retryDownload() {
    if (!this.download?.id) return;
    this.message = 'Queuing a retry. Verified cache files will be reused…';
    this.messageKind = 'pending';
    this.render();
    try {
      const payload = await requestJson(
        `/api/data/era5/downloads/${encodeURIComponent(this.download.id)}/retry`,
        { method: 'POST', body: '{}' },
      );
      this.download = payload.download;
      this.events = [];
      this.message = 'Retry queued with cache reuse.';
      this.messageKind = 'success';
      this.startPolling();
      await this.refreshCurrent();
    } catch (error) {
      this.showError(error);
    }
  }

  startPolling() {
    if (this.pollTimer) return;
    this.pollTimer = window.setInterval(() => {
      this.refreshCurrent().catch((error) => this.showError(error));
    }, 1000);
  }

  stopPolling() {
    if (this.pollTimer) {
      window.clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  showError(error) {
    this.message = error instanceof Error ? error.message : 'ERA5 download action failed.';
    this.messageKind = 'error';
    this.stopPolling();
    this.render();
  }

  bindEvents() {
    this.shadowRoot.getElementById('era5-download-start')?.addEventListener('click', () => this.startDownload());
    this.shadowRoot.getElementById('era5-download-refresh')?.addEventListener('click', () => this.refresh());
    this.shadowRoot.getElementById('era5-download-cancel')?.addEventListener('click', () => this.cancelDownload());
    this.shadowRoot.getElementById('era5-download-retry')?.addEventListener('click', () => this.retryDownload());
  }

  render() {
    const job = this.download;
    const progress = job?.progress || {};
    const total = Number(progress.total_requests || 0);
    const completed = Number(progress.completed_requests || 0);
    const percentage = total > 0 ? Math.round((completed / total) * 100) : 0;
    const previewAvailable = Boolean(this.readiness?.wizard_preview?.available);
    const hasActiveJob = Boolean(job && ACTIVE_STATUSES.has(job.status));
    const canStart = previewAvailable && !hasActiveJob;
    const recentEvents = this.events.slice(-5).reverse();

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; max-width: 1180px; margin: 1.25rem auto 3rem; padding: 0 1rem; color: #14213d; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
        section { background: linear-gradient(135deg, #f7fbff, #eef5ff); border: 1px solid #c9dcf2; border-radius: 18px; padding: 1.35rem; box-shadow: 0 12px 32px rgba(28, 57, 94, .08); }
        header { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; flex-wrap: wrap; }
        .eyebrow { margin: 0 0 .3rem; color: #315b85; font-weight: 750; letter-spacing: .04em; text-transform: uppercase; font-size: .76rem; }
        h2 { margin: 0; font-size: 1.45rem; }
        header p:last-child { max-width: 760px; margin: .45rem 0 0; color: #40566d; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: .75rem; margin: 1rem 0; }
        .card { background: white; border: 1px solid #d9e5f2; border-radius: 12px; padding: .8rem; min-width: 0; }
        .card span { display: block; color: #526a82; font-size: .78rem; }
        .card strong { display: block; margin-top: .2rem; overflow-wrap: anywhere; }
        progress { width: 100%; height: .75rem; accent-color: #2767a7; }
        .actions { display: flex; gap: .65rem; flex-wrap: wrap; }
        button { border: 0; border-radius: 9px; padding: .7rem 1rem; background: #1d5f9d; color: white; font: inherit; font-weight: 700; cursor: pointer; }
        button.secondary { background: white; color: #1d5f9d; border: 1px solid #9ebddd; }
        button.danger { background: #a63333; }
        button:disabled { cursor: not-allowed; opacity: .48; }
        .message { min-height: 1.4em; margin: .9rem 0 0; color: #40566d; }
        .message.error { color: #9b2424; font-weight: 650; }
        .message.success { color: #17663b; font-weight: 650; }
        .message.pending { color: #7a5200; }
        .events { margin: 1rem 0 0; padding: .8rem 1rem; background: rgba(255,255,255,.72); border-radius: 10px; }
        .events h3 { margin: 0 0 .45rem; font-size: 1rem; }
        .events ol { margin: 0; padding-left: 1.2rem; }
        .events li { margin: .25rem 0; color: #40566d; }
        code { font-size: .83em; overflow-wrap: anywhere; }
      </style>
      <section aria-labelledby="era5-download-title">
        <header>
          <div>
            <p class="eyebrow">Step 3 · Persistent data worker</p>
            <h2 id="era5-download-title">Download and verify real ERA5 files</h2>
            <p>The downloader runs outside the HTTP request, persists its state in the managed cache, verifies every file and reuses completed requests after cancellation, failure or restart.</p>
          </div>
          <button id="era5-download-refresh" class="secondary" type="button">Refresh</button>
        </header>
        <div class="grid">
          <div class="card"><span>Guided preview</span><strong>${previewAvailable ? 'Available' : 'Required'}</strong></div>
          <div class="card"><span>Credentials</span><strong>${this.readiness?.credentials?.configured ? 'Configured' : 'Not configured'}</strong></div>
          <div class="card"><span>Job status</span><strong id="era5-download-status">${escapeHtml(statusLabel(job?.status))}</strong></div>
          <div class="card"><span>Request progress</span><strong>${completed} / ${total || '—'}</strong><progress max="100" value="${percentage}">${percentage}%</progress></div>
          <div class="card"><span>Current request</span><strong>${escapeHtml(progress.current_request || '—')}</strong></div>
          <div class="card"><span>Persistent job ID</span><strong><code>${escapeHtml(job?.id || '—')}</code></strong></div>
        </div>
        <div class="actions">
          <button id="era5-download-start" type="button" ${canStart ? '' : 'disabled'}>Start real ERA5 download</button>
          <button id="era5-download-cancel" class="danger" type="button" ${job?.cancellable ? '' : 'disabled'}>Cancel safely</button>
          <button id="era5-download-retry" class="secondary" type="button" ${job?.retryable ? '' : 'disabled'}>Retry with cache reuse</button>
        </div>
        <p class="message ${escapeHtml(this.messageKind)}" aria-live="polite">${escapeHtml(this.message)}</p>
        ${recentEvents.length ? `<div class="events"><h3>Recent state events</h3><ol>${recentEvents.map((event) => `<li><strong>${escapeHtml(event.status)}</strong> — ${escapeHtml(event.message)}</li>`).join('')}</ol></div>` : ''}
      </section>
    `;
    this.bindEvents();
  }
}

if (!customElements.get('era5-download-control')) {
  customElements.define('era5-download-control', Era5DownloadControl);
}
