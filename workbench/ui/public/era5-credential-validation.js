const CDS_ACTIVE = new Set(['QUEUED', 'RUNNING']);

async function cdsRequestJson(path, init) {
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

function cdsEscape(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function cdsFormatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return 'not retained';
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KiB`;
}

class Era5CredentialValidation extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.status = null;
    this.message = 'Reading local CDS credential status…';
    this.messageKind = 'pending';
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
    try {
      this.status = await cdsRequestJson('/api/data/era5/credentials/validation');
      const validation = this.status.validation;
      if (validation?.running) {
        this.message = 'A minimal real ERA5 request is validating the configured credentials…';
        this.messageKind = 'pending';
        this.startPolling();
      } else {
        this.stopPolling();
        this.message = validation?.summary || this.status.configuration_summary;
        this.messageKind = validation?.status === 'VALID' ? 'success' : validation ? 'warning' : '';
      }
      this.render();
    } catch (error) {
      this.showError(error);
    }
  }

  async startValidation() {
    this.message = 'Starting an explicit minimal real ERA5 validation request…';
    this.messageKind = 'pending';
    this.render();
    try {
      const payload = await cdsRequestJson('/api/data/era5/credentials/validate', {
        method: 'POST',
        body: '{}',
      });
      this.status = {
        ...(this.status || {}),
        validation: payload.validation,
      };
      this.startPolling();
      this.render();
    } catch (error) {
      this.showError(error);
    }
  }

  startPolling() {
    if (this.pollTimer) return;
    this.pollTimer = window.setInterval(() => {
      this.refresh().catch((error) => this.showError(error));
    }, 1000);
  }

  stopPolling() {
    if (this.pollTimer) {
      window.clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  showError(error) {
    this.message = error instanceof Error ? error.message : 'CDS credential validation failed.';
    this.messageKind = 'error';
    this.stopPolling();
    this.render();
  }

  bindEvents() {
    this.shadowRoot.getElementById('cds-validation-start')?.addEventListener('click', () => this.startValidation());
    this.shadowRoot.getElementById('cds-validation-refresh')?.addEventListener('click', () => this.refresh());
  }

  render() {
    const validation = this.status?.validation;
    const result = validation?.result;
    const canStart = Boolean(this.status?.configured) && !CDS_ACTIVE.has(validation?.status);
    const statusText = validation?.status || (this.status?.configured ? 'NOT TESTED' : 'NOT CONFIGURED');
    const request = result?.request;
    const response = result?.response;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; max-width: 1180px; margin: 1.25rem auto 3rem; padding: 0 1rem; color: #14213d; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
        section { border: 1px solid #d6e0ea; border-radius: 18px; padding: 1.35rem; background: linear-gradient(135deg,#fff,#f5f9fc); box-shadow: 0 12px 30px rgba(28,57,94,.06); }
        header { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; align-items: flex-start; }
        .eyebrow { margin: 0 0 .3rem; color: #536d87; font-size: .76rem; font-weight: 750; letter-spacing: .04em; text-transform: uppercase; }
        h2 { margin: 0; font-size: 1.45rem; }
        header p:last-child { max-width: 760px; margin: .45rem 0 0; color: #40566d; }
        .actions { display: flex; gap: .6rem; flex-wrap: wrap; }
        button { border: 0; border-radius: 9px; padding: .7rem 1rem; background: #1d5f9d; color: white; font: inherit; font-weight: 700; cursor: pointer; }
        button.secondary { background: white; color: #1d5f9d; border: 1px solid #9ebddd; }
        button:disabled { cursor: not-allowed; opacity: .48; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: .7rem; margin: 1rem 0; }
        .card { border: 1px solid #dce6ef; border-radius: 10px; background: white; padding: .75rem; }
        .card span, .card small { display: block; color: #566d83; }
        .card strong { display: block; margin: .2rem 0; overflow-wrap: anywhere; }
        .message { min-height: 1.3em; color: #40566d; }
        .message.success { color: #17663b; font-weight: 650; }
        .message.warning { color: #8a5b00; font-weight: 650; }
        .message.error { color: #982424; font-weight: 650; }
        .caveat { margin: .8rem 0 0; color: #566d83; font-size: .9rem; }
      </style>
      <section aria-labelledby="cds-validation-title">
        <header>
          <div>
            <p class="eyebrow">Credential verification</p>
            <h2 id="cds-validation-title">Test Copernicus CDS access</h2>
            <p>This opt-in check requests one tiny real ERA5 field in an isolated temporary directory, verifies it, and deletes it immediately. No credential value or raw provider error is returned to the browser.</p>
          </div>
          <div class="actions">
            <button id="cds-validation-refresh" class="secondary" type="button">Refresh status</button>
            <button id="cds-validation-start" type="button" ${canStart ? '' : 'disabled'}>Run real credential test</button>
          </div>
        </header>
        <div class="grid">
          <div class="card"><span>Configuration</span><strong>${this.status?.configured ? 'Present' : 'Missing'}</strong><small>${cdsEscape(this.status?.configuration_summary || 'checking')}</small></div>
          <div class="card"><span>Validation</span><strong>${cdsEscape(statusText)}</strong><small>${cdsEscape(validation?.code || 'No test request yet')}</small></div>
          <div class="card"><span>Last checked</span><strong>${cdsEscape(result?.checked_at || validation?.finished_at || '—')}</strong><small>${result?.duration_seconds == null ? 'duration unavailable' : `${result.duration_seconds} seconds`}</small></div>
          <div class="card"><span>Test response</span><strong>${cdsFormatBytes(response?.size_bytes)}</strong><small>retained: no</small></div>
          <div class="card"><span>Dataset</span><strong>${cdsEscape(request?.dataset || 'ERA5 single levels')}</strong><small>${cdsEscape(request?.variable || '2 m temperature')}</small></div>
          <div class="card"><span>Artificial data</span><strong>no</strong><small>The test uses a real ERA5 response.</small></div>
        </div>
        <p class="message ${cdsEscape(this.messageKind)}" aria-live="polite">${cdsEscape(this.message)}</p>
        <p class="caveat">The test may enter the normal CDS queue and is never started automatically. A service outage is reported separately from invalid credentials.</p>
      </section>
    `;
    this.bindEvents();
  }
}

if (!customElements.get('era5-credential-validation')) {
  customElements.define('era5-credential-validation', Era5CredentialValidation);
}
