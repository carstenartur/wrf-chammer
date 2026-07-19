const fs = require('fs');
const path = require('path');
const { test, expect } = require('@playwright/test');

const repoRoot = path.resolve(__dirname, '../..');
const screenshotDir = path.join(repoRoot, 'doc', 'user-guide', 'screenshots');
const transparentPng = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64',
);

async function capture(page, fileName) {
  fs.mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({
    path: path.join(screenshotDir, fileName),
    fullPage: true,
  });
}

test.beforeEach(async ({ page }) => {
  await page.route(/^https:\/\/[abc]\.tile\.openstreetmap\.org\//, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: transparentPng,
    });
  });
});

test('capture the Xaver user-guide UI flow', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
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
  await page.goto('/', { waitUntil: 'domcontentloaded' });

  await expect(page.getByRole('heading', { name: 'Draw a real map area and estimate the WRF job' })).toBeVisible();
  await expect(page.locator('#wizard-map')).toBeVisible();
  await expect(page.locator('#wizard-west')).toHaveValue('2');
  await expect(page.locator('#wizard-north')).toHaveValue('58');

  await page.getByRole('button', { name: 'Plan domain and preview job' }).click();
  await expect(page.getByText('The map domain is valid and a job configuration was generated.')).toBeVisible();
  await expect(page.locator('#wizard-result')).toContainText('91 × 91');
  await expect(page.locator('#wizard-result')).toContainText('Recommended RAM');
  await expect(page.locator('#wizard-config-preview')).toContainText('"domain_source": "map-bounds"');
  await expect(page.locator('#wizard-config-preview')).toContainText('"quality_profile": "balanced"');
  await capture(page, 'xaver-03b-map-domain-wizard.png');

  await page.getByRole('button', { name: 'Start planned dry-run' }).click();
  await expect(page.locator('#wizard-status')).toContainText('finished successfully', { timeout: 30_000 });
});

test('draw a new simulation rectangle directly on the map', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  const map = page.locator('#wizard-map');
  await expect(map).toBeVisible();
  const box = await map.boundingBox();
  if (!box) throw new Error('Map bounding box is unavailable');

  await page.getByRole('button', { name: 'Draw simulation area' }).click();
  await expect(page.getByText(/Drag from one corner/)).toBeVisible();

  await page.mouse.move(box.x + box.width * 0.25, box.y + box.height * 0.25);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.75, box.y + box.height * 0.75, { steps: 8 });
  await page.mouse.up();

  await expect(page.getByRole('button', { name: 'Draw simulation area' })).toBeVisible();
  await expect(page.locator('#wizard-west')).not.toHaveValue('2');
  await expect(page.locator('#wizard-east')).not.toHaveValue('14');
  await page.getByRole('button', { name: 'Plan domain and preview job' }).click();
  await expect(page.getByText('The map domain is valid and a job configuration was generated.')).toBeVisible();
});
