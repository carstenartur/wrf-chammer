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

function requireRealVisualizationDir() {
  if (!realVisualizationDir) {
    throw new Error('WORKBENCH_REAL_VIS_DATA_DIR must point to real WRF visualization artifacts. Synthetic fixtures are not allowed for this test.');
  }
  const resolved = path.resolve(realVisualizationDir);
  const metadata = path.join(resolved, 'metadata.json');
  const layers = path.join(resolved, 'layers');
  if (!fs.existsSync(metadata) || !fs.existsSync(layers)) {
    throw new Error(`WORKBENCH_REAL_VIS_DATA_DIR is not a visualization artifact directory: ${resolved}`);
  }
  return resolved;
}

test.use({
  viewport: { width: 1920, height: 1200 },
  deviceScaleFactor: 2,
});

test('capture real WRF weather map result @real-data @docs @slow', async ({ page }) => {
  const dataDir = requireRealVisualizationDir();
  fs.mkdirSync(screenshotDir, { recursive: true });

  const metadata = JSON.parse(fs.readFileSync(path.join(dataDir, 'metadata.json'), 'utf-8'));
  const layer = (metadata.layers || []).find((entry) => entry.id === requestedLayer);
  if (!layer) {
    throw new Error(`Layer ${requestedLayer} not found in real visualization metadata.`);
  }

  const server = spawn('sh', ['visualization/web/serve.sh', dataDir, String(port)], {
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
    await expect(page.locator('#main-canvas')).toBeVisible();
    await expect(page.locator('#overlay')).toHaveClass(/hidden/, { timeout: 20000 });

    const layerButton = page.locator(`.layer-btn[data-id="${requestedLayer}"]`);
    await expect(layerButton).toBeVisible();
    await layerButton.click();
    await expect(layerButton).toHaveClass(/active/);
    await expect(page.locator('#sb-layer')).toContainText(layer.label);

    await page.screenshot({
      path: path.join(screenshotDir, 'xaver-07-weather-map.png'),
      fullPage: true,
    });
  } finally {
    if (!Number.isInteger(server.pid) || server.pid <= 0) {
      return;
    }
    try {
      process.kill(-server.pid, 'SIGTERM');
    } catch (error) {
      if (error && error.code !== 'ESRCH') {
        throw error;
      }
    }
  }
});
