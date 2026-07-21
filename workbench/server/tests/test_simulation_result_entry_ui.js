#!/usr/bin/env node

const assert = require('node:assert/strict');

const JOB_ID = 'sim-aaaaaaaaaaaa-bbbbbbbbbbbb';
const VIEWER_URL = `/jobs/${JOB_ID}/results/`;

class FakeButton {
  constructor(jobId = JOB_ID) {
    this.dataset = { jobId };
    this.disabled = false;
    this.listeners = new Map();
  }
  addEventListener(name, listener) {
    this.listeners.set(name, listener);
  }
  async click() {
    await this.listeners.get('click')?.({ currentTarget: this });
  }
}

class FakeQueue {
  constructor(jobId = JOB_ID) {
    this.button = new FakeButton(jobId);
    this.errors = [];
    this.shadowRoot = {
      querySelector: (selector) => selector === '[data-view-results]' ? this.button : null,
    };
  }
  renderActions(job) {
    return job?.status === 'READY' ? '<button>Queue</button>' : '';
  }
  bindEvents() {
    this.originalBindings = true;
  }
  showError(error) {
    this.errors.push(error.message);
  }
}

const assigned = [];
global.window = global;
global.location = { assign: (value) => assigned.push(value) };
global.fetch = async () => ({
  ok: true,
  async json() {
    return { ok: true, results: { viewer_url: VIEWER_URL } };
  },
});

const {
  enhanceSimulationQueue,
  resultButton,
} = require('../../web/simulation-result-entry.js');

(async () => {
  assert.equal(resultButton({ status: 'READY' }), '');
  assert.equal(resultButton({ status: 'SUCCEEDED' }), '');
  assert.equal(resultButton({ status: 'SUCCEEDED', id: '' }), '');
  assert.match(resultButton({ id: JOB_ID, status: 'SUCCEEDED' }), /View results/);

  enhanceSimulationQueue(FakeQueue);
  const queue = new FakeQueue();
  assert.equal(queue.renderActions({ status: 'READY' }), '<button>Queue</button>');
  assert.match(
    queue.renderActions({ id: JOB_ID, status: 'SUCCEEDED' }),
    /data-view-results/,
  );
  queue.bindEvents();
  assert.equal(queue.originalBindings, true);
  await queue.button.click();
  assert.deepEqual(assigned, [VIEWER_URL]);
  assert.equal(queue.errors.length, 0);

  global.fetch = async () => ({
    ok: false,
    async json() {
      return { error: { message: 'Result checksum changed.' } };
    },
  });
  const failing = new FakeQueue();
  failing.bindEvents();
  await failing.button.click();
  assert.deepEqual(failing.errors, ['Result checksum changed.']);
  assert.equal(failing.button.disabled, false);

  global.fetch = async () => ({
    ok: true,
    async json() {
      return { ok: true, results: { viewer_url: '/jobs/another/results/' } };
    },
  });
  const mismatched = new FakeQueue();
  mismatched.bindEvents();
  await mismatched.button.click();
  assert.deepEqual(
    mismatched.errors,
    ['The result viewer URL does not match the selected job.'],
  );
  assert.deepEqual(assigned, [VIEWER_URL]);

  console.log('Simulation result entry UI tests passed');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
