const fs = require('fs');
const path = require('path');
const { test, expect } = require('@playwright/test');
const { installDocumentationTileProvider } = require('./documentation-tile-provider');

const repoRoot = path.resolve(__dirname, '../..');
const screenshotDir = path.join(repoRoot, 'doc', 'user-guide', 'screenshots');

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

async function gotoDocumentationWorkbench(page) {
  const tileStats = await installDocumentationTileProvider(page);
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  const map = page.locator('#wizard-map');
  await expect(map).toBeVisible();
  await expect.poll(() => tileStats.requests).toBeGreaterThanOrEqual(4);
  await expect.poll(() => tileStats.paths).toBeGreaterThan(0);
  await expect.poll(() => tileStats.labels).toBeGreaterThan(0);
  await expect.poll(() => map.locator('img.leaflet-tile-loaded').count()).toBeGreaterThanOrEqual(4);

  await map.evaluate((element) => {
    element.dataset.basemapReady = 'natural-earth';
    element.setAttribute(
      'aria-label',
      'Interactive Natural Earth basemap for selecting the WRF simulation domain',
    );
    const attribution = document.querySelector('.simulation-map-attribution');
    if (attribution) {
      attribution.textContent =
        'Offline documentation basemap rendered from Natural Earth public-domain geography. Numeric coordinate fields remain available as a keyboard-accessible alternative.';
    }
  });
  await expect(map).toHaveAttribute('data-basemap-ready', 'natural-earth');
  await expect(page.getByText(/Natural Earth public-domain geography/)).toBeVisible();
  return tileStats;
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
  const tileStats = await gotoDocumentationWorkbench(page);

  await expect(page.getByRole('heading', { name: 'Draw a real map area and estimate the WRF job' })).toBeVisible();
  const map = page.locator('#wizard-map');
  await expect(map).toBeVisible();
  await expect.poll(() => tileStats.paths).toBeGreaterThan(0);
  await expect(page.locator('#wizard-west')).toHaveValue('2');
  await expect(page.locator('#wizard-north')).toHaveValue('58');

  await page.getByRole('button', { name: 'Plan domain and preview job' }).click();
  await expect(page.getByText('The map domain is valid and a job configuration was generated.')).toBeVisible();
  await expect(page.locator('#wizard-result')).toContainText('91 × 91');
  await expect(page.locator('#wizard-result')).toContainText('Recommended RAM');
  await expect(page.locator('#wizard-config-preview')).toContainText('"domain_source": "map-bounds"');
  await expect(page.locator('#wizard-config-preview')).toContainText('"quality_profile": "balanced"');
  await captureElement(page.locator('.simulation-wizard'), 'xaver-03b-map-domain-wizard.png');

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
