const fs = require('fs');
const path = require('path');
const { test, expect } = require('@playwright/test');

const repoRoot = path.resolve(__dirname, '../..');
const screenshotDir = path.join(repoRoot, 'doc', 'user-guide', 'screenshots');

test('queue, cancel, and retry the latest guided simulation', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });

  await expect(page.getByRole('heading', { name: 'Queue the validated simulation' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Queue and job history' })).toBeVisible();
  await expect(page.locator('#queue-latest-job')).toBeDisabled();

  await page.getByRole('button', { name: 'Plan domain and preview job' }).click();
  await expect(page.getByText('The map domain is valid and a job configuration was generated.')).toBeVisible();

  await expect(page.locator('#queue-latest-job')).toBeEnabled({ timeout: 10_000 });
  await page.locator('#queue-latest-job').click();
  await expect(page.locator('#job-launcher-message')).toContainText('entered state QUEUED');

  const message = await page.locator('#job-launcher-message').textContent();
  if (!message) throw new Error('Missing persistent queue confirmation message');
  const queuedId = message
    .replace(/^Job\s+/, '')
    .replace(/\s+entered state QUEUED\.$/, '');
  expect(queuedId).toMatch(/^[a-z0-9-]+$/);

  const item = page.locator('.job-list-item').filter({ hasText: queuedId });
  await expect(item).toBeVisible();
  await item.click();
  await expect(page.locator('#persistent-job-detail')).toContainText('QUEUED');
  await expect(page.locator('#persistent-job-cancel')).toBeEnabled();
  await expect(page.locator('#persistent-job-retry')).toBeDisabled();

  fs.mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({
    path: path.join(screenshotDir, 'xaver-03d-persistent-queue.png'),
    fullPage: true,
  });

  await page.locator('#persistent-job-cancel').click();
  await expect(page.locator('#job-queue-message')).toContainText('Cancellation state: CANCELLED.');
  await expect(page.locator('#persistent-job-detail')).toContainText('CANCELLED');
  await expect(page.locator('#persistent-job-retry')).toBeEnabled();

  await page.locator('#persistent-job-retry').click();
  await expect(page.locator('#job-queue-message')).toContainText('Attempt 2 is queued.');
  await expect(page.locator('#persistent-job-detail')).toContainText('Attempt');
  await expect(page.locator('#persistent-job-detail')).toContainText('2');
  await expect(page.locator('#persistent-job-detail')).toContainText('QUEUED');
});
