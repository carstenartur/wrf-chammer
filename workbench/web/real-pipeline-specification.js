async function pipelineRequestJson(path, init) {
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

const PIPELINE_PINNED_IDENTITY_RE = /^sha256:[0-9a-f]{64}$/;

function pipelineEscape(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function pipelineIsPinnedIdentity(value) {
  return PIPELINE_PINNED_IDENTITY_RE.test(String(value || ''));
}

function pipelineShortHash(value) {
  const text = String(value || '');
  if (pipelineIsPinnedIdentity(text)) return `${text.slice(0, 19)}…${text.slice(-8)}`;
  return 'not pinned';
}

function pipelineFormatPeriod(period) {
  if (!period?.start || !period?.end) return 'Period unavailable';
  return `${period.start} → ${period.end}`;
}

class RealPipelineSpecification extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.readiness = null;
    this.cacheEntries = [];
    this.specifications = [];
    this.selectedPlanKey = '';
    this.selectedProfile = 'small-real-data-demo';
    this.created = null;
    this.message = 'Checking whether a real WPS/WRF run can be frozen reproducibly…';
    this.messageKind = 'pending';
  }

  connectedCallback() {
    this.render();
    this.refresh().catch((error) => this.showError(error));
  }

  get eligibleCacheEntries() {
    return this.cacheEntries.filter((entry) => (
      entry.status === 'complete'
      && entry.coverage?.percent === 100
      && entry.provenance?.checksums_available
      && entry.provenance?.provenance_file_available
      && entry.provenance?.artificial_weather_data === false
    ));
  }

  get runtimeIdentitiesPinned() {
    const runtimes = this.readiness?.runtime || {};
    return ['wps', 'wrf', 'postprocessing'].every((name) => (
      pipelineIsPinnedIdentity(runtimes[name]?.identity)
    ));
  }

  async refresh() {
    this.message = 'Refreshing runtime identities, verified ERA5 inputs and immutable specifications…';
    this.messageKind = 'pending';
    this.render();
    try {
      const [readiness, cache, specifications] = await Promise.all([
        pipelineRequestJson('/api/pipeline/specifications/readiness'),
        pipelineRequestJson('/api/data/era5/cache'),
        pipelineRequestJson('/api/pipeline/specifications'),
      ]);
      this.readiness = readiness;
      this.cacheEntries = cache.entries || [];
      this.specifications = specifications.specifications || [];
      const eligible = this.eligibleCacheEntries;
      if (!eligible.some((entry) => entry.plan_key === this.selectedPlanKey)) {
        this.selectedPlanKey = eligible[0]?.plan_key || '';
      }
      const profileIds = Object.keys(readiness.profiles || {});
      if (!profileIds.includes(this.selectedProfile)) {
        this.selectedProfile = profileIds[0] || '';
      }
      if (!readiness.ready || !this.runtimeIdentitiesPinned) {
        this.message = 'Pin all WPS, WRF and postprocessing runtime identities before freezing a real run.';
        this.messageKind = 'warning';
      } else if (!readiness.wizard_preview_available) {
        this.message = 'Create a valid guided simulation preview before freezing a real run.';
        this.messageKind = 'warning';
      } else if (!eligible.length) {
        this.message = 'A complete, checksummed and provenance-verified ERA5 cache entry is required.';
        this.messageKind = 'warning';
      } else {
        this.message = `${eligible.length} verified ERA5 input set(s) can be frozen into an immutable run specification.`;
        this.messageKind = 'success';
      }
      this.render();
    } catch (error) {
      this.showError(error);
    }
  }

  async freezeSpecification() {
    if (!this.selectedPlanKey || !this.selectedProfile || !this.runtimeIdentitiesPinned) return;
    this.message = 'Freezing job, namelists, input checksums and runtime identities…';
    this.messageKind = 'pending';
    this.render();
    try {
      const payload = await pipelineRequestJson('/api/pipeline/specifications', {
        method: 'POST',
        body: JSON.stringify({
          plan_key: this.selectedPlanKey,
          profile: this.selectedProfile,
        }),
      });
      this.created = payload.specification;
      this.message = 'Immutable specification created or reused. No WPS or WRF process has been started.';
      this.messageKind = 'success';
      await this.refreshSpecifications();
      this.render();
    } catch (error) {
      this.showError(error);
    }
  }

  async refreshSpecifications() {
    const payload = await pipelineRequestJson('/api/pipeline/specifications');
    this.specifications = payload.specifications || [];
  }

  showError(error) {
    this.message = error instanceof Error ? error.message : 'Real pipeline specification failed.';
    this.messageKind = 'error';
    this.render();
  }

  bindEvents() {
    this.shadowRoot.getElementById('pipeline-spec-refresh')?.addEventListener('click', () => this.refresh());
    this.shadowRoot.getElementById('pipeline-spec-freeze')?.addEventListener('click', () => this.freezeSpecification());
    this.shadowRoot.getElementById('pipeline-spec-plan')?.addEventListener('change', (event) => {
      this.selectedPlanKey = event.target.value;
      this.created = null;
      this.render();
    });
    this.shadowRoot.getElementById('pipeline-spec-profile')?.addEventListener('change', (event) => {
      this.selectedProfile = event.target.value;
      this.created = null;
      this.render();
    });
  }

  renderRuntime(name, runtime) {
    const pinned = pipelineIsPinnedIdentity(runtime?.identity);
    return `
      <div class="runtime-card ${pinned ? 'ready' : 'warning'}">
        <span>${pipelineEscape(name)}</span>
        <strong>${pipelineEscape(runtime?.reference || 'not configured')}</strong>
        <code title="${pipelineEscape(runtime?.identity || '')}">${pipelineEscape(pipelineShortHash(runtime?.identity))}</code>
      </div>
    `;
  }

  renderCreatedSpecification() {
    const specification = this.created;
    if (!specification) return '';
    const identity = specification.identity || {};
    const steps = identity.steps || [];
    return `
      <article class="created" id="pipeline-spec-created">
        <div class="created-heading">
          <div>
            <span class="immutable">Immutable</span>
            <h3><code>${pipelineEscape(specification.specification_key)}</code></h3>
          </div>
          <strong>Execution started: ${specification.execution_started ? 'yes' : 'no'}</strong>
        </div>
        <dl>
          <dt>Job</dt><dd>${pipelineEscape(identity.job?.name || identity.job?.id)}</dd>
          <dt>Period</dt><dd>${pipelineEscape(pipelineFormatPeriod(identity.job?.period))}</dd>
          <dt>Profile</dt><dd>${pipelineEscape(identity.profile?.id)}</dd>
          <dt>ERA5 plan</dt><dd><code>${pipelineEscape(identity.era5_input?.plan_key)}</code></dd>
          <dt>Source revision</dt><dd><code>${pipelineEscape(identity.source?.repository_revision)}</code></dd>
          <dt>Namelists</dt><dd>${pipelineEscape(Object.keys(identity.namelists || {}).join(', '))}</dd>
        </dl>
        <h4>Frozen pipeline contracts</h4>
        <ol class="steps">
          ${steps.map((step) => `<li><strong>${pipelineEscape(step.label)}</strong><span>${pipelineEscape(step.id)} · ${pipelineEscape(step.status)}</span></li>`).join('')}
        </ol>
        <p class="no-run">This operation freezes configuration only. The later persistent worker must reference this key before it may start any container.</p>
      </article>
    `;
  }

  render() {
    const profiles = Object.values(this.readiness?.profiles || {});
    const eligible = this.eligibleCacheEntries;
    const runtimes = this.readiness?.runtime || {};
    const canFreeze = Boolean(
      this.readiness?.ready
      && this.runtimeIdentitiesPinned
      && this.readiness?.wizard_preview_available
      && this.selectedPlanKey
      && this.selectedProfile
    );
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; max-width: 1180px; margin: 1.25rem auto 3rem; padding: 0 1rem; color: #14213d; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
        section { border: 1px solid #cbd9e7; border-radius: 18px; padding: 1.35rem; background: linear-gradient(135deg,#f8fbff,#eef5fb); box-shadow: 0 12px 32px rgba(28,57,94,.08); }
        header { display: flex; justify-content: space-between; gap: 1rem; align-items: flex-start; flex-wrap: wrap; }
        .eyebrow { margin: 0 0 .3rem; color: #315b85; font-size: .76rem; font-weight: 750; letter-spacing: .04em; text-transform: uppercase; }
        h2 { margin: 0; font-size: 1.45rem; }
        header p:last-child { max-width: 760px; margin: .45rem 0 0; color: #40566d; }
        button { border: 0; border-radius: 9px; padding: .7rem 1rem; background: #1d5f9d; color: white; font: inherit; font-weight: 700; cursor: pointer; }
        button.secondary { background: white; color: #1d5f9d; border: 1px solid #9ebddd; }
        button:disabled { cursor: not-allowed; opacity: .48; }
        .runtime-grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(205px,1fr)); gap: .7rem; margin: 1rem 0; }
        .runtime-card { background: white; border: 1px solid #d9e4ef; border-radius: 10px; padding: .75rem; min-width: 0; }
        .runtime-card.warning { border-color: #d9b36d; }
        .runtime-card span, .runtime-card code { display: block; color: #566d83; overflow-wrap: anywhere; }
        .runtime-card strong { display: block; margin: .2rem 0; overflow-wrap: anywhere; }
        .source { margin: .3rem 0 1rem; color: #40566d; overflow-wrap: anywhere; }
        .controls { display: grid; grid-template-columns: minmax(240px,2fr) minmax(190px,1fr) max-content; gap: .75rem; align-items: end; }
        label { display: grid; gap: .3rem; color: #40566d; font-weight: 650; }
        select { min-width: 0; border: 1px solid #aebfd0; border-radius: 8px; padding: .65rem; background: white; font: inherit; }
        .message { min-height: 1.3em; color: #40566d; }
        .message.success { color: #17663b; font-weight: 650; }
        .message.warning { color: #845700; font-weight: 650; }
        .message.error { color: #982424; font-weight: 650; }
        .history { margin: .8rem 0 0; color: #566d83; }
        .created { margin-top: 1rem; border: 1px solid #9bbbd7; border-radius: 13px; padding: 1rem; background: white; }
        .created-heading { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
        .immutable { border-radius: 999px; background: #e1f3e8; color: #17663b; padding: .2rem .55rem; font-size: .75rem; font-weight: 750; text-transform: uppercase; }
        h3 { margin: .45rem 0 .8rem; font-size: .95rem; overflow-wrap: anywhere; }
        h4 { margin-bottom: .5rem; }
        dl { display: grid; grid-template-columns: max-content 1fr; gap: .3rem .75rem; }
        dt { color: #566d83; font-weight: 650; }
        dd { margin: 0; overflow-wrap: anywhere; }
        .steps { display: grid; grid-template-columns: repeat(auto-fit,minmax(190px,1fr)); gap: .55rem; padding: 0; list-style: none; }
        .steps li { border: 1px solid #dce5ee; border-radius: 8px; padding: .6rem; }
        .steps strong, .steps span { display: block; }
        .steps span { margin-top: .2rem; color: #566d83; font-size: .82rem; }
        .no-run { margin: .8rem 0 0; padding: .65rem; border-radius: 8px; background: #fff7df; color: #674900; font-weight: 650; }
        @media (max-width: 760px) { .controls { grid-template-columns: 1fr; } }
      </style>
      <section aria-labelledby="pipeline-spec-title">
        <header>
          <div>
            <p class="eyebrow">Step 4 · Reproducible execution boundary</p>
            <h2 id="pipeline-spec-title">Freeze a real WPS/WRF run specification</h2>
            <p>Bind the validated domain, verified ERA5 files, deterministic namelists, source revision and pinned runtime images into one content-addressed record before any worker may execute them.</p>
          </div>
          <button id="pipeline-spec-refresh" class="secondary" type="button">Refresh readiness</button>
        </header>
        <div class="runtime-grid">
          ${this.renderRuntime('WPS runtime', runtimes.wps)}
          ${this.renderRuntime('WRF runtime', runtimes.wrf)}
          ${this.renderRuntime('Postprocessing runtime', runtimes.postprocessing)}
        </div>
        <p class="source">Source revision: <code>${pipelineEscape(this.readiness?.source_revision || 'not available')}</code></p>
        <div class="controls">
          <label>Verified ERA5 input
            <select id="pipeline-spec-plan" ${eligible.length ? '' : 'disabled'}>
              ${eligible.length ? eligible.map((entry) => `<option value="${pipelineEscape(entry.plan_key)}" ${entry.plan_key === this.selectedPlanKey ? 'selected' : ''}>${pipelineEscape(entry.plan_key.slice(0, 16))}… · ${pipelineEscape(pipelineFormatPeriod(entry.period))}</option>`).join('') : '<option value="">No eligible plan</option>'}
            </select>
          </label>
          <label>Pipeline profile
            <select id="pipeline-spec-profile" ${profiles.length ? '' : 'disabled'}>
              ${profiles.map((profile) => `<option value="${pipelineEscape(profile.id)}" ${profile.id === this.selectedProfile ? 'selected' : ''}>${pipelineEscape(profile.label)} · max ${Number(profile.max_grid_points || 0).toLocaleString()} points</option>`).join('')}
            </select>
          </label>
          <button id="pipeline-spec-freeze" type="button" ${canFreeze ? '' : 'disabled'}>Freeze immutable specification</button>
        </div>
        <p class="message ${pipelineEscape(this.messageKind)}" aria-live="polite">${pipelineEscape(this.message)}</p>
        <p class="history">Stored immutable specifications: <strong>${this.specifications.length}</strong>. They are reusable and are not execution jobs.</p>
        ${this.renderCreatedSpecification()}
      </section>
    `;
    this.bindEvents();
  }
}

if (!customElements.get('real-pipeline-specification')) {
  customElements.define('real-pipeline-specification', RealPipelineSpecification);
}
