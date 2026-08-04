(() => {
  'use strict';

  const charts = window.LightweightCharts;
  if (!charts || typeof charts.createChart !== 'function') return;

  const originalCreateChart = charts.createChart.bind(charts);
  const MIN_WIDTH = 120;
  const MIN_HEIGHT = 180;
  const RETRY_DELAYS = [0, 60, 250, 1000];
  const MARKET_REFRESH_AFTER_MS = 20_000;

  let hiddenAt = document.visibilityState === 'hidden' ? Date.now() : 0;
  let lastMarketRefreshAt = 0;

  function refreshMarketData(reason, force = false) {
    if (document.visibilityState !== 'visible') return;
    const now = Date.now();
    if (!force && now - lastMarketRefreshAt < 5_000) return;

    const interval = document.getElementById('intervalSelect');
    if (!interval || interval.disabled) return;

    lastMarketRefreshAt = now;
    interval.dispatchEvent(new Event('change', { bubbles: true }));
    window.dispatchEvent(new CustomEvent('galka:market-recovery', {
      detail: { reason, at: now },
    }));
  }

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

    const recover = (reason = 'layout', forceMarketRefresh = false) => {
      if (document.visibilityState !== 'visible') return;
      for (const delay of RETRY_DELAYS) {
        setTimeout(() => {
          if (document.visibilityState !== 'visible') return;
          requestAnimationFrame(() => chart.resize());
        }, delay);
      }
      if (forceMarketRefresh) {
        setTimeout(() => refreshMarketData(reason, true), 80);
      }
    };

    window.addEventListener('pageshow', (event) => {
      recover('pageshow', Boolean(event.persisted));
    }, { passive: true });
    window.addEventListener('focus', () => {
      const sleptLongEnough = hiddenAt > 0 && Date.now() - hiddenAt >= MARKET_REFRESH_AFTER_MS;
      recover('focus', sleptLongEnough);
      hiddenAt = 0;
    }, { passive: true });
    window.addEventListener('resize', () => recover('resize'), { passive: true });
    window.addEventListener('orientationchange', () => recover('orientationchange'), { passive: true });
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') {
        hiddenAt = Date.now();
        return;
      }
      const sleptLongEnough = hiddenAt > 0 && Date.now() - hiddenAt >= MARKET_REFRESH_AFTER_MS;
      recover('visibilitychange', sleptLongEnough);
      hiddenAt = 0;
    });
    window.visualViewport?.addEventListener('resize', () => recover('viewport-resize'), { passive: true });

    new ResizeObserver(() => recover('container-resize')).observe(container);
    recover('startup');
    return chart;
  }

  window.LightweightCharts = Object.freeze({
    ...charts,
    createChart(container, options) {
      return installRecovery(originalCreateChart(container, options), container);
    },
  });
})();
