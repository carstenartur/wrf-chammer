const fs = require('fs');
const path = require('path');
const { test, expect } = require('@playwright/test');

const repoRoot = path.resolve(__dirname, '../..');
const screenshotDir = path.join(repoRoot, 'doc', 'user-guide', 'screenshots');
const transparentPng = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64',
);

test.beforeEach(async ({ page }) => {
  await page.route(/^https:\/\/[abc]\.tile\.openstreetmap\.org\//, async (route) => {
    await route.fulfill({ status: 200, contentType: 'image/png', body: transparentPng });
  });
});

test('plan and prepare real ERA5 data from the guided map plan', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });

  await expect(page.getByRole('heading', { name: 'Plan ERA5 boundary data' })).toBeVisible();

  await page.getByRole('button', { name: 'Plan domain and preview job' }).click();
  await expect(page.getByText('The map domain is valid and a job configuration was generated.')).toBeVisible();

  await page.getByRole('button', { name: 'Refresh data status' }).click();
  await expect(page.locator('#era5-plan-data')).toBeEnabled();

  await page.locator('#era5-plan-data').click();
  await expect(page.locator('#era5-data-message')).toContainText('No download has been started.');
  await expect(page.locator('#era5-plan-result')).toContainText('ERA5 requests');
  await expect(page.locator('#era5-plan-result')).toContainText('Boundary time points');
  await expect(page.locator('#era5-plan-result')).toContainText('Copernicus Climate Data Store ERA5 reanalysis');
  await expect(page.locator('#era5-plan-result')).toContainText('Artificial weather data');
  await expect(page.locator('#era5-plan-result')).toContainText('no');

  fs.mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({
    path: path.join(screenshotDir, 'xaver-03c-era5-data-plan.png'),
    fullPage: true,
  });

  await page.locator('#era5-prepare-data').click();
  await expect(page.locator('#era5-data-message')).toContainText('No network download has been started.');
  await expect(page.locator('#era5-prepared-files')).toContainText('era5-plan.json');
  await expect(page.locator('#era5-prepared-files')).toContainText('era5-download-config.json');
  await expect(page.locator('#era5-prepared-files')).toContainText('Download started: no');
});
