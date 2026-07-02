const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { execFileSync, spawn } = require('node:child_process');
const { test, expect } = require('@playwright/test');

const repoRoot = path.resolve(__dirname, '../..');
const outputDir = path.join(repoRoot, 'workbench-runs', 'user-guide-weather-map');
const screenshotDir = path.join(repoRoot, 'doc', 'user-guide', 'screenshots');
const port = 8091;

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

test('capture computed WRF weather map result', async ({ page }) => {
  fs.rmSync(outputDir, { recursive: true, force: true });
  fs.mkdirSync(screenshotDir, { recursive: true });

  execFileSync('sh', ['visualization/postprocess/run-demo.sh', outputDir], {
    cwd: repoRoot,
    stdio: 'inherit',
  });

  const server = spawn('sh', ['visualization/web/serve.sh', outputDir, String(port)], {
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

    const maxWindLayer = page.getByRole('button', { name: /Maximum 10 m wind speed/i });
    await maxWindLayer.click();
    await expect(maxWindLayer).toHaveClass(/active/);
    await expect(page.locator('#sb-layer')).toContainText('Maximum 10 m wind speed');

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
