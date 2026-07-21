const { defineConfig, devices } = require('@playwright/test');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '../..');

module.exports = defineConfig({
  testDir: __dirname,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  outputDir: path.join(repoRoot, 'workbench-runs', 'result-viewer-playwright'),
  reporter: [['list']],
  use: {
    viewport: { width: 1440, height: 1050 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    acceptDownloads: true,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
