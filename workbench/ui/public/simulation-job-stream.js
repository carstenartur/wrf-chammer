(function (global) {
  const STREAMABLE = new Set([
    'QUEUED',
    'PREPROCESSING',
    'INITIALIZING',
    'SIMULATING',
    'POSTPROCESSING',
    'CANCELLING',
  ]);

  class SimulationJobStreamController {
    constructor(element, options = {}) {
      this.element = element;
      this.EventSource = options.EventSource || global.EventSource;
      this.setInterval = options.setInterval || global.setInterval.bind(global);
      this.clearInterval = options.clearInterval || global.clearInterval.bind(global);
      this.intervalMs = options.intervalMs || 500;
      this.timer = null;
      this.source = null;
      this.jobId = '';
      this.streaming = false;
      this.opened = false;
      this.fallbackScheduled = false;
      this.refreshing = false;
      this.originalScheduleRefresh = typeof element.scheduleRefresh === 'function'
        ? element.scheduleRefresh.bind(element)
        : null;
      if (this.originalScheduleRefresh) {
        element.scheduleRefresh = () => {
          if (this.streaming) {
            if (element.pollTimer) global.clearTimeout(element.pollTimer);
            element.pollTimer = null;
            return;
          }
          this.originalScheduleRefresh();
        };
      }
    }

    start() {
      if (this.timer || typeof this.EventSource !== 'function') return;
      this.sync();
      this.timer = this.setInterval(() => this.sync(), this.intervalMs);
    }

    stop() {
      if (this.timer) this.clearInterval(this.timer);
      this.timer = null;
      this.closeSource();
      if (this.originalScheduleRefresh) {
        this.element.scheduleRefresh = this.originalScheduleRefresh;
      }
    }

    selectedJob() {
      return this.element.selectedJob || null;
    }

    shouldStream(job) {
      return Boolean(job?.id && STREAMABLE.has(job.status));
    }

    lastSequence(job) {
      const sequences = (job?.events || [])
        .map((event) => Number(event?.sequence))
        .filter((sequence) => Number.isInteger(sequence) && sequence > 0);
      return sequences.length ? Math.max(...sequences) : 0;
    }

    sync() {
      const job = this.selectedJob();
      if (!this.shouldStream(job)) {
        this.closeSource();
        return;
      }
      if (this.source && this.jobId === job.id) return;
      this.open(job.id, this.lastSequence(job));
    }

    open(jobId, afterSequence = 0) {
      this.closeSource();
      this.jobId = jobId;
      const cursor = Number.isInteger(afterSequence) && afterSequence > 0
        ? `?after=${afterSequence}`
        : '';
      const source = new this.EventSource(
        `/api/simulations/${encodeURIComponent(jobId)}/events/stream${cursor}`,
      );
      this.source = source;
      this.streaming = false;
      this.opened = false;
      this.fallbackScheduled = false;

      source.onopen = () => {
        if (this.source !== source) return;
        this.opened = true;
        this.streaming = true;
        this.fallbackScheduled = false;
        if (this.element.pollTimer) global.clearTimeout(this.element.pollTimer);
        this.element.pollTimer = null;
      };
      source.addEventListener('simulation-event', () => this.refreshDetail());
      source.addEventListener('simulation-complete', () => {
        this.closeSource();
        this.refreshAll();
      });
      source.onerror = () => {
        if (this.source !== source) return;
        const closedState = this.EventSource.CLOSED ?? 2;
        if (!this.opened) {
          if (!this.fallbackScheduled && this.originalScheduleRefresh) {
            this.fallbackScheduled = true;
            this.originalScheduleRefresh();
          }
          if (source.readyState === closedState) this.closeSource();
          return;
        }
        if (source.readyState !== closedState) return;
        this.closeSource();
        if (this.originalScheduleRefresh) this.originalScheduleRefresh();
      };
    }

    closeSource() {
      if (this.source) this.source.close();
      this.source = null;
      this.jobId = '';
      this.streaming = false;
      this.opened = false;
      this.fallbackScheduled = false;
    }

    async refreshDetail() {
      if (this.refreshing || typeof this.element.refreshSelectedJob !== 'function') return;
      this.refreshing = true;
      try {
        await this.element.refreshSelectedJob();
        if (typeof this.element.render === 'function') this.element.render();
      } catch (error) {
        if (typeof this.element.showError === 'function') this.element.showError(error);
      } finally {
        this.refreshing = false;
      }
    }

    async refreshAll() {
      if (typeof this.element.refresh !== 'function') return;
      try {
        await this.element.refresh({ quiet: true });
      } catch (error) {
        if (typeof this.element.showError === 'function') this.element.showError(error);
      }
    }
  }

  function attachSimulationJobStream(element, options) {
    if (!element || element.__simulationStreamController) {
      return element?.__simulationStreamController || null;
    }
    const controller = new SimulationJobStreamController(element, options);
    element.__simulationStreamController = controller;
    controller.start();
    return controller;
  }

  function boot() {
    const attach = () => {
      const element = document.querySelector('simulation-job-queue');
      if (element) attachSimulationJobStream(element);
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', attach, { once: true });
    } else {
      attach();
    }
  }

  global.SimulationJobStreamController = SimulationJobStreamController;
  global.attachSimulationJobStream = attachSimulationJobStream;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { SimulationJobStreamController, attachSimulationJobStream };
  } else {
    boot();
  }
})(typeof window !== 'undefined' ? window : globalThis);
