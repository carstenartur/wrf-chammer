#!/usr/bin/env node

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..', '..', '..');
const sourcePath = path.join(repoRoot, 'workbench', 'web', 'real-pipeline-specification.js');

class TestElement {
  attachShadow() {
    this.shadowRoot = {
      innerHTML: '',
      getElementById() {
        return null;
      },
    };
    return this.shadowRoot;
  }
}

const registry = new Map();
global.HTMLElement = TestElement;
global.customElements = {
  get(name) {
    return registry.get(name);
  },
  define(name, constructor) {
    registry.set(name, constructor);
  },
};

vm.runInThisContext(fs.readFileSync(sourcePath, 'utf8'), { filename: sourcePath });
const Control = registry.get('real-pipeline-specification');
assert.ok(Control, 'custom element was not registered');

const control = new Control();
control.cacheEntries = [{
  plan_key: 'a'.repeat(64),
  status: 'complete',
  coverage: { percent: 100 },
  period: { start: '2013-12-05T12:00:00Z', end: '2013-12-05T18:00:00Z' },
  provenance: {
    checksums_available: true,
    provenance_file_available: true,
    artificial_weather_data: false,
  },
}];
control.selectedPlanKey = 'a'.repeat(64);
control.selectedProfile = 'small-real-data-demo';
control.readiness = {
  ready: true,
  wizard_preview_available: true,
  source_revision: 'b'.repeat(40),
  profiles: {
    'small-real-data-demo': {
      id: 'small-real-data-demo',
      label: 'Small real-data demonstration',
      max_grid_points: 45000,
    },
  },
  runtime: {
    wps: { reference: 'wps:4.6', identity: `sha256:${'1'.repeat(64)}` },
    wrf: { reference: 'wrf:latest', identity: 'latest' },
    postprocessing: { reference: 'post:1', identity: `sha256:${'3'.repeat(64)}` },
  },
};

assert.equal(control.runtimeIdentitiesPinned, false);
control.render();
assert.match(control.shadowRoot.innerHTML, /not pinned/);
assert.match(
  control.shadowRoot.innerHTML,
  /id="pipeline-spec-freeze"[^>]*disabled/,
  'freeze button must stay disabled for a mutable runtime identity',
);

control.readiness.runtime.wrf.identity = `sha256:${'2'.repeat(64)}`;
assert.equal(control.runtimeIdentitiesPinned, true);
control.render();
const freezeButton = control.shadowRoot.innerHTML.match(/<button id="pipeline-spec-freeze"[^>]*>/)?.[0];
assert.ok(freezeButton, 'freeze button was not rendered');
assert.doesNotMatch(freezeButton, /disabled/);
assert.doesNotMatch(control.shadowRoot.innerHTML, />not pinned</);

console.log('Real pipeline specification UI identity checks passed');
