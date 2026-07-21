(function (global) {
  'use strict';

  const MAX_GEOJSON_POINTS = 100000;

  // Reduced Natural Earth 1:110m Admin-0 geometry for the North Sea/Xaver
  // documentation region. Natural Earth data is public domain.
  const COUNTRIES = [
    {
      name: 'Belgium',
      polygons: [[
        [6.156658, 50.803721], [6.043073, 50.128052], [5.782417, 50.090328],
        [5.674052, 49.529484], [4.799222, 49.985373], [4.286023, 49.907497],
        [3.588184, 50.378992], [3.123252, 50.780363], [2.658422, 50.796848],
        [2.513573, 51.148506], [3.314971, 51.345781], [4.047071, 51.267259],
        [4.973991, 51.475024], [5.606976, 51.037298], [6.156658, 50.803721],
      ]],
    },
    {
      name: 'Netherlands',
      polygons: [[
        [6.90514, 53.482162], [7.092053, 53.144043], [6.84287, 52.22844],
        [6.589397, 51.852029], [5.988658, 51.851616], [6.156658, 50.803721],
        [5.606976, 51.037298], [4.973991, 51.475024], [4.047071, 51.267259],
        [3.314971, 51.345755], [3.830289, 51.620545], [4.705997, 53.091798],
        [6.074183, 53.510403], [6.90514, 53.482162],
      ]],
    },
    {
      name: 'Germany',
      polygons: [[
        [14.119686, 53.757029], [14.353315, 53.248171], [14.074521, 52.981263],
        [14.4376, 52.62485], [14.685026, 52.089947], [14.607098, 51.745188],
        [15.016996, 51.106674], [14.570718, 51.002339], [14.307013, 51.117268],
        [14.056228, 50.926918], [13.338132, 50.733234], [12.240111, 50.266338],
        [12.521024, 49.547415], [13.595946, 48.877172], [13.243357, 48.416115],
        [12.884103, 48.289146], [12.932627, 47.467646], [12.62076, 47.672388],
        [11.426414, 47.523766], [10.544504, 47.566399], [10.402084, 47.302488],
        [9.896068, 47.580197], [8.522612, 47.830828], [8.317301, 47.61358],
        [7.466759, 47.620582], [7.593676, 48.333019], [8.099279, 49.017784],
        [6.65823, 49.201958], [6.18632, 49.463803], [6.242751, 49.902226],
        [6.043073, 50.128052], [6.156658, 50.803721], [5.988658, 51.851616],
        [6.589397, 51.852029], [6.84287, 52.22844], [7.092053, 53.144043],
        [6.90514, 53.482162], [7.100425, 53.693932], [7.936239, 53.748296],
        [8.121706, 53.527792], [8.800734, 54.020786], [8.572118, 54.395646],
        [8.526229, 54.962744], [9.282049, 54.830865], [9.921906, 54.983104],
        [9.93958, 54.596642], [10.950112, 54.363607], [10.939467, 54.008693],
        [11.956252, 54.196486], [12.51844, 54.470371], [13.647467, 54.075511],
        [14.119686, 53.757029],
      ]],
    },
    {
      name: 'Denmark',
      polygons: [
        [
          [9.921906, 54.983104], [9.282049, 54.830865], [8.526229, 54.962744],
          [8.120311, 55.517723], [8.089977, 56.540012], [8.256582, 56.809969],
          [8.543438, 57.110003], [9.424469, 57.172066], [9.775559, 57.447941],
          [10.580006, 57.730017], [10.546106, 57.215733], [10.25, 56.890016],
          [10.369993, 56.609982], [10.912182, 56.458621], [10.667804, 56.081383],
          [10.369993, 56.190007], [9.649985, 55.469999], [9.921906, 54.983104],
        ],
        [
          [12.370904, 56.111407], [12.690006, 55.609991], [12.089991, 54.800015],
          [11.043543, 55.364864], [10.903914, 55.779955], [12.370904, 56.111407],
        ],
      ],
    },
    {
      name: 'Sweden',
      polygons: [[
        [11.027369, 58.856149], [11.468272, 59.432393], [12.300366, 60.117933],
        [12.631147, 61.293572], [11.992064, 61.800362], [11.930569, 63.128318],
        [13.571916, 64.049114], [13.919905, 64.445421], [15.108411, 66.193867],
        [16.768879, 68.013937], [17.993868, 68.567391], [19.87856, 68.407194],
        [20.645593, 69.106247], [21.978535, 68.616846], [23.539473, 67.936009],
        [23.56588, 66.396051], [23.903379, 66.006927], [22.183173, 65.723741],
        [21.213517, 65.026005], [19.778876, 63.609554], [17.847779, 62.7494],
        [17.119555, 61.341166], [18.787722, 60.081914], [17.869225, 58.953766],
        [16.829185, 58.719827], [16.44771, 57.041118], [15.879786, 56.104302],
        [14.666681, 56.200885], [14.100721, 55.407781], [12.942911, 55.361737],
        [12.625101, 56.30708], [11.787942, 57.441817], [11.027369, 58.856149],
      ]],
    },
  ];

  const LABELS = [
    ['North Sea', 4.8, 55.2],
    ['Netherlands', 5.3, 52.35],
    ['Germany', 10.2, 52.2],
    ['Denmark', 9.5, 56.25],
    ['Sweden', 14.8, 57.3],
  ];

  function finiteNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function normalizeBounds(domain) {
    const raw = domain?.bounds;
    let west;
    let south;
    let east;
    let north;
    if (Array.isArray(raw) && raw.length >= 4) {
      [west, south, east, north] = raw;
    } else if (raw && typeof raw === 'object') {
      ({ west, south, east, north } = raw);
    } else {
      return null;
    }
    const values = [west, south, east, north].map(finiteNumber);
    if (values.some((value) => value === null)) return null;
    if (values[0] >= values[2] || values[1] >= values[3]) return null;
    return {
      west: values[0],
      south: values[1],
      east: values[2],
      north: values[3],
    };
  }

  function activeGrid(layer, timeIdx) {
    if (!layer) return [];
    if (layer.type === 'raster-time-series') {
      const frames = Array.isArray(layer.frames) ? layer.frames : [];
      return frames[Math.min(Math.max(0, timeIdx), Math.max(0, frames.length - 1))] || [];
    }
    if (layer.type === 'raster-max') {
      return Array.isArray(layer.data) ? layer.data : [];
    }
    return [];
  }

  function northUpGridCell(px, py, width, height, nx, ny) {
    if (!(width > 0 && height > 0 && nx > 0 && ny > 0)) return null;
    const i = Math.max(0, Math.min(nx - 1, Math.floor(px / width * nx)));
    const screenRow = Math.max(0, Math.min(ny - 1, Math.floor(py / height * ny)));
    return { i, j: ny - 1 - screenRow };
  }

  function coordinateToCanvas(lon, lat, bounds, width, height) {
    if (!bounds || !(width > 0 && height > 0)) return null;
    return {
      x: (lon - bounds.west) / (bounds.east - bounds.west) * width,
      y: (bounds.north - lat) / (bounds.north - bounds.south) * height,
    };
  }

  function canvasToCoordinate(px, py, bounds, width, height) {
    if (!bounds || !(width > 0 && height > 0)) return null;
    return {
      lon: bounds.west + px / width * (bounds.east - bounds.west),
      lat: bounds.north - py / height * (bounds.north - bounds.south),
    };
  }

  function haversineKm(a, b) {
    if (!a || !b) return null;
    const radians = (value) => value * Math.PI / 180;
    const lat1 = radians(a.lat);
    const lat2 = radians(b.lat);
    const dLat = lat2 - lat1;
    const dLon = radians(b.lon - a.lon);
    const h = Math.sin(dLat / 2) ** 2
      + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
    return 6371.0088 * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(Math.max(0, 1 - h)));
  }

  function csvCell(value) {
    const text = value === null || value === undefined ? '' : String(value);
    return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  }

  function timeLabel(layer, timeIdx) {
    if (layer?.type === 'raster-max') return 'maximum-over-simulation-period';
    const times = Array.isArray(layer?.times) ? layer.times : [];
    return times[timeIdx] || '';
  }

  function buildPointCsv(options) {
    const {
      jobId, layer, layerMeta, selection, coordinateSource = 'unknown',
    } = options;
    if (!selection || !layer) throw new Error('Select a model grid point first.');
    const header = [
      'job_id', 'layer_id', 'layer_label', 'unit', 'time',
      'grid_i', 'grid_j', 'latitude', 'longitude',
      'clicked_latitude', 'clicked_longitude', 'distance_km',
      'coordinate_source', 'value', 'model_result', 'observation',
    ];
    const rows = [header];
    if (layer.type === 'raster-time-series') {
      const times = Array.isArray(layer.times) ? layer.times : [];
      const frames = Array.isArray(layer.frames) ? layer.frames : [];
      const count = Math.max(times.length, frames.length);
      for (let index = 0; index < count; index++) {
        const value = frames[index]?.[selection.j]?.[selection.i];
        rows.push([
          jobId, layer.id, layerMeta?.label || layer.id, layerMeta?.unit || layer.unit || '',
          times[index] || '', selection.i, selection.j,
          selection.gridLat, selection.gridLon,
          selection.clickLat, selection.clickLon, selection.distanceKm,
          coordinateSource, value, true, false,
        ]);
      }
    } else {
      rows.push([
        jobId, layer.id, layerMeta?.label || layer.id, layerMeta?.unit || layer.unit || '',
        'maximum-over-simulation-period', selection.i, selection.j,
        selection.gridLat, selection.gridLon,
        selection.clickLat, selection.clickLon, selection.distanceKm,
        coordinateSource, layer.data?.[selection.j]?.[selection.i], true, false,
      ]);
    }
    return rows.map((row) => row.map(csvCell).join(',')).join('\n') + '\n';
  }

  function buildLayerGeoJson(options) {
    const {
      jobId, layer, layerMeta, timeIdx, bounds, coordinateAt,
      coordinateSource = 'unknown', maxPoints = MAX_GEOJSON_POINTS,
    } = options;
    const grid = activeGrid(layer, timeIdx);
    const ny = grid.length;
    const nx = ny ? grid[0].length : 0;
    if (!nx || !ny) throw new Error('The active layer contains no raster grid.');
    if (nx * ny > maxPoints) {
      throw new Error(`GeoJSON export is limited to ${maxPoints.toLocaleString()} grid points.`);
    }
    const features = [];
    for (let j = 0; j < ny; j++) {
      for (let i = 0; i < nx; i++) {
        const coordinate = coordinateAt(i, j);
        const lon = finiteNumber(coordinate?.lon);
        const lat = finiteNumber(coordinate?.lat);
        const value = finiteNumber(grid[j]?.[i]);
        if (lon === null || lat === null || value === null) continue;
        features.push({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [lon, lat] },
          properties: {
            job_id: jobId,
            layer_id: layer.id,
            layer_label: layerMeta?.label || layer.id,
            unit: layerMeta?.unit || layer.unit || '',
            time: timeLabel(layer, timeIdx),
            grid_i: i,
            grid_j: j,
            coordinate_source: coordinateSource,
            value,
            model_result: true,
            observation: false,
          },
        });
      }
    }
    return {
      type: 'FeatureCollection',
      bbox: bounds ? [bounds.west, bounds.south, bounds.east, bounds.north] : undefined,
      properties: {
        job_id: jobId,
        layer_id: layer.id,
        unit: layerMeta?.unit || layer.unit || '',
        time: timeLabel(layer, timeIdx),
        representation: 'model-grid-point',
        model_result: true,
        observation: false,
      },
      features,
    };
  }

  function parseJobId(pathname) {
    const match = String(pathname || '').match(/^\/jobs\/(sim-[0-9a-f]{12}-[0-9a-f]{12})\/results\/?$/);
    return match ? match[1] : '';
  }

  function safeFilename(value) {
    return String(value || 'result').replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'result';
  }

  function installViewerTools() {
    if (typeof document === 'undefined' || typeof state === 'undefined'
        || typeof canvas === 'undefined' || typeof ctx === 'undefined'
        || typeof renderLayer !== 'function' || typeof getGridCoords !== 'function') {
      return false;
    }

    const jobId = parseJobId(global.location?.pathname);
    let geographyEnabled = true;
    let selection = null;
    const coordinateSource = () => (
      state.layerCache.__xlat__ && state.layerCache.__xlong__
        ? 'model-coordinate-layer'
        : 'linear-domain-bounds'
    );

    function layerMeta() {
      return state.metadata?.layers?.find((entry) => entry.id === state.activeLayerId) || null;
    }

    function coordinateAt(i, j) {
      const coordinate = getGridCoords(i, j);
      return coordinate ? {
        lat: finiteNumber(coordinate.lat),
        lon: finiteNumber(coordinate.lon),
      } : null;
    }

    function drawGeography(bounds) {
      if (!geographyEnabled || !bounds) return;
      ctx.save();
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';

      const lonSpan = bounds.east - bounds.west;
      const latSpan = bounds.north - bounds.south;
      const step = Math.max(lonSpan, latSpan) <= 6 ? 1 : Math.max(lonSpan, latSpan) <= 16 ? 2 : 5;
      ctx.lineWidth = 1;
      ctx.strokeStyle = 'rgba(255,255,255,0.28)';
      ctx.setLineDash([3, 4]);
      for (let lon = Math.ceil(bounds.west / step) * step; lon <= bounds.east; lon += step) {
        const top = coordinateToCanvas(lon, bounds.north, bounds, canvas.width, canvas.height);
        const bottom = coordinateToCanvas(lon, bounds.south, bounds, canvas.width, canvas.height);
        ctx.beginPath();
        ctx.moveTo(top.x, top.y);
        ctx.lineTo(bottom.x, bottom.y);
        ctx.stroke();
      }
      for (let lat = Math.ceil(bounds.south / step) * step; lat <= bounds.north; lat += step) {
        const left = coordinateToCanvas(bounds.west, lat, bounds, canvas.width, canvas.height);
        const right = coordinateToCanvas(bounds.east, lat, bounds, canvas.width, canvas.height);
        ctx.beginPath();
        ctx.moveTo(left.x, left.y);
        ctx.lineTo(right.x, right.y);
        ctx.stroke();
      }
      ctx.setLineDash([]);

      for (const country of COUNTRIES) {
        for (const ring of country.polygons) {
          ctx.beginPath();
          ring.forEach(([lon, lat], index) => {
            const point = coordinateToCanvas(lon, lat, bounds, canvas.width, canvas.height);
            if (index === 0) ctx.moveTo(point.x, point.y);
            else ctx.lineTo(point.x, point.y);
          });
          ctx.closePath();
          ctx.lineWidth = 3.2;
          ctx.strokeStyle = 'rgba(0,0,0,0.72)';
          ctx.stroke();
          ctx.lineWidth = 1.15;
          ctx.strokeStyle = 'rgba(255,255,255,0.95)';
          ctx.stroke();
        }
      }

      ctx.font = '700 12px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      for (const [label, lon, lat] of LABELS) {
        if (lon < bounds.west || lon > bounds.east || lat < bounds.south || lat > bounds.north) continue;
        const point = coordinateToCanvas(lon, lat, bounds, canvas.width, canvas.height);
        ctx.lineWidth = 3.5;
        ctx.strokeStyle = 'rgba(0,0,0,0.8)';
        ctx.strokeText(label, point.x, point.y);
        ctx.fillStyle = 'rgba(255,255,255,0.95)';
        ctx.fillText(label, point.x, point.y);
      }
      ctx.restore();
    }

    function drawSelection(bounds) {
      if (!selection || !bounds) return;
      const point = coordinateToCanvas(
        selection.gridLon, selection.gridLat, bounds, canvas.width, canvas.height,
      );
      if (!point) return;
      ctx.save();
      ctx.beginPath();
      ctx.arc(point.x, point.y, 6, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255,255,255,0.95)';
      ctx.fill();
      ctx.lineWidth = 3;
      ctx.strokeStyle = 'rgba(0,0,0,0.9)';
      ctx.stroke();
      ctx.restore();
    }

    renderLayer = function renderNorthUp(layer, timeIdx) { // eslint-disable-line no-global-assign
      if (!layer) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        return;
      }
      const grid = activeGrid(layer, timeIdx);
      const ny = grid.length;
      const nx = ny ? grid[0].length : 0;
      if (!nx || !ny) return;
      const vmin = finiteNumber(layer.vmin) ?? 0;
      const vmax = finiteNumber(layer.vmax) ?? 1;
      const range = vmax - vmin || 1;
      const cmap = layerColormap(layer.id);
      const cellW = canvas.width / nx;
      const cellH = canvas.height / ny;
      const image = ctx.createImageData(canvas.width, canvas.height);
      for (let py = 0; py < canvas.height; py++) {
        const j = ny - 1 - Math.min(ny - 1, Math.floor(py / cellH));
        const row = grid[j] || [];
        for (let px = 0; px < canvas.width; px++) {
          const i = Math.min(nx - 1, Math.floor(px / cellW));
          const value = finiteNumber(row[i]);
          const offset = (py * canvas.width + px) * 4;
          if (value === null) {
            image.data[offset + 3] = 0;
            continue;
          }
          const [red, green, blue] = interpolateColor(cmap, (value - vmin) / range);
          image.data[offset] = red;
          image.data[offset + 1] = green;
          image.data[offset + 2] = blue;
          image.data[offset + 3] = 220;
        }
      }
      ctx.putImageData(image, 0, 0);
      if (nx <= 20 && ny <= 20) {
        ctx.save();
        ctx.strokeStyle = 'rgba(255,255,255,0.15)';
        ctx.lineWidth = 1;
        for (let i = 0; i <= nx; i++) {
          ctx.beginPath();
          ctx.moveTo(i * cellW, 0);
          ctx.lineTo(i * cellW, canvas.height);
          ctx.stroke();
        }
        for (let row = 0; row <= ny; row++) {
          ctx.beginPath();
          ctx.moveTo(0, row * cellH);
          ctx.lineTo(canvas.width, row * cellH);
          ctx.stroke();
        }
        ctx.restore();
      }
      const bounds = normalizeBounds(state.metadata?.domain);
      drawGeography(bounds);
      drawSelection(bounds);
    };

    canvasToGrid = function canvasToNorthUpGrid(px, py) { // eslint-disable-line no-global-assign
      const layer = state.layerCache[state.activeLayerId];
      const grid = activeGrid(layer, state.timeIdx);
      const ny = grid.length;
      const nx = ny ? grid[0].length : 0;
      return northUpGridCell(px, py, canvas.width, canvas.height, nx, ny);
    };

    updateTimeDisplay = function updateExplicitTimeDisplay(layer) { // eslint-disable-line no-global-assign
      const display = document.getElementById('time-display');
      if (!display) return;
      if (layer?.type === 'raster-max') {
        display.textContent = 'Maximum over simulation period';
        return;
      }
      const value = timeLabel(layer, state.timeIdx);
      display.textContent = value ? `${value}${value.endsWith('Z') ? ' (UTC)' : ''}` : '(no time info)';
    };

    function setStatus(message, error = false) {
      const status = document.getElementById('workbench-export-status');
      if (!status) return;
      status.textContent = message;
      status.style.color = error ? '#f87171' : '#8bd5a8';
    }

    function downloadBlob(blob, filename) {
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    }

    function exportPng() {
      const layer = state.layerCache[state.activeLayerId];
      const entry = layerMeta();
      if (!layer || !entry) throw new Error('Select a layer first.');
      const header = 72;
      const footer = 48;
      const output = document.createElement('canvas');
      output.width = canvas.width;
      output.height = canvas.height + header + footer;
      const outputContext = output.getContext('2d');
      outputContext.fillStyle = '#0f1117';
      outputContext.fillRect(0, 0, output.width, output.height);
      outputContext.fillStyle = '#e0e4ef';
      outputContext.font = '700 18px system-ui, sans-serif';
      outputContext.fillText(entry.label || layer.id, 18, 26);
      outputContext.font = '13px system-ui, sans-serif';
      outputContext.fillStyle = '#aab3ca';
      outputContext.fillText(
        `Job ${jobId} · ${timeLabel(layer, state.timeIdx) || 'no time'} · ${entry.unit || ''}`,
        18, 49,
      );
      outputContext.drawImage(canvas, 0, header);
      outputContext.fillStyle = '#aab3ca';
      outputContext.font = '12px system-ui, sans-serif';
      outputContext.fillText('WRF model result · not an observation', 18, output.height - 18);
      const cmap = layerColormap(layer.id);
      const gradient = outputContext.createLinearGradient(output.width - 230, 0, output.width - 20, 0);
      cmap.forEach((color, index) => {
        gradient.addColorStop(index / Math.max(1, cmap.length - 1), `rgb(${color.join(',')})`);
      });
      outputContext.fillStyle = gradient;
      outputContext.fillRect(output.width - 230, output.height - 34, 210, 12);
      outputContext.fillStyle = '#e0e4ef';
      outputContext.textAlign = 'right';
      outputContext.fillText(`${entry.vmin}–${entry.vmax} ${entry.unit || ''}`, output.width - 20, output.height - 18);
      output.toBlob((blob) => {
        if (!blob) {
          setStatus('PNG export failed.', true);
          return;
        }
        downloadBlob(
          blob,
          `${safeFilename(jobId)}-${safeFilename(layer.id)}-${safeFilename(timeLabel(layer, state.timeIdx))}.png`,
        );
        setStatus('PNG map exported.');
      }, 'image/png');
    }

    function exportCsv() {
      const layer = state.layerCache[state.activeLayerId];
      const csv = buildPointCsv({
        jobId,
        layer,
        layerMeta: layerMeta(),
        selection,
        coordinateSource: coordinateSource(),
      });
      downloadBlob(
        new Blob([csv], { type: 'text/csv;charset=utf-8' }),
        `${safeFilename(jobId)}-${safeFilename(layer.id)}-point-${selection.i}-${selection.j}.csv`,
      );
      setStatus('Selected point time series exported.');
    }

    function exportGeoJson() {
      const layer = state.layerCache[state.activeLayerId];
      if (!layer) throw new Error('Select a layer first.');
      const geojson = buildLayerGeoJson({
        jobId,
        layer,
        layerMeta: layerMeta(),
        timeIdx: state.timeIdx,
        bounds: normalizeBounds(state.metadata?.domain),
        coordinateAt,
        coordinateSource: coordinateSource(),
      });
      downloadBlob(
        new Blob([JSON.stringify(geojson, null, 2) + '\n'], { type: 'application/geo+json' }),
        `${safeFilename(jobId)}-${safeFilename(layer.id)}-${safeFilename(timeLabel(layer, state.timeIdx))}.geojson`,
      );
      setStatus(`GeoJSON exported with ${geojson.features.length.toLocaleString()} model grid points.`);
    }

    async function exportProvenance() {
      if (!jobId) throw new Error('The simulation job ID is unavailable.');
      const manifestResponse = await fetch(`/api/simulations/${encodeURIComponent(jobId)}/results`);
      if (!manifestResponse.ok) throw new Error(`Result manifest HTTP ${manifestResponse.status}`);
      const manifestPayload = await manifestResponse.json();
      const results = manifestPayload.results;
      const specificationKey = results?.specification_key;
      if (typeof specificationKey !== 'string' || !/^[0-9a-f]{64}$/.test(specificationKey)) {
        throw new Error('The immutable specification key is invalid.');
      }
      const specificationResponse = await fetch(
        `/api/pipeline/specifications/${encodeURIComponent(specificationKey)}`,
      );
      if (!specificationResponse.ok) {
        throw new Error(`Immutable specification HTTP ${specificationResponse.status}`);
      }
      const specificationPayload = await specificationResponse.json();
      const evidence = {
        exported_at: new Date().toISOString(),
        model_result: true,
        observation: false,
        results,
        specification: specificationPayload.specification,
      };
      downloadBlob(
        new Blob([JSON.stringify(evidence, null, 2) + '\n'], { type: 'application/json' }),
        `${safeFilename(jobId)}-provenance.json`,
      );
      setStatus('Run configuration and provenance exported.');
    }

    function invoke(action) {
      try {
        const result = action();
        if (result && typeof result.catch === 'function') {
          result.catch((error) => setStatus(error.message, true));
        }
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    const sidebar = document.querySelector('.sidebar');
    if (sidebar && !document.getElementById('workbench-map-tools')) {
      const section = document.createElement('section');
      section.id = 'workbench-map-tools';
      section.innerHTML = `
        <h2>Map & export</h2>
        <label style="display:flex;gap:7px;align-items:center;font-size:.78rem;color:#c8cedf">
          <input id="workbench-geography-toggle" type="checkbox" checked>
          Coastlines, borders and graticule
        </label>
        <button class="layer-btn" id="workbench-export-png" type="button">Export current map as PNG</button>
        <button class="layer-btn" id="workbench-export-csv" type="button" disabled>Export selected point CSV</button>
        <button class="layer-btn" id="workbench-export-geojson" type="button">Export current layer GeoJSON</button>
        <button class="layer-btn" id="workbench-export-provenance" type="button">Export run configuration & provenance</button>
        <div id="workbench-export-status" role="status" aria-live="polite" style="font-size:.72rem;color:#8bd5a8;min-height:1.2em"></div>
        <div style="font-size:.68rem;color:#5f6478">Exports are model grid values, not observations.</div>
      `;
      sidebar.appendChild(section);
      document.getElementById('workbench-geography-toggle').addEventListener('change', (event) => {
        geographyEnabled = Boolean(event.target.checked);
        const layer = state.layerCache[state.activeLayerId];
        if (layer) renderLayer(layer, state.timeIdx);
      });
      document.getElementById('workbench-export-png').addEventListener('click', () => invoke(exportPng));
      document.getElementById('workbench-export-csv').addEventListener('click', () => invoke(exportCsv));
      document.getElementById('workbench-export-geojson').addEventListener('click', () => invoke(exportGeoJson));
      document.getElementById('workbench-export-provenance').addEventListener('click', () => invoke(exportProvenance));
    }

    canvas.addEventListener('click', (event) => {
      const rect = canvas.getBoundingClientRect();
      const px = event.clientX - rect.left;
      const py = event.clientY - rect.top;
      const cell = canvasToGrid(px, py);
      const bounds = normalizeBounds(state.metadata?.domain);
      if (!cell || !bounds) return;
      const clicked = canvasToCoordinate(px, py, bounds, canvas.width, canvas.height);
      const grid = coordinateAt(cell.i, cell.j);
      if (!clicked || !grid || grid.lat === null || grid.lon === null) return;
      selection = {
        i: cell.i,
        j: cell.j,
        clickLat: clicked.lat,
        clickLon: clicked.lon,
        gridLat: grid.lat,
        gridLon: grid.lon,
        distanceKm: haversineKm(clicked, grid),
      };
      const csvButton = document.getElementById('workbench-export-csv');
      if (csvButton) csvButton.disabled = false;
      const info = document.getElementById('point-info');
      if (info) {
        let distance = document.getElementById('workbench-point-distance');
        if (!distance) {
          distance = document.createElement('div');
          distance.id = 'workbench-point-distance';
          distance.style.marginTop = '7px';
          distance.style.fontSize = '.72rem';
          distance.style.color = '#93c5fd';
          info.appendChild(distance);
        }
        distance.textContent = `Clicked ${clicked.lat.toFixed(3)}°, ${clicked.lon.toFixed(3)}°; nearest model grid point is ${selection.distanceKm.toFixed(2)} km away (${coordinateSource()}).`;
      }
      const layer = state.layerCache[state.activeLayerId];
      if (layer) renderLayer(layer, state.timeIdx);
    });

    const active = state.layerCache[state.activeLayerId];
    if (active) renderLayer(active, state.timeIdx);
    return true;
  }

  const api = {
    activeGrid,
    buildLayerGeoJson,
    buildPointCsv,
    canvasToCoordinate,
    coordinateToCanvas,
    haversineKm,
    normalizeBounds,
    northUpGridCell,
    parseJobId,
    timeLabel,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    global.WrfResultViewerTools = api;
    installViewerTools();
  }
})(typeof window !== 'undefined' ? window : globalThis);
