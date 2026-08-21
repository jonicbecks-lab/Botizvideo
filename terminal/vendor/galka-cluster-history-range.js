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
  const MAX_CLUSTER_QUERY_MS = 89 * 24 * 60 * 60 * 1000;

  function visibleRange() {
    const rows = chart?.visibleWindow?.()?.rows || [];
    if (!rows.length) return null;
    const interval = String(document.getElementById('intervalSelect')?.value || '5m');
    const intervalMs = INTERVAL_MS[interval] || 300_000;
    const first = Number(rows[0]?.time || 0) * 1000;
    const last = Number(rows.at(-1)?.time || 0) * 1000;
    if (!(first > 0) || !(last >= first)) return null;

    // Query only the visible window plus a modest buffer. The old implementation
    // forced every 2-second cluster poll to regroup up to five days of archive,
    // which could starve the phone/server while the user switched timeframe.
    const visibleSpan = Math.max(intervalMs * 12, last - first + intervalMs);
    const pad = Math.max(intervalMs * 8, Math.min(12 * 60 * 60 * 1000, visibleSpan * 0.35));
    const desiredFrom = Math.max(1, Math.floor(first - pad));
    const desiredTo = Math.floor(last + pad);
    const fromMs = Math.max(1, desiredTo - MAX_CLUSTER_QUERY_MS, desiredFrom);
    return { fromMs, toMs: desiredTo };
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
      // Candle requests are intentionally left unchanged. A previous patch
      // rewrote every full 600-bar request to 1500 bars; that made timeframe
      // switches and Android resume contend with LIVE exchange I/O for seconds.
    } catch (_) {
      // If chart/range inspection fails, the existing endpoint behavior remains.
    }
    return originalFetch(input, init);
  };

  // Android browsers can start native text selection while a finger is held on
  // an interactive chart. That conflicts with Galka's hold-to-crosshair gesture
  // and produces the Copy/Translate toolbar. Keep form fields selectable, but
  // suppress page selection everywhere else in the terminal.
  document.addEventListener('selectstart', (event) => {
    const target = event.target;
    if (
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target?.isContentEditable
    ) {
      return;
    }
    event.preventDefault();
  }, { capture: true });

  document.addEventListener('pointerdown', (event) => {
    if (event.pointerType !== 'touch') return;
    const target = event.target;
    if (
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target?.isContentEditable
    ) {
      return;
    }
    try {
      window.getSelection()?.removeAllRanges();
    } catch (_) {
      // Native selection cleanup is best-effort and must not affect the chart.
    }
  }, { capture: true });

  window.GalkaClusterHistoryRange = Object.freeze({ visibleRange });
})();