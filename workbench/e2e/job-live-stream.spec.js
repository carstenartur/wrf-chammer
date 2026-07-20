const { test, expect } = require('@playwright/test');

test('stream persistent job events and complete after cancellation', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: 'Latest job event stream' })).toBeVisible();

  await page.getByRole('button', { name: 'Plan domain and preview job' }).click();
  await expect(page.locator('#queue-latest-job')).toBeEnabled({ timeout: 10_000 });
  await page.locator('#queue-latest-job').click();
  await expect(page.locator('#job-launcher-message')).toContainText('entered state QUEUED');

  const message = await page.locator('#job-launcher-message').textContent();
  if (!message) throw new Error('Missing queue confirmation');
  const jobId = message.replace(/^Job\s+/, '').replace(/\s+entered state QUEUED\.$/, '');

  await expect(page.locator('#job-live-grid')).toContainText(jobId, { timeout: 10_000 });
  await expect(page.locator('#job-live-grid')).toContainText('job-created', { timeout: 10_000 });
  await expect(page.locator('#job-stream-connection')).toHaveText('live');

  const item = page.locator('.job-list-item').filter({ hasText: jobId });
  await item.click();
  await page.locator('#persistent-job-cancel').click();
  await expect(page.locator('#job-queue-message')).toContainText('Cancellation state: CANCELLED.');

  await expect(page.locator('#job-live-grid')).toContainText('cancel-requested', { timeout: 10_000 });
  await expect(page.locator('#job-live-grid')).toContainText('CANCELLED');
  await expect(page.locator('#job-stream-connection')).toHaveText('complete');
});
