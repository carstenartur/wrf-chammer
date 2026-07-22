const fs = require('node:fs');
const path = require('node:path');
const { GenericContainer } = require('testcontainers');

const repoRoot = path.resolve(__dirname, '../..');
const configuredOutput = process.env.WRF_SCREENSHOT_OUTPUT_DIR || 'doc/user-guide/screenshots';
const screenshotDir = path.resolve(repoRoot, configuredOutput);
const expectedScreenshots = [
  'xaver-01-search.png',
  'xaver-02-event-selected.png',
  'xaver-03-domain-resolution.png',
  'xaver-03b-map-domain-wizard.png',
  'xaver-03c-era5-data-plan.png',
  'xaver-04-preview-config.png',
  'xaver-05-dry-run-status.png',
  'xaver-06-logs.png',
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
      WRF_SCREENSHOT_BASEMAP: process.env.WRF_SCREENSHOT_BASEMAP || 'offline-natural-earth',
      WRF_SCREENSHOT_OUTPUT_DIR: configuredOutput,
      WRF_ALLOW_LIVE_OSM_SCREENSHOTS: process.env.WRF_ALLOW_LIVE_OSM_SCREENSHOTS || '',
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
  const provenance = path.join(screenshotDir, 'xaver-03b-map-domain-wizard.png.provenance.json');
  if (!fs.existsSync(provenance)) {
    throw new Error(`Missing map screenshot provenance: ${provenance}`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
