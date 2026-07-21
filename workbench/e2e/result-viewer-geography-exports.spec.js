const { test, expect } = require('@playwright/test');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const REPO_ROOT = path.resolve(__dirname, '../..');
const JOB_ID = 'sim-aaaaaaaaaaaa-bbbbbbbbbbbb';
const SPECIFICATION_KEY = 'c'.repeat(64);

function jsonResponse(response, value) {
  const body = Buffer.from(JSON.stringify(value));
  response.writeHead(200, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': body.length,
    'Cache-Control': 'no-store',
  });
  response.end(body);
}

function viewerHtml() {
  const source = fs.readFileSync(
    path.join(REPO_ROOT, 'visualization', 'web', 'index.html'),
    'utf8',
  );
  return source
    .replace('<head>', `<head>\n<base href="/jobs/${JOB_ID}/results/">`)
    .replace(
      '<body>',
      '<body>\n<div id="workbench-model-notice">Model result, not an observation.</div>',
    )
    .replace(
      '</body>',
      '<script src="/web/result-viewer-tools.js"></script>\n</body>',
    );
}

function createFixtureServer() {
  const metadata = {
    jobId: JOB_ID,
    domain: {
      projection: 'Lambert Conformal',
      bounds: [2, 51, 14, 59],
      nx: 2,
      ny: 2,
      dx: 9000,
      dy: 9000,
      center_lat: 55,
      center_lon: 8,
    },
    times: ['2013-12-05T12:00:00Z', '2013-12-05T13:00:00Z'],
    layers: [
      {
        id: 'wind10m',
        label: '10 m wind speed',
        unit: 'm s-1',
        type: 'raster-time-series',
        file: 'layers/wind10m.json',
        vmin: 1,
        vmax: 8,
      },
      {
        id: 'max_wind10m',
        label: 'Maximum 10 m wind speed',
        unit: 'm s-1',
        type: 'raster-max',
        file: 'layers/max_wind10m.json',
        vmin: 5,
        vmax: 8,
      },
    ],
    provenance: {
      mode: 'wrf',
      wrfout_files: ['wrfout_d01_2013-12-05_12:00:00'],
    },
  };
  const layers = {
    [`/jobs/${JOB_ID}/results/layers/wind10m.json`]: {
      id: 'wind10m',
      label: '10 m wind speed',
      unit: 'm s-1',
      type: 'raster-time-series',
      nx: 2,
      ny: 2,
      vmin: 1,
      vmax: 8,
      times: metadata.times,
      frames: [
        [[1, 2], [3, 4]],
        [[5, 6], [7, 8]],
      ],
    },
    [`/jobs/${JOB_ID}/results/layers/max_wind10m.json`]: {
      id: 'max_wind10m',
      label: 'Maximum 10 m wind speed',
      unit: 'm s-1',
      type: 'raster-max',
      nx: 2,
      ny: 2,
      vmin: 5,
      vmax: 8,
      source_layer: 'wind10m',
      source_times: metadata.times,
      data: [[5, 6], [7, 8]],
    },
    [`/jobs/${JOB_ID}/results/layers/xlat.json`]: [
      [51, 51.1],
      [58.9, 59],
    ],
    [`/jobs/${JOB_ID}/results/layers/xlong.json`]: [
      [2, 14],
      [2.1, 13.9],
    ],
  };
  const manifest = {
    ok: true,
    results: {
      job_id: JOB_ID,
      status: 'SUCCEEDED',
      specification_key: SPECIFICATION_KEY,
      viewer_url: `/jobs/${JOB_ID}/results/`,
      artificial_weather_data: false,
      provenance: metadata.provenance,
      metadata,
      products: [],
    },
  };
  const specification = {
    ok: true,
    specification: {
      specification_key: SPECIFICATION_KEY,
      immutable: true,
      identity: {
        source: { repository_revision: 'd'.repeat(40) },
        era5_input: { plan_key: 'e'.repeat(64) },
        runtime: {
          wps: { identity: 'sha256:' + '1'.repeat(64) },
          wrf: { identity: 'sha256:' + '2'.repeat(64) },
          postprocessing: { identity: 'sha256:' + '3'.repeat(64) },
        },
      },
    },
  };
  const tools = fs.readFileSync(
    path.join(REPO_ROOT, 'workbench', 'web', 'result-viewer-tools.js'),
  );
  const html = Buffer.from(viewerHtml());

  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
    if (pathname === `/jobs/${JOB_ID}/results/`) {
      response.writeHead(200, {
        'Content-Type': 'text/html; charset=utf-8',
        'Content-Length': html.length,
        'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; object-src 'none'; base-uri 'self'",
      });
      response.end(html);
      return;
    }
    if (pathname === '/web/result-viewer-tools.js') {
      response.writeHead(200, {
        'Content-Type': 'application/javascript; charset=utf-8',
        'Content-Length': tools.length,
      });
      response.end(tools);
      return;
    }
    if (pathname === `/jobs/${JOB_ID}/results/metadata.json`) {
      jsonResponse(response, metadata);
      return;
    }
    if (Object.hasOwn(layers, pathname)) {
      jsonResponse(response, layers[pathname]);
      return;
    }
    if (pathname === `/api/simulations/${JOB_ID}/results`) {
      jsonResponse(response, manifest);
      return;
    }
    if (pathname === `/api/pipeline/specifications/${SPECIFICATION_KEY}`) {
      jsonResponse(response, specification);
      return;
    }
    response.writeHead(404, { 'Content-Type': 'text/plain' });
    response.end('not found');
  });
  return server;
}

async function readDownload(download) {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'wrf-result-download-'));
  const target = path.join(temporary, download.suggestedFilename());
  await download.saveAs(target);
  return { target, body: fs.readFileSync(target) };
}

test('integrated result viewer renders north-up geography and exports', async ({ page }) => {
  const server = createFixtureServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  try {
    await page.goto(`http://127.0.0.1:${port}/jobs/${JOB_ID}/results/`);
    await expect(page.locator('#workbench-map-tools')).toBeVisible();
    await expect(page.locator('#workbench-geography-toggle')).toBeChecked();
    await expect(page.locator('#time-display')).toContainText('(UTC)');
    await expect(page.locator('#overlay')).toHaveClass(/hidden/);

    await page.locator('#workbench-geography-toggle').uncheck();
    const orientation = await page.locator('#main-canvas').evaluate((canvas) => {
      const context = canvas.getContext('2d');
      const x = Math.max(1, Math.floor(canvas.width * 0.25));
      const top = [...context.getImageData(x, 5, 1, 1).data];
      const bottom = [...context.getImageData(x, canvas.height - 5, 1, 1).data];
      return { top, bottom };
    });
    expect(orientation.top.slice(0, 3)).not.toEqual(orientation.bottom.slice(0, 3));
    expect(orientation.top[1]).toBeGreaterThan(orientation.bottom[1]);

    const box = await page.locator('#main-canvas').boundingBox();
    await page.mouse.click(box.x + box.width * 0.55, box.y + box.height * 0.45);
    await expect(page.locator('#workbench-point-distance')).toContainText('nearest model grid point');
    await expect(page.locator('#workbench-export-csv')).toBeEnabled();

    const csvDownloadPromise = page.waitForEvent('download');
    await page.locator('#workbench-export-csv').click();
    const csv = await readDownload(await csvDownloadPromise);
    expect(csv.target).toMatch(/\.csv$/);
    const csvText = csv.body.toString('utf8');
    expect(csvText).toContain('job_id,layer_id');
    expect(csvText).toContain('model-coordinate-layer');
    expect(csvText).toContain(',true,false');

    const geoDownloadPromise = page.waitForEvent('download');
    await page.locator('#workbench-export-geojson').click();
    const geo = await readDownload(await geoDownloadPromise);
    const geojson = JSON.parse(geo.body.toString('utf8'));
    expect(geojson.type).toBe('FeatureCollection');
    expect(geojson.features).toHaveLength(4);
    expect(geojson.features[0].properties.coordinate_source).toBe('model-coordinate-layer');

    const provenancePromise = page.waitForEvent('download');
    await page.locator('#workbench-export-provenance').click();
    const provenance = JSON.parse((await readDownload(await provenancePromise)).body.toString('utf8'));
    expect(provenance.results.job_id).toBe(JOB_ID);
    expect(provenance.specification.specification_key).toBe(SPECIFICATION_KEY);
    expect(provenance.observation).toBe(false);

    await page.locator('[data-id="max_wind10m"]').click();
    await expect(page.locator('#time-display')).toHaveText('Maximum over simulation period');

    const pngPromise = page.waitForEvent('download');
    await page.locator('#workbench-export-png').click();
    const png = await readDownload(await pngPromise);
    expect(png.target).toMatch(/\.png$/);
    expect(png.body.length).toBeGreaterThan(1000);
    expect(png.body.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
