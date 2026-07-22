const fs = require('fs');
const path = require('path');
const { test, expect } = require('@playwright/test');
const { installDocumentationTileProvider } = require('./documentation-tile-provider');
const { resolveScreenshotDirectory } = require('./screenshot-output');

const repoRoot = path.resolve(__dirname, '../..');
const screenshotDir = resolveScreenshotDirectory(
  repoRoot,
  process.env.WRF_SCREENSHOT_OUTPUT_DIR,
);
const basemapMode = process.env.WRF_SCREENSHOT_BASEMAP || 'openstreetmap';
const OPENSTREETMAP_HOST = 'tile.openstreetmap.org';
const OPENSTREETMAP_TEMPLATE = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';

if (
  process.env.CI === 'true'
  && basemapMode === 'openstreetmap'
  && process.env.WRF_ALLOW_LIVE_OSM_SCREENSHOTS !== '1'
) {
  throw new Error(
    'Live OpenStreetMap screenshots in CI require WRF_ALLOW_LIVE_OSM_SCREENSHOTS=1.',
  );
}

async function capture(page, fileName) {
  fs.mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({
    path: path.join(screenshotDir, fileName),
    fullPage: true,
  });
}

async function captureElement(locator, fileName) {
  fs.mkdirSync(screenshotDir, { recursive: true });
  await locator.screenshot({
    path: path.join(screenshotDir, fileName),
  });
}

function writeMapProvenance(fileName, details) {
  const screenshotPath = path.join(screenshotDir, fileName);
  const body = fs.readFileSync(screenshotPath);
  const crypto = require('node:crypto');
  const provenance = {
    schema_version: 1,
    screenshot: fileName,
    screenshot_sha256: crypto.createHash('sha256').update(body).digest('hex'),
    basemap: details.basemap,
    tile_url_template: details.tileUrlTemplate || null,
    tile_host: details.tileHost || null,
    successful_tile_responses: details.successfulTileResponses || 0,
    attribution: details.attribution,
    generated_at: new Date().toISOString(),
    generator: 'workbench/e2e/xaver-user-guide.spec.js',
    note: details.note,
  };
  fs.writeFileSync(
    path.join(screenshotDir, `${fileName}.provenance.json`),
    `${JSON.stringify(provenance, null, 2)}\n`,
    'utf8',
  );
}

async function gotoDocumentationWorkbench(page) {
  if (!['openstreetmap', 'offline-natural-earth'].includes(basemapMode)) {
    throw new Error(`Unsupported WRF_SCREENSHOT_BASEMAP mode: ${basemapMode}`);
  }

  const liveTileStats = {
    requests: 0,
    successful: 0,
    hosts: new Set(),
  };
  let offlineTileStats = null;

  if (basemapMode === 'offline-natural-earth') {
    offlineTileStats = await installDocumentationTileProvider(page);
  } else {
    page.on('response', (response) => {
      let parsed;
      try {
        parsed = new URL(response.url());
      } catch (_) {
        return;
      }
      if (parsed.hostname !== OPENSTREETMAP_HOST || !/\/\d+\/\d+\/\d+\.png$/.test(parsed.pathname)) {
        return;
      }
      liveTileStats.requests += 1;
      liveTileStats.hosts.add(parsed.hostname);
      if (response.ok() || response.status() === 304) liveTileStats.successful += 1;
    });
  }

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  const map = page.locator('#wizard-map');
  await expect(map).toBeVisible();
  await expect.poll(() => map.locator('img.leaflet-tile-loaded').count()).toBeGreaterThanOrEqual(4);

  if (basemapMode === 'offline-natural-earth') {
    await expect.poll(() => offlineTileStats.requests).toBeGreaterThanOrEqual(4);
    await expect.poll(() => offlineTileStats.paths).toBeGreaterThan(0);
    await expect.poll(() => offlineTileStats.labels).toBeGreaterThan(0);
    await map.evaluate((element) => {
      element.dataset.basemapReady = 'offline-natural-earth';
      element.setAttribute(
        'aria-label',
        'Offline Natural Earth quality-assurance basemap for selecting the WRF simulation domain',
      );
      const attribution = document.querySelector('.simulation-map-attribution');
      if (attribution) {
        attribution.textContent =
          'Offline QA basemap rendered from Natural Earth public-domain geography. It is not the User Guide OpenStreetMap capture.';
      }
    });
    await expect(map).toHaveAttribute('data-basemap-ready', 'offline-natural-earth');
    return {
      basemap: 'natural-earth-offline-qa',
      tileStats: offlineTileStats,
      attribution: 'Natural Earth public-domain geography',
      note: 'Deterministic CI quality-assurance rendering; not committed as the OpenStreetMap documentation image.',
    };
  }

  await expect.poll(() => liveTileStats.successful, { timeout: 30_000 }).toBeGreaterThanOrEqual(4);
  expect([...liveTileStats.hosts]).toEqual([OPENSTREETMAP_HOST]);
  const tileSources = await map.locator('img.leaflet-tile-loaded').evaluateAll((images) =>
    images.map((image) => image.currentSrc || image.src),
  );
  expect(tileSources.length).toBeGreaterThanOrEqual(4);
  for (const source of tileSources) {
    expect(new URL(source).hostname).toBe(OPENSTREETMAP_HOST);
  }
  const attribution = page.locator('.simulation-map-attribution').first();
  await expect(attribution).toContainText('OpenStreetMap');
  const visibleAttribution = ((await attribution.textContent()) || '').trim();
  expect(visibleAttribution).toContain('OpenStreetMap contributors');
  await map.evaluate((element) => {
    element.dataset.basemapReady = 'openstreetmap';
    element.setAttribute(
      'aria-label',
      'Interactive OpenStreetMap basemap for selecting the WRF simulation domain',
    );
  });
  await expect(map).toHaveAttribute('data-basemap-ready', 'openstreetmap');
  return {
    basemap: 'openstreetmap-standard',
    tileStats: liveTileStats,
    tileUrlTemplate: OPENSTREETMAP_TEMPLATE,
    tileHost: OPENSTREETMAP_HOST,
    attribution: visibleAttribution,
    note: 'Human-requested documentation capture of the fixed visible viewport; no panning, zoom sweep or tile prefetch.',
  };
}

test('capture the Xaver user-guide UI flow', async ({ page }) => {
  await gotoDocumentationWorkbench(page);
  await expect(page.getByRole('heading', { name: 'System readiness' })).toBeVisible();
  await expect(page.locator('.readiness-check').filter({ hasText: 'python' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Check again' })).toBeEnabled();
  await expect(page.getByText('Event to simulation')).toBeVisible();
  await expect(page.getByText('API online')).toBeVisible();
  await expect(page.getByRole('button', { name: /Select xaver/i })).toBeVisible();
  await capture(page, 'xaver-01-search.png');

  await page.getByRole('button', { name: /Select xaver/i }).click();
  await expect(page.locator('#event-detail')).toContainText('Xaver');
  await expect(page.locator('#domain-select')).toBeEnabled();
  await expect(page.locator('#resolution-select')).toBeEnabled();
  await capture(page, 'xaver-02-event-selected.png');

  await page.locator('#domain-select').selectOption('northern-germany-27km');
  await page.locator('#resolution-select').selectOption('quick-preview');
  await expect(page.locator('#domain-label')).toContainText('northern-germany-27km');
  await capture(page, 'xaver-03-domain-resolution.png');

  await page.getByRole('button', { name: 'Preview job config' }).click();
  await expect(page.locator('#config-preview')).toContainText('"id"');
  await expect(page.locator('#config-preview')).toContainText('xaver-ui-dry-run');
  await expect(page.getByText('Preview is valid and ready to run.')).toBeVisible();
  await capture(page, 'xaver-04-preview-config.png');

  await page.getByRole('button', { name: 'Start dry-run' }).click();
  await expect(page.locator('#job-status')).toContainText('succeeded', { timeout: 30_000 });
  await expect(page.getByText('Dry-run finished. Status and logs are available below.')).toBeVisible();
  await capture(page, 'xaver-05-dry-run-status.png');

  await expect(page.locator('#job-logs')).toContainText('Dry run complete');
  await capture(page, 'xaver-06-logs.png');
});

test('plan a map-selected Xaver domain without editing JSON', async ({ page }) => {
  const basemap = await gotoDocumentationWorkbench(page);

  await expect(page.getByRole('heading', { name: 'Draw a real map area and estimate the WRF job' })).toBeVisible();
  const map = page.locator('#wizard-map');
  await expect(map).toBeVisible();
  await expect(page.locator('#wizard-west')).toHaveValue('2');
  await expect(page.locator('#wizard-north')).toHaveValue('58');

  await page.getByRole('button', { name: 'Plan domain and preview job' }).click();
  await expect(page.getByText('The map domain is valid and a job configuration was generated.')).toBeVisible();
  await expect(page.locator('#wizard-result')).toContainText('91 × 91');
  await expect(page.locator('#wizard-result')).toContainText('Recommended RAM');
  await expect(page.locator('#wizard-config-preview')).toContainText('"domain_source": "map-bounds"');
  await expect(page.locator('#wizard-config-preview')).toContainText('"quality_profile": "balanced"');
  const screenshotName = 'xaver-03b-map-domain-wizard.png';
  await captureElement(page.locator('.simulation-wizard'), screenshotName);
  writeMapProvenance(screenshotName, {
    basemap: basemap.basemap,
    tileUrlTemplate: basemap.tileUrlTemplate,
    tileHost: basemap.tileHost,
    successfulTileResponses: basemapMode === 'openstreetmap'
      ? basemap.tileStats.successful
      : basemap.tileStats.requests,
    attribution: basemap.attribution,
    note: basemap.note,
  });

  await page.getByRole('button', { name: 'Start planned dry-run' }).click();
  await expect(page.locator('#wizard-status')).toContainText('finished successfully', { timeout: 30_000 });
});

test('draw a new simulation rectangle directly on the map', async ({ page }) => {
  await gotoDocumentationWorkbench(page);
  const map = page.locator('#wizard-map');

  await page.getByRole('button', { name: 'Draw simulation area' }).click();
  await expect(page.getByText(/Drag from one corner/)).toBeVisible();

  await map.evaluate((element) => {
    // Synthetic PointerEvents do not represent an active hardware pointer, so
    // browsers reject setPointerCapture(). Stub capture only inside this test;
    // production input continues to use the native implementation.
    element.setPointerCapture = () => {};
    element.releasePointerCapture = () => {};
    element.hasPointerCapture = () => false;

    const rectangle = element.getBoundingClientRect();
    const pointerId = 41;
    const dispatch = (type, xRatio, yRatio, buttons) => {
      element.dispatchEvent(new PointerEvent(type, {
        bubbles: true,
        cancelable: true,
        composed: true,
        pointerId,
        pointerType: 'mouse',
        isPrimary: true,
        button: type === 'pointerdown' ? 0 : -1,
        buttons,
        clientX: rectangle.left + rectangle.width * xRatio,
        clientY: rectangle.top + rectangle.height * yRatio,
      }));
    };
    dispatch('pointerdown', 0.25, 0.25, 1);
    dispatch('pointermove', 0.50, 0.50, 1);
    dispatch('pointermove', 0.75, 0.75, 1);
    dispatch('pointerup', 0.75, 0.75, 0);
  });

  await expect(page.getByRole('button', { name: 'Draw simulation area' })).toBeVisible();
  await expect(page.locator('#wizard-west')).not.toHaveValue('2');
  await expect(page.locator('#wizard-east')).not.toHaveValue('14');
  await page.getByRole('button', { name: 'Plan domain and preview job' }).click();
  await expect(page.getByText('The map domain is valid and a job configuration was generated.')).toBeVisible();
});
