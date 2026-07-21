#!/usr/bin/env node

const assert = require('node:assert/strict');

class FakeButton {
  constructor(jobId) {
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
  constructor() {
    this.button = new FakeButton('sim-aaaaaaaaaaaa-bbbbbbbbbbbb');
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
    return {
      ok: true,
      results: {
        viewer_url: '/jobs/sim-aaaaaaaaaaaa-bbbbbbbbbbbb/results/',
      },
    };
  },
});

const {
  enhanceSimulationQueue,
  resultButton,
} = require('../../web/simulation-result-entry.js');

(async () => {
  assert.equal(resultButton({ status: 'READY' }), '');
  assert.match(resultButton({
    id: 'sim-aaaaaaaaaaaa-bbbbbbbbbbbb',
    status: 'SUCCEEDED',
  }), /View results/);

  enhanceSimulationQueue(FakeQueue);
  const queue = new FakeQueue();
  assert.equal(queue.renderActions({ status: 'READY' }), '<button>Queue</button>');
  assert.match(queue.renderActions({
    id: 'sim-aaaaaaaaaaaa-bbbbbbbbbbbb',
    status: 'SUCCEEDED',
  }), /data-view-results/);
  queue.bindEvents();
  assert.equal(queue.originalBindings, true);
  await queue.button.click();
  assert.deepEqual(assigned, ['/jobs/sim-aaaaaaaaaaaa-bbbbbbbbbbbb/results/']);
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

  console.log('Simulation result entry UI tests passed');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
