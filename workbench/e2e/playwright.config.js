const { defineConfig, devices } = require('@playwright/test');
const path = require('path');

const repoRoot = path.resolve(__dirname, '../..');

module.exports = defineConfig({
  testDir: __dirname,
  timeout: 60_000,
  expect: {
    timeout: 15_000,
  },
  outputDir: path.join(repoRoot, 'workbench-runs', 'playwright-results'),
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:8080',
    viewport: { width: 1440, height: 1050 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'python3 -m workbench.server.server --host 127.0.0.1 --port 8080',
    cwd: repoRoot,
    url: 'http://127.0.0.1:8080/api/health',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
