const path = require('node:path');
const { pathToFileURL } = require('node:url');

function resolveScreenshotDirectory(repoRoot, configuredOutput) {
  const relativeOutput = configuredOutput || 'doc/user-guide/screenshots';
  if (path.isAbsolute(relativeOutput)) {
    throw new Error('WRF_SCREENSHOT_OUTPUT_DIR must be relative to the repository root.');
  }
  const root = path.resolve(repoRoot);
  const resolved = path.resolve(root, relativeOutput);
  const rootUrl = pathToFileURL(`${root}${path.sep}`).href;
  const outputUrl = pathToFileURL(`${resolved}${path.sep}`).href;
  if (!outputUrl.startsWith(rootUrl) || resolved === root) {
    throw new Error('WRF_SCREENSHOT_OUTPUT_DIR must stay inside the repository root.');
  }
  return resolved;
}

module.exports = { resolveScreenshotDirectory };
