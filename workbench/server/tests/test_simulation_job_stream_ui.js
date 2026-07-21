#!/usr/bin/env node

const assert = require('node:assert/strict');

const sources = [];
class FakeEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;

  constructor(url) {
    this.url = url;
    this.listeners = new Map();
    this.closed = false;
    this.readyState = FakeEventSource.CONNECTING;
    sources.push(this);
  }

  addEventListener(name, listener) {
    this.listeners.set(name, listener);
  }

  emit(name, payload = {}) {
    this.listeners.get(name)?.(payload);
  }

  open() {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.();
  }

  close() {
    this.closed = true;
    this.readyState = FakeEventSource.CLOSED;
  }
}

global.window = global;
global.document = {
  readyState: 'loading',
  addEventListener() {},
  querySelector() { return null; },
};

const {
  SimulationJobStreamController,
} = require('../../web/simulation-job-stream.js');

(async () => {
  const intervals = [];
  let detailRefreshes = 0;
  let fullRefreshes = 0;
  let renders = 0;
  let pollingFallbacks = 0;
  const element = {
    selectedJob: { id: 'sim-a', status: 'READY', events: [] },
    pollTimer: 77,
    scheduleRefresh() { pollingFallbacks += 1; },
    async refreshSelectedJob() { detailRefreshes += 1; },
    async refresh() { fullRefreshes += 1; },
    render() { renders += 1; },
    showError(error) { throw error; },
  };
  const controller = new SimulationJobStreamController(element, {
    EventSource: FakeEventSource,
    setInterval(callback) { intervals.push(callback); return intervals.length; },
    clearInterval() {},
    intervalMs: 5,
  });

  controller.start();
  assert.equal(sources.length, 0, 'READY jobs must not open a stream');

  element.selectedJob = {
    id: 'sim-a',
    status: 'QUEUED',
    events: [{ sequence: 3 }, { sequence: 5 }],
  };
  controller.sync();
  assert.equal(sources.length, 1);
  assert.equal(sources[0].url, '/api/simulations/sim-a/events/stream?after=5');
  assert.equal(controller.streaming, false, 'polling remains active before SSE opens');
  sources[0].open();
  assert.equal(controller.streaming, true);
  assert.equal(element.pollTimer, null);

  sources[0].emit('simulation-event');
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(detailRefreshes, 1);
  assert.equal(renders, 1);

  element.selectedJob = {
    id: 'sim-b',
    status: 'SIMULATING',
    events: [{ sequence: 11 }],
  };
  controller.sync();
  assert.equal(sources[0].closed, true);
  assert.equal(sources.length, 2);
  assert.equal(sources[1].url, '/api/simulations/sim-b/events/stream?after=11');
  sources[1].open();

  sources[1].emit('simulation-complete');
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(sources[1].closed, true);
  assert.equal(fullRefreshes, 1);

  element.selectedJob = { id: 'sim-c', status: 'QUEUED', events: [] };
  controller.sync();
  assert.equal(sources.length, 3);
  assert.equal(sources[2].url, '/api/simulations/sim-c/events/stream');
  sources[2].onerror(new Error('initial connection unavailable'));
  assert.equal(sources[2].closed, false);
  assert.equal(controller.streaming, false);
  assert.equal(pollingFallbacks, 1, 'initial SSE failure keeps polling active');

  sources[2].open();
  assert.equal(controller.streaming, true);
  sources[2].readyState = FakeEventSource.CONNECTING;
  sources[2].onerror(new Error('temporary reconnect'));
  assert.equal(sources[2].closed, false);
  assert.equal(pollingFallbacks, 1, 'native reconnect does not duplicate fallback');

  sources[2].readyState = FakeEventSource.CLOSED;
  sources[2].onerror(new Error('stream permanently closed'));
  assert.equal(sources[2].closed, true);
  assert.equal(pollingFallbacks, 2);

  controller.stop();
  assert.equal(controller.streaming, false);
  console.log('Simulation SSE UI controller tests passed');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
