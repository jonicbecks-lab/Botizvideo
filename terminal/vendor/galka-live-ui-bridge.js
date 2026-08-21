(() => {
  'use strict';

  const charts = window.LightweightCharts;
  if (!charts?.createChart) return;

  const originalCreateChart = charts.createChart;
  let chart = null;
  let series = null;

  const bridge = {
    clearPriceLines() {
      if (!series) return;
      const lines = Array.isArray(series.lines) ? [...series.lines] : [];
      if (lines.length && typeof series.removePriceLine === 'function') {
        for (const line of lines) {
          try {
            series.removePriceLine(line);
          } catch (_) {
            // UI cleanup is best effort; the next status render remains authoritative.
          }
        }
        return;
      }
      if (Array.isArray(series.lines)) {
        series.lines = [];
        chart?.draw?.();
      }
    },
  };

  window.GalkaLiveUiBridge = bridge;
  window.LightweightCharts = Object.freeze({
    ...charts,
    createChart(container, options) {
      const created = originalCreateChart(container, options);
      chart = created;
      const originalAddSeries = created.addSeries.bind(created);
      created.addSeries = (...args) => {
        const createdSeries = originalAddSeries(...args);
        series = createdSeries;
        return createdSeries;
      };
      return created;
    },
  });
})();
