(function () {
  'use strict';

  const DEFAULTS = {
    west: 2,
    south: 51,
    east: 14,
    north: 58,
    start: '2013-12-05T12:00',
    end: '2013-12-06T06:00',
  };

  function element(tag, attributes, children) {
    const node = document.createElement(tag);
    Object.entries(attributes || {}).forEach(([name, value]) => {
      if (name === 'className') node.className = value;
      else if (name === 'text') node.textContent = value;
      else if (name === 'htmlFor') node.htmlFor = value;
      else if (value !== undefined && value !== null) node.setAttribute(name, String(value));
    });
    (children || []).forEach((child) => node.append(child));
    return node;
  }

  async function requestJson(path, init) {
    const response = await fetch(path, {
      headers: init && init.body ? { 'Content-Type': 'application/json' } : undefined,
      ...init,
    });
    const payload = await response.json();
    if (!response.ok) {
      const message = payload.errors?.join('; ') || payload.error?.message || `HTTP ${response.status}`;
      throw new Error(message);
    }
    return payload;
  }

  function isoUtc(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) throw new Error(`Invalid date: ${value}`);
    return date.toISOString().replace('.000Z', 'Z');
  }

  function numberValue(id) {
    const value = Number(document.getElementById(id).value);
    if (!Number.isFinite(value)) throw new Error(`${id} must be a number`);
    return value;
  }

  function currentBounds() {
    return {
      west: numberValue('wizard-west'),
      south: numberValue('wizard-south'),
      east: numberValue('wizard-east'),
      north: numberValue('wizard-north'),
    };
  }

  function updateMap() {
    const frame = document.getElementById('wizard-map');
    const link = document.getElementById('wizard-map-link');
    try {
      const bounds = currentBounds();
      const centerLat = (bounds.south + bounds.north) / 2;
      const centerLon = (bounds.west + bounds.east) / 2;
      const bbox = [bounds.west, bounds.south, bounds.east, bounds.north].join(',');
      frame.src = `https://www.openstreetmap.org/export/embed.html?bbox=${encodeURIComponent(bbox)}&layer=mapnik&marker=${encodeURIComponent(`${centerLat},${centerLon}`)}`;
      link.href = `https://www.openstreetmap.org/?mlat=${encodeURIComponent(centerLat)}&mlon=${encodeURIComponent(centerLon)}#map=5/${encodeURIComponent(centerLat)}/${encodeURIComponent(centerLon)}`;
    } catch (_error) {
      // Keep the previous map while an input is temporarily incomplete.
    }
  }

  function planningRequest() {
    const expertEnabled = document.getElementById('wizard-expert').checked;
    const request = {
      event: document.getElementById('wizard-event').value.trim() || 'xaver',
      mode: 'dry-run',
      job_id: `${(document.getElementById('wizard-event').value.trim() || 'xaver').toLowerCase()}-map-dry-run`,
      planning: {
        label: 'map-selected-domain',
        bounds: currentBounds(),
        period: {
          start: isoUtc(document.getElementById('wizard-start').value),
          end: isoUtc(document.getElementById('wizard-end').value),
        },
        quality_profile: document.getElementById('wizard-profile').value,
      },
    };
    if (expertEnabled) {
      request.planning.expert = {
        grid_spacing_km: numberValue('wizard-grid-spacing'),
        vertical_levels: numberValue('wizard-vertical-levels'),
        output_interval_minutes: numberValue('wizard-output-interval'),
      };
    }
    return request;
  }

  function metric(label, value, detail) {
    return element('div', { className: 'wizard-metric' }, [
      element('span', { className: 'wizard-metric-label', text: label }),
      element('strong', { text: value }),
      detail ? element('small', { text: detail }) : document.createTextNode(''),
    ]);
  }

  function renderPlan(payload) {
    const result = document.getElementById('wizard-result');
    result.replaceChildren();
    const plan = payload.plan;
    const domain = plan.domain;
    const resources = plan.resources;
    const metrics = element('div', { className: 'wizard-metrics' }, [
      metric('Grid', `${domain.e_we} × ${domain.e_sn}`, `${domain.dx_km} km spacing`),
      metric('Extent', `${domain.width_km} × ${domain.height_km} km`, plan.quality_profile.label),
      metric('Recommended RAM', `${resources.estimated_ram_gb.recommended} GB`, `minimum ${resources.estimated_ram_gb.minimum} GB`),
      metric('Working storage', `${resources.estimated_storage_gb.working_total} GB`, `ERA5 ${resources.estimated_storage_gb.era5_input} GB`),
      metric('Estimated runtime', `${resources.estimated_wall_clock_minutes.lower}–${resources.estimated_wall_clock_minutes.upper} min`, resources.estimated_wall_clock_minutes.runtime_class),
      metric('Simulation', `${plan.period.simulation_hours} h`, `${resources.output_frames} output frames`),
    ]);
    result.append(metrics);

    if (payload.warnings && payload.warnings.length) {
      result.append(element('div', { className: 'wizard-message warning', text: payload.warnings.join(' ') }));
    }

    const details = element('details', { className: 'wizard-details' }, [
      element('summary', { text: 'Show generated job configuration and assumptions' }),
      element('p', { text: plan.assumptions.join(' ') }),
      element('pre', { id: 'wizard-config-preview', text: JSON.stringify(payload.config, null, 2) }),
    ]);
    result.append(details);

    const runButton = element('button', { type: 'button', id: 'wizard-run', text: 'Start planned dry-run' });
    runButton.addEventListener('click', async () => {
      const status = document.getElementById('wizard-status');
      status.className = 'wizard-message pending';
      status.textContent = 'Starting the planned dry-run…';
      runButton.disabled = true;
      try {
        const started = await requestJson('/api/jobs', {
          method: 'POST',
          body: JSON.stringify({ config: payload.config, start: true }),
        });
        status.className = `wizard-message ${started.ok ? 'success' : 'error'}`;
        status.textContent = started.ok
          ? `Dry-run ${started.job.job_id} finished successfully.`
          : `Dry-run ${started.job.job_id} failed.`;
      } catch (error) {
        status.className = 'wizard-message error';
        status.textContent = error.message;
      } finally {
        runButton.disabled = false;
      }
    });
    result.append(element('div', { className: 'wizard-actions' }, [runButton]));
    window.__WRF_DOMAIN_PLAN__ = payload;
  }

  async function plan() {
    const status = document.getElementById('wizard-status');
    const button = document.getElementById('wizard-plan');
    status.className = 'wizard-message pending';
    status.textContent = 'Calculating grid and resource estimates…';
    button.disabled = true;
    try {
      const payload = await requestJson('/api/wizard/preview', {
        method: 'POST',
        body: JSON.stringify(planningRequest()),
      });
      renderPlan(payload);
      status.className = 'wizard-message success';
      status.textContent = 'The map domain is valid and a job configuration was generated.';
      updateMap();
    } catch (error) {
      document.getElementById('wizard-result').replaceChildren();
      status.className = 'wizard-message error';
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  function inputField(id, label, type, value, attributes) {
    const input = element('input', { id, type, value, ...(attributes || {}) });
    return element('label', { className: 'wizard-field', htmlFor: id }, [
      element('span', { text: label }),
      input,
    ]);
  }

  function buildWizard() {
    if (document.getElementById('domain-wizard')) return;
    const root = document.getElementById('root');
    const section = element('section', { id: 'domain-wizard', className: 'wizard-shell', 'aria-labelledby': 'wizard-title' });
    section.append(
      element('div', { className: 'wizard-heading' }, [
        element('div', {}, [
          element('p', { className: 'eyebrow', text: 'Guided simulation planning' }),
          element('h2', { id: 'wizard-title', text: 'Choose a real map area and estimate the WRF job' }),
          element('p', { text: 'The browser sends geographic bounds to the server. Grid dimensions and resource estimates are derived centrally and remain visible before a run starts.' }),
        ]),
      ]),
    );

    const form = element('div', { className: 'wizard-form' }, [
      inputField('wizard-event', 'Event or template', 'text', 'xaver'),
      element('label', { className: 'wizard-field', htmlFor: 'wizard-profile' }, [
        element('span', { text: 'Quality profile' }),
        element('select', { id: 'wizard-profile' }, [
          element('option', { value: 'quick-preview', text: 'Quick preview · 27 km' }),
          element('option', { value: 'balanced', text: 'Balanced regional · 9 km', selected: 'selected' }),
          element('option', { value: 'detailed', text: 'Detailed regional · 3 km' }),
        ]),
      ]),
      inputField('wizard-start', 'Start (UTC)', 'datetime-local', DEFAULTS.start),
      inputField('wizard-end', 'End (UTC)', 'datetime-local', DEFAULTS.end),
    ]);

    const boundsGrid = element('fieldset', { className: 'wizard-bounds' }, [
      element('legend', { text: 'Map bounds' }),
      inputField('wizard-west', 'West longitude', 'number', DEFAULTS.west, { step: '0.1', min: '-180', max: '180' }),
      inputField('wizard-south', 'South latitude', 'number', DEFAULTS.south, { step: '0.1', min: '-90', max: '90' }),
      inputField('wizard-east', 'East longitude', 'number', DEFAULTS.east, { step: '0.1', min: '-180', max: '180' }),
      inputField('wizard-north', 'North latitude', 'number', DEFAULTS.north, { step: '0.1', min: '-90', max: '90' }),
    ]);

    const map = element('div', { className: 'wizard-map-wrap' }, [
      element('iframe', {
        id: 'wizard-map',
        title: 'OpenStreetMap preview of the selected simulation domain',
        loading: 'lazy',
        referrerpolicy: 'no-referrer',
      }),
      element('p', { className: 'wizard-attribution' }, [
        document.createTextNode('Basemap © OpenStreetMap contributors · '),
        element('a', { id: 'wizard-map-link', href: 'https://www.openstreetmap.org', target: '_blank', rel: 'noreferrer', text: 'Open full map' }),
      ]),
    ]);

    const expertToggle = element('input', { id: 'wizard-expert', type: 'checkbox' });
    const expertFields = element('div', { id: 'wizard-expert-fields', className: 'wizard-expert-fields', hidden: 'hidden' }, [
      inputField('wizard-grid-spacing', 'Grid spacing (km)', 'number', 9, { step: '1', min: '1', max: '100' }),
      inputField('wizard-vertical-levels', 'Vertical levels', 'number', 35, { step: '1', min: '20', max: '100' }),
      inputField('wizard-output-interval', 'Output interval (minutes)', 'number', 60, { step: '10', min: '10', max: '360' }),
    ]);
    expertToggle.addEventListener('change', () => {
      expertFields.hidden = !expertToggle.checked;
    });

    const planButton = element('button', { type: 'button', id: 'wizard-plan', text: 'Plan domain and preview job' });
    planButton.addEventListener('click', plan);

    section.append(
      element('div', { className: 'wizard-grid' }, [
        element('div', {}, [form, boundsGrid, element('label', { className: 'wizard-expert-toggle' }, [expertToggle, document.createTextNode(' Show expert grid controls')]), expertFields]),
        map,
      ]),
      element('div', { className: 'wizard-actions' }, [planButton]),
      element('div', { id: 'wizard-status', className: 'wizard-message', 'aria-live': 'polite', text: 'Adjust the map bounds or use the Xaver defaults, then calculate the plan.' }),
      element('div', { id: 'wizard-result' }),
    );

    root.insertAdjacentElement('afterend', section);
    ['wizard-west', 'wizard-south', 'wizard-east', 'wizard-north'].forEach((id) => {
      document.getElementById(id).addEventListener('change', updateMap);
    });
    updateMap();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', buildWizard);
  } else {
    buildWizard();
  }
})();
