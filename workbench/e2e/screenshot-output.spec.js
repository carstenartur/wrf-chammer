const path = require('node:path');
const { test, expect } = require('@playwright/test');
const { resolveScreenshotDirectory } = require('./screenshot-output');

const repoRoot = path.resolve(__dirname, '../..');

test('accept a screenshot output directory below the repository root', () => {
  expect(
    resolveScreenshotDirectory(repoRoot, 'workbench-runs/screenshot-output-test'),
  ).toBe(path.join(repoRoot, 'workbench-runs', 'screenshot-output-test'));
});

test('reject absolute, repository-root and escaping screenshot outputs', () => {
  const invalid = [
    repoRoot,
    '.',
    path.parse(repoRoot).root,
    path.join('workbench-runs', '..', '..', 'outside'),
  ];
  for (const value of invalid) {
    expect(() => resolveScreenshotDirectory(repoRoot, value)).toThrow(
      /must (?:be relative to|stay inside) the repository root|must not be the repository root/,
    );
  }
});
