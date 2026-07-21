const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { test, expect } = require('@playwright/test');

const repoRoot = path.resolve(__dirname, '../..');
const screenshotDir = path.join(repoRoot, 'doc', 'user-guide', 'screenshots');
const port = Number(process.env.WORKBENCH_REAL_VIS_PORT || '8091');
const realVisualizationDir = process.env.WORKBENCH_REAL_VIS_DATA_DIR || '';
const requestedLayer = process.env.WORKBENCH_REAL_VIS_LAYER || 'max_wind10m';

function waitForServer(url, timeoutMs = 20000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const poll = () => {
      http.get(url, (res) => {
        res.resume();
        resolve();
      }).on('error', (error) => {
        if (Date.now() - start > timeoutMs) {
          reject(error);
          return;
        }
        setTimeout(poll, 250);
      });
    };
    poll();
  });
}

function requireRegularPath(candidate, label, kind = 'file') {
  const stat = fs.lstatSync(candidate);
  if (stat.isSymbolicLink()) {
    throw new Error(`${label} must not be a symbolic link: ${candidate}`);
  }
  if (kind === 'directory' ? !stat.isDirectory() : !stat.isFile()) {
    throw new Error(`${label} is not a regular ${kind}: ${candidate}`);
  }
}

function collectFiniteNumbers(value, output, limit = 50000) {
  if (output.length >= limit) return;
  if (typeof value === 'number' && Number.isFinite(value)) {
    output.push(value);
    return;
  }
  if (Array.isArray(value)) {
    for (const entry of value) {
      collectFiniteNumbers(entry, output, limit);
      if (output.length >= limit) return;
    }
  }
}

function requireRealVisualization() {
  if (!realVisualizationDir) {
    throw new Error('WORKBENCH_REAL_VIS_DATA_DIR must point to real WRF visualization artifacts. Synthetic fixtures are not allowed for this test.');
  }
  const resolved = path.resolve(realVisualizationDir);
  const metadataPath = path.join(resolved, 'metadata.json');
  const layersDirectory = path.join(resolved, 'layers');
  requireRegularPath(resolved, 'Visualization artifact directory', 'directory');
  requireRegularPath(metadataPath, 'Visualization metadata');
  requireRegularPath(layersDirectory, 'Visualization layers directory', 'directory');

  const metadata = JSON.parse(fs.readFileSync(metadataPath, 'utf-8'));
  const provenance = metadata.provenance;
  if (!provenance || provenance.mode !== 'wrf') {
    throw new Error(`Visualization metadata is not real WRF provenance: ${JSON.stringify(provenance)}`);
  }
  if (!Array.isArray(provenance.wrfout_files) || provenance.wrfout_files.length === 0) {
    throw new Error('Visualization metadata contains no wrfout provenance.');
  }

  const layer = (metadata.layers || []).find((entry) => entry.id === requestedLayer);
  if (!layer) {
    throw new Error(`Layer ${requestedLayer} not found in real visualization metadata.`);
  }
  if (typeof layer.file !== 'string' || path.isAbsolute(layer.file) || layer.file.split(/[\\/]/).includes('..')) {
    throw new Error(`Layer ${requestedLayer} has an unsafe file path.`);
  }
  const layerPath = path.resolve(resolved, layer.file);
  if (layerPath !== resolved && !layerPath.startsWith(`${resolved}${path.sep}`)) {
    throw new Error(`Layer ${requestedLayer} escaped the visualization directory.`);
  }
  requireRegularPath(layerPath, `Layer ${requestedLayer}`);

  const layerPayload = JSON.parse(fs.readFileSync(layerPath, 'utf-8'));
  const values = [];
  collectFiniteNumbers(layerPayload.data ?? layerPayload.frames, values);
  const distinct = new Set(values.map((value) => value.toPrecision(10)));
  if (values.length < 4 || distinct.size < 2) {
    throw new Error(`Layer ${requestedLayer} contains no spatially varying numeric field.`);
  }
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum) || maximum <= minimum) {
    throw new Error(`Layer ${requestedLayer} has an invalid numeric range.`);
  }

  return { resolved, metadata, layer, minimum, maximum };
}

test.use({
  viewport: { width: 1920, height: 1200 },
  deviceScaleFactor: 2,
});

test('capture real WRF weather map result @real-data @docs @slow', async ({ page }) => {
  const real = requireRealVisualization();
  fs.mkdirSync(screenshotDir, { recursive: true });

  const server = spawn('sh', ['visualization/web/serve.sh', real.resolved, String(port)], {
    cwd: repoRoot,
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: true,
  });
  server.stdout.on('data', (line) => process.stdout.write(line));
  server.stderr.on('data', (line) => process.stderr.write(line));

  try {
    await waitForServer(`http://127.0.0.1:${port}/`);
    await page.goto(`http://127.0.0.1:${port}/`);
    await expect(page.getByText('WRF Weather Viewer')).toBeVisible();
    await expect(page.locator('#job-id-label')).toContainText('Job:');
    const canvas = page.locator('#main-canvas');
    await expect(canvas).toBeVisible();
    await expect(page.locator('#overlay')).toHaveClass(/hidden/, { timeout: 20000 });

    const layerButton = page.locator(`.layer-btn[data-id="${requestedLayer}"]`);
    await expect(layerButton).toBeVisible();
    await layerButton.click();
    await expect(layerButton).toHaveClass(/active/);
    await expect(page.locator('#sb-layer')).toContainText(real.layer.label);

    const canvasStats = await canvas.evaluate((element) => {
      const context = element.getContext('2d', { willReadFrequently: true });
      if (!context || element.width < 100 || element.height < 100) {
        return { width: element.width, height: element.height, opaqueSamples: 0, uniqueColors: 0 };
      }
      const pixels = context.getImageData(0, 0, element.width, element.height).data;
      const pixelCount = element.width * element.height;
      const stride = Math.max(1, Math.floor(Math.sqrt(pixelCount / 20000)));
      let opaqueSamples = 0;
      const colors = new Set();
      for (let y = 0; y < element.height; y += stride) {
        for (let x = 0; x < element.width; x += stride) {
          const offset = (y * element.width + x) * 4;
          const alpha = pixels[offset + 3];
          if (alpha < 16) continue;
          opaqueSamples += 1;
          colors.add(`${pixels[offset] >> 3}:${pixels[offset + 1] >> 3}:${pixels[offset + 2] >> 3}`);
        }
      }
      return {
        width: element.width,
        height: element.height,
        opaqueSamples,
        uniqueColors: colors.size,
      };
    });
    expect(canvasStats.width).toBeGreaterThanOrEqual(100);
    expect(canvasStats.height).toBeGreaterThanOrEqual(100);
    expect(canvasStats.opaqueSamples).toBeGreaterThan(500);
    expect(canvasStats.uniqueColors).toBeGreaterThan(8);

    await page.screenshot({
      path: path.join(screenshotDir, 'xaver-07-weather-map.png'),
      fullPage: true,
    });
  } finally {
    if (!Number.isInteger(server.pid) || server.pid <= 0) return;
    try {
      process.kill(-server.pid, 'SIGTERM');
    } catch (error) {
      if (error && error.code !== 'ESRCH') throw error;
    }
  }
});
