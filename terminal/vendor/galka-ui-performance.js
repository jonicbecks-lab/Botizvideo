(() => {
  'use strict';

  const charts = window.LightweightCharts;
  if (!charts?.createChart) return;

  const originalCreateChart = charts.createChart;
  const RECENT_BARS = {
    '1m': 180,
    '3m': 160,
    '5m': 144,
    '15m': 120,
    '30m': 110,
    '1h': 100,
    '2h': 96,
    '4h': 90,
    '8h': 90,
    '12h': 90,
    '1d': 90,
  };

  function preferredBars() {
    const interval = String(document.getElementById('intervalSelect')?.value || '5m');
    return RECENT_BARS[interval] || 144;
  }

  function patchSeries(chart, series) {
    // Galka uses its own canvas series. Creating/removing each GALKA/L1..L8 line
    // used to redraw the entire chart immediately, producing 9-18 full draws for
    // one status update. Mutate the line array and coalesce all those changes into
    // one animation-frame draw. Fall back untouched if another chart backend is used.
    if (!series || !Array.isArray(series.lines)) return series;

    let drawQueued = false;
    const scheduleDraw = () => {
      if (drawQueued) return;
      drawQueued = true;
      requestAnimationFrame(() => {
        drawQueued = false;
        chart.draw?.();
      });
    };

    series.createPriceLine = (options) => {
      const line = {
        id: `line-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        ...options,
      };
      series.lines.push(line);
      scheduleDraw();
      return line;
    };

    series.removePriceLine = (line) => {
      const next = series.lines.filter((item) => item !== line);
      if (next.length === series.lines.length) return;
      series.lines = next;
      scheduleDraw();
    };

    return series;
  }

  window.LightweightCharts = Object.freeze({
    ...charts,
    createChart(container, options) {
      const chart = originalCreateChart(container, options);

      const originalAddSeries = chart.addSeries?.bind(chart);
      if (originalAddSeries) {
        chart.addSeries = (...args) => patchSeries(chart, originalAddSeries(...args));
      }

      // A full candle reload must reopen on the recent market, not on the whole
      // multi-day dataset. Older candles remain loaded and are still reachable by
      // pan/zoom. This also prevents old ETH prices from compressing current 5m
      // candles into a tiny strip after an Android restore/timeframe switch.
      if ('visibleCount' in chart && typeof chart.draw === 'function') {
        chart.fitContent = () => {
          const dataLength = Number(chart.series?.data?.length || 0);
          const bars = Math.max(20, Math.min(preferredBars(), dataLength || preferredBars()));
          chart.visibleCount = bars;
          chart.panOffset = 0;
          chart.resetPriceScale?.();
          chart.clampPanOffset?.();
          chart.draw();
        };
      }

      return chart;
    },
  });
})();
