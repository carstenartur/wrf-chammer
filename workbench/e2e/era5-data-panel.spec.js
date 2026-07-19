const { test, expect } = require('@playwright/test');

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

  await page.locator('#era5-prepare-data').click();
  await expect(page.locator('#era5-data-message')).toContainText('No network download has been started.');
  await expect(page.locator('#era5-prepared-files')).toContainText('era5-plan.json');
  await expect(page.locator('#era5-prepared-files')).toContainText('era5-download-config.json');
  await expect(page.locator('#era5-prepared-files')).toContainText('Download started: no');
});
