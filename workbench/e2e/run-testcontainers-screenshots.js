const fs = require('node:fs');
const path = require('node:path');
const { GenericContainer } = require('testcontainers');

const repoRoot = path.resolve(__dirname, '../..');
const screenshotDir = path.join(repoRoot, 'doc', 'user-guide', 'screenshots');
const expectedScreenshots = [
  'xaver-01-search.png',
  'xaver-02-event-selected.png',
  'xaver-03-domain-resolution.png',
  'xaver-04-preview-config.png',
  'xaver-05-dry-run-status.png',
  'xaver-06-logs.png',
  'xaver-07-weather-map.png',
];

async function main() {
  const image = await GenericContainer
    .fromDockerfile(repoRoot, 'workbench/e2e/testcontainers/Dockerfile')
    .build('wrf-workbench-screenshot-runner:local');

  const container = await image
    .withBindMounts([{ source: repoRoot, target: '/workspace', mode: 'rw' }])
    .withWorkingDir('/workspace')
    .withEnvironment({
      CI: 'true',
      WORKBENCH_SKIP_PLAYWRIGHT_INSTALL: '1',
    })
    .withCommand(['sh', '-lc', 'sleep infinity'])
    .start();

  try {
    const result = await container.exec(['sh', '-lc', 'sh ci/generate-user-guide-screenshots.sh']);
    if (result.output) {
      process.stdout.write(result.output);
    }
    if (result.exitCode !== 0) {
      throw new Error(`Screenshot command failed with exit code ${result.exitCode}`);
    }
  } finally {
    await container.stop({ timeout: 10 });
  }

  const missing = expectedScreenshots.filter((fileName) => !fs.existsSync(path.join(screenshotDir, fileName)));
  if (missing.length > 0) {
    throw new Error(`Missing generated screenshots: ${missing.join(', ')}`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
