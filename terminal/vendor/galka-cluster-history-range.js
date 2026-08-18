(() => {
  'use strict';

  const charts = window.LightweightCharts;
  if (!charts?.createChart) return;

  const originalCreateChart = charts.createChart;
  let chart = null;

  const INTERVAL_MS = {
    '1m': 60_000,
    '3m': 180_000,
    '5m': 300_000,
    '15m': 900_000,
    '30m': 1_800_000,
    '1h': 3_600_000,
    '2h': 7_200_000,
    '4h': 14_400_000,
    '8h': 28_800_000,
    '12h': 43_200_000,
    '1d': 86_400_000,
  };

  function visibleRange() {
    const rows = chart?.visibleWindow?.()?.rows || [];
    if (!rows.length) return null;
    const interval = String(document.getElementById('intervalSelect')?.value || '5m');
    const pad = (INTERVAL_MS[interval] || 300_000) * 2;
    const first = Number(rows[0]?.time || 0) * 1000;
    const last = Number(rows.at(-1)?.time || 0) * 1000;
    if (!(first > 0) || !(last >= first)) return null;
    return {
      fromMs: Math.max(1, Math.floor(first - pad)),
      toMs: Math.floor(last + pad),
    };
  }

  window.LightweightCharts = Object.freeze({
    ...charts,
    createChart(container, options) {
      const created = originalCreateChart(container, options);
      chart = created;
      return created;
    },
  });

  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    try {
      const raw = typeof input === 'string' ? input : String(input?.url || '');
      if (raw.includes('/api/live/clusters')) {
        const url = new URL(raw, window.location.origin);
        if (!url.searchParams.has('fromMs') && !url.searchParams.has('toMs')) {
          const range = visibleRange();
          if (range) {
            url.searchParams.set('fromMs', String(range.fromMs));
            url.searchParams.set('toMs', String(range.toMs));
            if (typeof input === 'string') {
              input = `${url.pathname}${url.search}`;
            } else if (input instanceof Request) {
              input = new Request(url.toString(), input);
            }
          }
        }
      }
    } catch (_) {
      // If chart/range inspection fails, the existing 12h default endpoint works.
    }
    return originalFetch(input, init);
  };

  window.GalkaClusterHistoryRange = Object.freeze({
    visibleRange,
  });
})();
