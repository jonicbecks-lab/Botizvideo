(() => {
  'use strict';
  const charts = window.LightweightCharts;
  if (!charts?.createChart) return;
  const originalCreateChart = charts.createChart.bind(charts);
  window.LightweightCharts = Object.freeze({
    ...charts,
    createChart(container, options) {
      const chart = originalCreateChart(container, options);
      if (container?.id === 'chart') window.GalkaMemChart = chart;
      return chart;
    },
  });
})();
