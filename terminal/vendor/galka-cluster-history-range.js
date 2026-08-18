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

  // Keep enough cluster archive around the current chart to make panning feel
  // continuous instead of waiting for a new request every time the user drags.
  // 5m deliberately gets about five days because the candle endpoint below is
  // expanded to 1500 bars (1500 * 5m ~= 5.2 days).
  const HISTORY_LOOKBACK_MS = {
    '1m': 24 * 60 * 60 * 1000,
    '3m': 3 * 24 * 60 * 60 * 1000,
    '5m': 5 * 24 * 60 * 60 * 1000,
    '15m': 10 * 24 * 60 * 60 * 1000,
    '30m': 20 * 24 * 60 * 60 * 1000,
    '1h': 45 * 24 * 60 * 60 * 1000,
    '2h': 60 * 24 * 60 * 60 * 1000,
    '4h': 89 * 24 * 60 * 60 * 1000,
    '8h': 89 * 24 * 60 * 60 * 1000,
    '12h': 89 * 24 * 60 * 60 * 1000,
    '1d': 89 * 24 * 60 * 60 * 1000,
  };
  const MAX_CLUSTER_QUERY_MS = 89 * 24 * 60 * 60 * 1000;

  function visibleRange() {
    const rows = chart?.visibleWindow?.()?.rows || [];
    if (!rows.length) return null;
    const interval = String(document.getElementById('intervalSelect')?.value || '5m');
    const intervalMs = INTERVAL_MS[interval] || 300_000;
    const pad = intervalMs * 2;
    const first = Number(rows[0]?.time || 0) * 1000;
    const last = Number(rows.at(-1)?.time || 0) * 1000;
    if (!(first > 0) || !(last >= first)) return null;

    const now = Date.now();
    const lookback = HISTORY_LOOKBACK_MS[interval] || (5 * 24 * 60 * 60 * 1000);
    const desiredFrom = Math.min(first - pad, now - lookback);
    const desiredTo = Math.max(last + pad, now + pad);
    const fromMs = Math.max(1, desiredTo - MAX_CLUSTER_QUERY_MS, desiredFrom);
    return {
      fromMs: Math.floor(fromMs),
      toMs: Math.floor(desiredTo),
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
      } else if (raw.includes('/api/live/candles')) {
        // live.js asks for 600 bars on a full reload. The backend safely caps
        // candle snapshots at 1500, so expand only the full-history request and
        // leave the tiny periodic refresh requests unchanged.
        const url = new URL(raw, window.location.origin);
        const limit = Number(url.searchParams.get('limit') || 0);
        if (limit >= 600 && limit < 1500) {
          url.searchParams.set('limit', '1500');
          if (typeof input === 'string') {
            input = `${url.pathname}${url.search}`;
          } else if (input instanceof Request) {
            input = new Request(url.toString(), input);
          }
        }
      }
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

  window.GalkaClusterHistoryRange = Object.freeze({
    visibleRange,
    historyLookbackMs: HISTORY_LOOKBACK_MS,
  });
})();