(() => {
  'use strict';

  const charts = window.LightweightCharts;
  if (!charts || typeof charts.createChart !== 'function') return;

  const originalCreateChart = charts.createChart.bind(charts);
  const MIN_WIDTH = 120;
  const MIN_HEIGHT = 180;
  const RETRY_DELAYS = [0, 60, 250, 1000];

  function installRecovery(chart, container) {
    if (!chart || chart.__galkaVisibilityRecoveryInstalled) return chart;
    chart.__galkaVisibilityRecoveryInstalled = true;

    const originalResize = typeof chart.resize === 'function' ? chart.resize.bind(chart) : null;
    if (!originalResize) return chart;

    let retryTimer = null;
    chart.resize = function resilientResize() {
      const rect = container.getBoundingClientRect();
      const width = Math.floor(rect.width);
      const height = Math.floor(rect.height);
      if (width < MIN_WIDTH || height < MIN_HEIGHT) {
        if (document.visibilityState === 'visible') {
          clearTimeout(retryTimer);
          retryTimer = setTimeout(() => chart.resize(), 120);
        }
        return;
      }
      originalResize();
    };

    const recover = () => {
      if (document.visibilityState !== 'visible') return;
      for (const delay of RETRY_DELAYS) {
        setTimeout(() => {
          if (document.visibilityState !== 'visible') return;
          requestAnimationFrame(() => chart.resize());
        }, delay);
      }
    };

    // This module is layout-only. Data refresh is owned by galka-resume-refresh;
    // firing interval.change here used to start an additional full candle load on
    // every Android resume/focus and could leave the screen black for seconds.
    window.addEventListener('pageshow', recover, { passive: true });
    window.addEventListener('focus', recover, { passive: true });
    window.addEventListener('resize', recover, { passive: true });
    window.addEventListener('orientationchange', recover, { passive: true });
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') recover();
    });
    window.visualViewport?.addEventListener('resize', recover, { passive: true });

    new ResizeObserver(recover).observe(container);
    recover();
    return chart;
  }

  window.LightweightCharts = Object.freeze({
    ...charts,
    createChart(container, options) {
      return installRecovery(originalCreateChart(container, options), container);
    },
  });
})();