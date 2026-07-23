#!/usr/bin/env node

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..', '..', '..');
const sourcePath = path.join(repoRoot, 'workbench', 'web', 'simulation-job-queue.js');

class TestElement {
  attachShadow() {
    this.shadowRoot = {
      innerHTML: '',
      getElementById() { return null; },
      querySelectorAll() { return []; },
    };
    return this.shadowRoot;
  }
}

const registry = new Map();
global.HTMLElement = TestElement;
global.customElements = {
  get(name) { return registry.get(name); },
  define(name, constructor) { registry.set(name, constructor); },
};
global.setTimeout = () => 1;
global.clearTimeout = () => {};

const jobs = {
  'job-1': {
    id: 'job-1', status: 'READY', specification_key: 'a'.repeat(64), created_at: '2026-07-20T12:00:00Z',
    steps: [], input_datasets: [], runtime_snapshots: [], artifacts: [], resource_measurements: [],
    events: [{ type: 'job_created', timestamp: '2026-07-20T12:00:00Z', message: 'created first' }],
    cancellable: true, retryable: false,
  },
  'job-2': {
    id: 'job-2', status: 'CANCELLED', specification_key: 'b'.repeat(64), created_at: '2026-07-20T13:00:00Z',
    steps: [], input_datasets: [], runtime_snapshots: [], artifacts: [], resource_measurements: [],
    events: [{ type: 'job_cancelled', timestamp: '2026-07-20T13:10:00Z', message: 'cancelled second' }],
    cancellable: false, retryable: true,
  },
};
const calls = [];
global.fetch = async (requestPath) => {
  calls.push(requestPath);
  let payload;
  if (requestPath === '/api/pipeline/specifications') {
    payload = { specifications: [{ specification_key: 'a'.repeat(64), identity: { job: { name: 'Xaver' } } }] };
  } else if (requestPath === '/api/simulations') {
    payload = {
      simulations: Object.values(jobs).map(({ events, ...summary }) => summary),
    };
  } else if (requestPath === '/api/simulations/job-1') {
    payload = { simulation: jobs['job-1'] };
  } else if (requestPath === '/api/simulations/job-2') {
    payload = { simulation: jobs['job-2'] };
  } else {
    throw new Error(`Unexpected fetch: ${requestPath}`);
  }
  return {
    ok: true,
    status: 200,
    async json() { return payload; },
  };
};

vm.runInThisContext(fs.readFileSync(sourcePath, 'utf8'), { filename: sourcePath });
const Control = registry.get('simulation-job-queue');
assert.ok(Control, 'custom element was not registered');

(async () => {
  const control = new Control();
  await control.refresh();
  assert.equal(control.selectedJobId, 'job-1');
  assert.equal(control.selectedJobDetail.events[0].type, 'job_created');
  assert.ok(calls.includes('/api/simulations/job-1'));
  assert.match(control.shadowRoot.innerHTML, /created first/);
  assert.match(
    control.shadowRoot.innerHTML,
    /href="\/api\/simulations\/job-1\/run-manifest"/,
  );
  assert.match(
    control.shadowRoot.innerHTML,
    /download="wrf-chammer-run-manifest-job-1\.json"/,
  );

  await control.selectJob('job-2');
  assert.equal(control.selectedJobDetail.id, 'job-2');
  assert.equal(control.selectedJobDetail.events[0].type, 'job_cancelled');
  assert.ok(calls.includes('/api/simulations/job-2'));
  assert.match(control.shadowRoot.innerHTML, /cancelled second/);
  assert.match(
    control.shadowRoot.innerHTML,
    /href="\/api\/simulations\/job-2\/run-manifest"/,
  );
  console.log('Persistent simulation queue detail loading and manifest export passed');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
