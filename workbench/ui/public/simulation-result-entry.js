(function (global) {
  async function resultRequestJson(path) {
    const response = await global.fetch(path, { headers: { Accept: 'application/json' } });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload?.error?.message || `HTTP ${response.status}`);
    }
    return payload;
  }

  function resultButton(job) {
    if (!job || job.status !== 'SUCCEEDED') return '';
    const jobId = String(job.id || '');
    return `<button type="button" data-view-results data-job-id="${jobId.replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')}">View results</button>`;
  }

  function enhanceSimulationQueue(QueueConstructor) {
    if (!QueueConstructor || QueueConstructor.prototype.__resultViewerEnhanced) return;
    QueueConstructor.prototype.__resultViewerEnhanced = true;

    const originalRenderActions = QueueConstructor.prototype.renderActions;
    QueueConstructor.prototype.renderActions = function renderActionsWithResults(job) {
      const existing = typeof originalRenderActions === 'function'
        ? originalRenderActions.call(this, job)
        : '';
      return `${existing}${resultButton(job)}`;
    };

    const originalBindEvents = QueueConstructor.prototype.bindEvents;
    QueueConstructor.prototype.bindEvents = function bindResultEntry() {
      if (typeof originalBindEvents === 'function') originalBindEvents.call(this);
      this.shadowRoot?.querySelector('[data-view-results]')?.addEventListener('click', async (event) => {
        const jobId = event.currentTarget?.dataset?.jobId;
        if (!jobId) return;
        event.currentTarget.disabled = true;
        try {
          const payload = await resultRequestJson(
            `/api/simulations/${encodeURIComponent(jobId)}/results`,
          );
          const viewerUrl = payload?.results?.viewer_url;
          if (typeof viewerUrl !== 'string' || !viewerUrl.startsWith('/jobs/')) {
            throw new Error('The result viewer URL is invalid.');
          }
          global.location.assign(viewerUrl);
        } catch (error) {
          event.currentTarget.disabled = false;
          if (typeof this.showError === 'function') {
            this.showError(error);
          }
        }
      });
    };
  }

  async function boot() {
    if (!global.customElements) return;
    await global.customElements.whenDefined('simulation-job-queue');
    const QueueConstructor = global.customElements.get('simulation-job-queue');
    enhanceSimulationQueue(QueueConstructor);
    const element = global.document?.querySelector('simulation-job-queue');
    if (element && typeof element.render === 'function') element.render();
  }

  global.enhanceSimulationQueueResults = enhanceSimulationQueue;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { enhanceSimulationQueue, resultButton };
  } else {
    boot();
  }
})(typeof window !== 'undefined' ? window : globalThis);
