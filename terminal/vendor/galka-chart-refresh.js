(() => {
  'use strict';

  const charts = window.LightweightCharts;
  const button = document.getElementById('chartRefreshButton');
  if (!charts?.createChart || !button) return;

  const originalCreateChart = charts.createChart;
  let chart = null;
  let series = null;
  let busy = false;

  window.LightweightCharts = Object.freeze({
    ...charts,
    createChart(container, options) {
      const created = originalCreateChart(container, options);
      chart = created;
      const originalAddSeries = created.addSeries?.bind(created);
      if (originalAddSeries) {
        created.addSeries = (...args) => {
          const createdSeries = originalAddSeries(...args);
          if (!series) series = createdSeries;
          return createdSeries;
        };
      }
      return created;
    },
  });

  function sessionHeaders() {
    const token = sessionStorage.getItem('galkaLiveSession') || '';
    return token ? { 'X-Galka-Session': token } : {};
  }

  function normalize(rows) {
    const unique = new Map();
    for (const source of rows || []) {
      const row = {
        time: Number(source?.time),
        open: Number(source?.open),
        high: Number(source?.high),
        low: Number(source?.low),
        close: Number(source?.close),
      };
      if (
        !Number.isFinite(row.time) ||
        !Number.isFinite(row.open) ||
        !Number.isFinite(row.high) ||
        !Number.isFinite(row.low) ||
        !Number.isFinite(row.close)
      ) continue;
      unique.set(row.time, row);
    }
    return [...unique.values()].sort((a, b) => a.time - b.time);
  }

  function showToast(message, type = 'ok') {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.className = `toast ${type}`;
    window.setTimeout(() => toast.classList.add('hidden'), 2500);
  }

  async function refreshToLatest() {
    if (busy || !chart || !series) return;
    const symbol = document.getElementById('symbolSelect');
    const interval = document.getElementById('intervalSelect');
    const coin = String(symbol?.value || 'BTC').toUpperCase();
    const timeframe = String(interval?.value || '5m');

    busy = true;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    const previousText = button.textContent;
    button.textContent = '…';

    try {
      // Only fetch a small recent tail. This is deliberately much lighter than a
      // timeframe switch/full 600-candle reload, while still repairing a stale
      // chart after the app has been in the background for a while.
      const response = await fetch(
        `/api/live/candles?coin=${encodeURIComponent(coin)}` +
        `&interval=${encodeURIComponent(timeframe)}&limit=80`,
        {
          headers: sessionHeaders(),
          credentials: 'same-origin',
          cache: 'no-store',
        },
      );
      const payload = await response.json();
      if (!response.ok || payload?.ok === false) {
        throw new Error(payload?.error || `HTTP ${response.status}`);
      }

      // Ignore a response if the user changed symbol/timeframe while it was loading.
      if (
        coin !== String(symbol?.value || '').toUpperCase() ||
        timeframe !== String(interval?.value || '')
      ) return;

      const fresh = normalize(payload?.data);
      if (!fresh.length) throw new Error('Hyperliquid не вернул последние свечи');

      const existing = Array.isArray(series.data) ? normalize(series.data) : [];
      const merged = new Map(existing.map((row) => [row.time, row]));
      for (const row of fresh) merged.set(row.time, row);
      const rows = [...merged.values()].sort((a, b) => a.time - b.time).slice(-600);

      series.setData?.(rows);
      // galka-ui-performance overrides fitContent so this means "latest useful
      // window", not "squeeze all 600 candles onto the screen".
      if (typeof chart.fitContent === 'function') chart.fitContent();
      else {
        if ('panOffset' in chart) chart.panOffset = 0;
        chart.resetPriceScale?.();
        chart.clampPanOffset?.();
        chart.draw?.();
      }

      document.dispatchEvent(new CustomEvent('galka:chart-refreshed', {
        detail: { coin, interval: timeframe, latestTime: fresh.at(-1)?.time || null },
      }));
      showToast('График обновлён до последних свечей');
    } catch (error) {
      showToast(error?.message || 'Не удалось обновить график', 'error');
    } finally {
      busy = false;
      button.disabled = false;
      button.removeAttribute('aria-busy');
      button.textContent = previousText || '↻';
    }
  }

  button.addEventListener('click', refreshToLatest);
})();
