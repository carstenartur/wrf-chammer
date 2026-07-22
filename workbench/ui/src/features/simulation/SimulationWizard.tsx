import React, { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import './simulation-wizard.css';

type DomainBounds = {
  west: number;
  south: number;
  east: number;
  north: number;
};

type DomainPlan = {
  quality_profile: {
    id: string;
    label: string;
    description: string;
  };
  period: {
    start: string;
    end: string;
    simulation_hours: number;
    output_interval_minutes: number;
  };
  domain: {
    bounds: DomainBounds;
    center_lat: number;
    center_lon: number;
    width_km: number;
    height_km: number;
    dx_km: number;
    dy_km: number;
    e_we: number;
    e_sn: number;
    vertical_levels: number;
    time_step_seconds: number;
    projection_recommendation: string;
  };
  resources: {
    output_frames: number;
    estimated_ram_gb: {
      minimum: number;
      recommended: number;
    };
    estimated_storage_gb: {
      era5_input: number;
      wrf_output: number;
      working_total: number;
    };
    estimated_wall_clock_minutes: {
      lower: number;
      upper: number;
      runtime_class: string;
      reference: string;
    };
  };
  assumptions: string[];
};

type WizardPreview = {
  ok: boolean;
  valid: boolean;
  errors: string[];
  warnings: string[];
  plan: DomainPlan;
  config: Record<string, unknown>;
};

type JobStartResponse = {
  ok: boolean;
  job: {
    job_id: string;
  };
};

const DEFAULT_BOUNDS: DomainBounds = {
  west: 2,
  south: 51,
  east: 14,
  north: 58,
};

const MIN_DRAW_SPAN_DEGREES = 0.05;
const OPENSTREETMAP_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const OPENSTREETMAP_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>';

function roundedCoordinate(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

function toLeafletBounds(bounds: DomainBounds): L.LatLngBounds {
  return L.latLngBounds(
    [bounds.south, bounds.west],
    [bounds.north, bounds.east],
  );
}

function toUtcIso(value: string): string {
  const withSeconds = value.length === 16 ? `${value}:00` : value;
  const iso = `${withSeconds}Z`;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error(`Invalid UTC date and time: ${value}`);
  }
  return parsed.toISOString().replace('.000Z', 'Z');
}

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

function DomainMap(props: {
  bounds: DomainBounds;
  onBoundsChange: (bounds: DomainBounds) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const domainRectangleRef = useRef<L.Rectangle | null>(null);
  const drawingRectangleRef = useRef<L.Rectangle | null>(null);
  const startPointRef = useRef<L.LatLng | null>(null);
  const activePointerIdRef = useRef<number | null>(null);
  const drawModeRef = useRef(false);
  const [drawMode, setDrawMode] = useState(false);

  function stopDrawing() {
    const map = mapRef.current;
    const container = map?.getContainer();
    const pointerId = activePointerIdRef.current;
    if (container && pointerId !== null && container.hasPointerCapture(pointerId)) {
      container.releasePointerCapture(pointerId);
    }
    activePointerIdRef.current = null;
    drawModeRef.current = false;
    startPointRef.current = null;
    setDrawMode(false);
    if (map) {
      map.dragging.enable();
      map.getContainer().style.cursor = '';
      map.getContainer().style.touchAction = '';
    }
    if (drawingRectangleRef.current && map) {
      map.removeLayer(drawingRectangleRef.current);
      drawingRectangleRef.current = null;
    }
  }

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return undefined;

    const map = L.map(containerRef.current, {
      zoomControl: true,
      attributionControl: true,
      boxZoom: false,
    });
    L.tileLayer(OPENSTREETMAP_TILE_URL, {
      maxZoom: 18,
      attribution: OPENSTREETMAP_ATTRIBUTION,
      crossOrigin: true,
    }).addTo(map);

    const domainRectangle = L.rectangle(toLeafletBounds(props.bounds), {
      weight: 3,
      fillOpacity: 0.12,
    }).addTo(map);
    domainRectangleRef.current = domainRectangle;
    map.fitBounds(domainRectangle.getBounds(), { padding: [18, 18] });
    mapRef.current = map;

    const container = map.getContainer();
    const pointerLatLng = (event: PointerEvent): L.LatLng => {
      const rectangle = container.getBoundingClientRect();
      return map.containerPointToLatLng(
        L.point(event.clientX - rectangle.left, event.clientY - rectangle.top),
      );
    };

    const handlePointerDown = (event: PointerEvent) => {
      if (!drawModeRef.current || event.button !== 0) return;
      const target = event.target as Element | null;
      if (target?.closest('.leaflet-control')) return;
      event.preventDefault();
      event.stopPropagation();
      activePointerIdRef.current = event.pointerId;
      container.setPointerCapture(event.pointerId);
      const start = pointerLatLng(event);
      startPointRef.current = start;
      if (drawingRectangleRef.current) {
        map.removeLayer(drawingRectangleRef.current);
      }
      drawingRectangleRef.current = L.rectangle(
        L.latLngBounds(start, start),
        { weight: 2, dashArray: '7 5', fillOpacity: 0.08 },
      ).addTo(map);
    };

    const handlePointerMove = (event: PointerEvent) => {
      if (
        !drawModeRef.current
        || activePointerIdRef.current !== event.pointerId
        || !startPointRef.current
        || !drawingRectangleRef.current
      ) return;
      event.preventDefault();
      drawingRectangleRef.current.setBounds(
        L.latLngBounds(startPointRef.current, pointerLatLng(event)),
      );
    };

    const finishPointerDrawing = (event: PointerEvent) => {
      const start = startPointRef.current;
      if (!drawModeRef.current || activePointerIdRef.current !== event.pointerId || !start) return;
      event.preventDefault();
      event.stopPropagation();
      const end = pointerLatLng(event);
      const west = Math.min(start.lng, end.lng);
      const east = Math.max(start.lng, end.lng);
      const south = Math.min(start.lat, end.lat);
      const north = Math.max(start.lat, end.lat);
      if (east - west >= MIN_DRAW_SPAN_DEGREES && north - south >= MIN_DRAW_SPAN_DEGREES) {
        props.onBoundsChange({
          west: roundedCoordinate(west),
          south: roundedCoordinate(south),
          east: roundedCoordinate(east),
          north: roundedCoordinate(north),
        });
      }
      stopDrawing();
    };

    const handlePointerCancel = (event: PointerEvent) => {
      if (activePointerIdRef.current === event.pointerId) stopDrawing();
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && drawModeRef.current) stopDrawing();
    };

    container.addEventListener('pointerdown', handlePointerDown, true);
    container.addEventListener('pointermove', handlePointerMove, true);
    container.addEventListener('pointerup', finishPointerDrawing, true);
    container.addEventListener('pointercancel', handlePointerCancel, true);
    document.addEventListener('keydown', handleKeyDown);

    const resizeTimer = window.setTimeout(() => map.invalidateSize(), 0);
    return () => {
      window.clearTimeout(resizeTimer);
      container.removeEventListener('pointerdown', handlePointerDown, true);
      container.removeEventListener('pointermove', handlePointerMove, true);
      container.removeEventListener('pointerup', finishPointerDrawing, true);
      container.removeEventListener('pointercancel', handlePointerCancel, true);
      document.removeEventListener('keydown', handleKeyDown);
      map.remove();
      mapRef.current = null;
      domainRectangleRef.current = null;
      drawingRectangleRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const rectangle = domainRectangleRef.current;
    if (!map || !rectangle) return;
    const leafletBounds = toLeafletBounds(props.bounds);
    rectangle.setBounds(leafletBounds);
    map.fitBounds(leafletBounds, { padding: [18, 18], maxZoom: 8 });
  }, [props.bounds]);

  function beginDrawing() {
    const map = mapRef.current;
    if (!map) return;
    drawModeRef.current = true;
    setDrawMode(true);
    map.dragging.disable();
    map.getContainer().style.cursor = 'crosshair';
    map.getContainer().style.touchAction = 'none';
  }

  return (
    <div className="simulation-map-panel">
      <div className="simulation-map-actions">
        <button type="button" onClick={drawMode ? stopDrawing : beginDrawing} aria-pressed={drawMode}>
          {drawMode ? 'Cancel drawing' : 'Draw simulation area'}
        </button>
        <span aria-live="polite">
          {drawMode ? 'Drag from one corner of the desired domain to the opposite corner.' : 'The blue rectangle is the planned model domain.'}
        </span>
      </div>
      <div
        ref={containerRef}
        id="wizard-map"
        className="simulation-map"
        role="application"
        aria-label="Interactive OpenStreetMap for selecting the WRF simulation domain"
      />
      <p className="simulation-map-attribution">
        Basemap © OpenStreetMap contributors. Numeric coordinate fields remain available as a keyboard-accessible alternative.
      </p>
    </div>
  );
}

function NumericField(props: {
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="simulation-field" htmlFor={props.id}>
      <span>{props.label}</span>
      <input
        id={props.id}
        type="number"
        value={props.value}
        min={props.min}
        max={props.max}
        step={props.step}
        onChange={(event) => props.onChange(Number(event.currentTarget.value))}
      />
    </label>
  );
}

function Metric(props: { label: string; value: string; detail: string }) {
  return (
    <div className="simulation-metric">
      <span>{props.label}</span>
      <strong>{props.value}</strong>
      <small>{props.detail}</small>
    </div>
  );
}

export function SimulationWizard() {
  const [eventReference, setEventReference] = useState('xaver');
  const [profile, setProfile] = useState('balanced');
  const [start, setStart] = useState('2013-12-05T12:00');
  const [end, setEnd] = useState('2013-12-06T06:00');
  const [bounds, setBounds] = useState<DomainBounds>(DEFAULT_BOUNDS);
  const [expertEnabled, setExpertEnabled] = useState(false);
  const [gridSpacing, setGridSpacing] = useState(9);
  const [verticalLevels, setVerticalLevels] = useState(35);
  const [outputInterval, setOutputInterval] = useState(60);
  const [preview, setPreview] = useState<WizardPreview | null>(null);
  const [status, setStatus] = useState('Draw or enter an area, then calculate the WRF plan.');
  const [statusKind, setStatusKind] = useState('');
  const [busy, setBusy] = useState(false);

  function updateBound(name: keyof DomainBounds, value: number) {
    setBounds((current) => ({ ...current, [name]: value }));
    setPreview(null);
  }

  async function calculatePlan() {
    setBusy(true);
    setStatusKind('pending');
    setStatus('Calculating the grid and resource estimates on the server…');
    try {
      const planning: Record<string, unknown> = {
        label: 'map-selected-domain',
        bounds,
        period: {
          start: toUtcIso(start),
          end: toUtcIso(end),
        },
        quality_profile: profile,
      };
      if (expertEnabled) {
        planning.expert = {
          grid_spacing_km: gridSpacing,
          vertical_levels: verticalLevels,
          output_interval_minutes: outputInterval,
        };
      }
      const result = await requestJson<WizardPreview>('/api/wizard/preview', {
        method: 'POST',
        body: JSON.stringify({
          event_reference: eventReference,
          mode: 'real-data',
          planning,
        }),
      });
      setPreview(result);
      setStatusKind(result.valid ? 'success' : 'error');
      setStatus(result.valid
        ? 'The map domain is valid and a job configuration was generated.'
        : `The server rejected the plan: ${result.errors.join('; ')}`);
    } catch (error) {
      setPreview(null);
      setStatusKind('error');
      setStatus(error instanceof Error ? error.message : 'Domain planning failed.');
    } finally {
      setBusy(false);
    }
  }

  async function startDryRun() {
    if (!preview?.config) return;
    setBusy(true);
    setStatusKind('pending');
    setStatus('Starting the schema-validated dry-run…');
    try {
      const response = await requestJson<JobStartResponse>('/api/jobs', {
        method: 'POST',
        body: JSON.stringify(preview.config),
      });
      const jobId = response.job.job_id;
      const deadline = Date.now() + 30_000;
      while (Date.now() < deadline) {
        const jobResponse = await requestJson<{ job: { status: string } }>(`/api/jobs/${encodeURIComponent(jobId)}`);
        if (jobResponse.job.status === 'succeeded') {
          setStatusKind('success');
          setStatus(`Dry-run ${jobId} finished successfully.`);
          return;
        }
        if (jobResponse.job.status === 'failed' || jobResponse.job.status === 'cancelled') {
          setStatusKind('error');
          setStatus(`Dry-run ${jobId} ended with status ${jobResponse.job.status}.`);
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 250));
      }
      throw new Error(`Timed out waiting for dry-run ${jobId}.`);
    } catch (error) {
      setStatusKind('error');
      setStatus(error instanceof Error ? error.message : 'Dry-run failed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="simulation-wizard" aria-labelledby="simulation-wizard-title">
      <header>
        <p className="simulation-eyebrow">Guided simulation planning</p>
        <h2 id="simulation-wizard-title">Draw a real map area and estimate the WRF job</h2>
        <p>
          Select a rectangle directly on the map or use the coordinate fields. The server derives the
          WRF grid, time step, output count and honest resource ranges before it creates any files.
        </p>
      </header>

      <DomainMap bounds={bounds} onBoundsChange={setBounds} />

      <div className="simulation-form-grid">
        <label className="simulation-field" htmlFor="wizard-event">
          <span>Event or catalogue reference</span>
          <input
            id="wizard-event"
            value={eventReference}
            onChange={(event) => setEventReference(event.currentTarget.value)}
          />
        </label>
        <label className="simulation-field" htmlFor="wizard-profile">
          <span>Quality profile</span>
          <select
            id="wizard-profile"
            value={profile}
            onChange={(event) => setProfile(event.currentTarget.value)}
          >
            <option value="quick-preview">Quick preview</option>
            <option value="balanced">Balanced regional</option>
            <option value="detailed">Detailed regional</option>
          </select>
        </label>
        <label className="simulation-field" htmlFor="wizard-start">
          <span>Start (UTC)</span>
          <input
            id="wizard-start"
            type="datetime-local"
            value={start}
            onChange={(event) => setStart(event.currentTarget.value)}
          />
        </label>
        <label className="simulation-field" htmlFor="wizard-end">
          <span>End (UTC)</span>
          <input
            id="wizard-end"
            type="datetime-local"
            value={end}
            onChange={(event) => setEnd(event.currentTarget.value)}
          />
        </label>
        <NumericField id="wizard-west" label="West longitude" value={bounds.west} min={-180} max={180} step={0.1} onChange={(value) => updateBound('west', value)} />
        <NumericField id="wizard-south" label="South latitude" value={bounds.south} min={-89.9} max={89.9} step={0.1} onChange={(value) => updateBound('south', value)} />
        <NumericField id="wizard-east" label="East longitude" value={bounds.east} min={-180} max={180} step={0.1} onChange={(value) => updateBound('east', value)} />
        <NumericField id="wizard-north" label="North latitude" value={bounds.north} min={-89.9} max={89.9} step={0.1} onChange={(value) => updateBound('north', value)} />
      </div>

      <details className="simulation-expert">
        <summary onClick={() => setExpertEnabled(!expertEnabled)}>Expert overrides</summary>
        <div className="simulation-form-grid">
          <NumericField id="wizard-spacing" label="Grid spacing (km)" value={gridSpacing} min={1} max={50} step={1} onChange={setGridSpacing} />
          <NumericField id="wizard-levels" label="Vertical levels" value={verticalLevels} min={20} max={80} step={1} onChange={setVerticalLevels} />
          <NumericField id="wizard-output" label="Output interval (minutes)" value={outputInterval} min={5} max={360} step={5} onChange={setOutputInterval} />
        </div>
      </details>

      <div className="simulation-actions">
        <button type="button" onClick={calculatePlan} disabled={busy}>
          Plan domain and preview job
        </button>
        <button type="button" onClick={startDryRun} disabled={busy || !preview?.valid}>
          Start planned dry-run
        </button>
      </div>

      <p className={`simulation-status ${statusKind}`} role="status">{status}</p>

      {preview && (
        <div id="wizard-result" className="simulation-result">
          <h3>{preview.plan.quality_profile.label}</h3>
          <p>{preview.plan.quality_profile.description}</p>
          <div className="simulation-metrics">
            <Metric label="Grid" value={`${preview.plan.domain.e_we} × ${preview.plan.domain.e_sn}`} detail={`${preview.plan.domain.dx_km} km horizontal spacing`} />
            <Metric label="Domain" value={`${preview.plan.domain.width_km} × ${preview.plan.domain.height_km} km`} detail={preview.plan.domain.projection_recommendation} />
            <Metric label="Recommended RAM" value={`${preview.plan.resources.estimated_ram_gb.recommended} GB`} detail={`${preview.plan.resources.estimated_ram_gb.minimum} GB estimated minimum`} />
            <Metric label="Working storage" value={`${preview.plan.resources.estimated_storage_gb.working_total} GB`} detail={`${preview.plan.resources.estimated_storage_gb.wrf_output} GB estimated WRF output`} />
            <Metric label="Runtime range" value={`${preview.plan.resources.estimated_wall_clock_minutes.lower}–${preview.plan.resources.estimated_wall_clock_minutes.upper} min`} detail={preview.plan.resources.estimated_wall_clock_minutes.reference} />
            <Metric label="Output frames" value={`${preview.plan.resources.output_frames}`} detail={`${preview.plan.period.output_interval_minutes} minute interval`} />
          </div>
          {preview.warnings.length > 0 && (
            <ul className="simulation-warnings">
              {preview.warnings.map((warning) => <li key={warning}>{warning}</li>)}
            </ul>
          )}
          <h3>Generated job configuration</h3>
          <pre id="wizard-config-preview">{JSON.stringify(preview.config, null, 2)}</pre>
        </div>
      )}
    </section>
  );
}
