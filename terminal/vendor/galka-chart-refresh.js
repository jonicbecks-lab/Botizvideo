(() => {
  'use strict';

  const charts = window.LightweightCharts;
  const button = document.getElementById('chartRefreshButton');
  if (!charts?.createChart || !button) return;

  // The main terminal already performs a slower candle poll. This lightweight
  // tail updater makes the selected chart feel live without touching trading
  // status/reconciliation or sharing the private trading I/O path.
  const AUTO_REFRESH_MS = 5000;
  const AUTO_LIMIT = 3;
  const MANUAL_LIMIT = 97;

  const originalCreateChart = charts.createChart;
  let chart = null;
  let series = null;
  let busy = false;
  let autoTimer = null;

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

  function currentSelection() {
    const symbol = document.getElementById('symbolSelect');
    const interval = document.getElementById('intervalSelect');
    return {
      symbol,
      interval,
      coin: String(symbol?.value || 'BTC').toUpperCase(),
      timeframe: String(interval?.value || '5m'),
    };
  }

  function existingRows() {
    return Array.isArray(series?.data) ? normalize(series.data) : [];
  }

  function latestLabel(unixSeconds) {
    if (!Number.isFinite(Number(unixSeconds))) return '';
    try {
      return new Date(Number(unixSeconds) * 1000).toLocaleTimeString('ru-RU', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
    } catch (_) {
      return '';
    }
  }

  async function fetchTail(coin, timeframe, limit) {
    // Different limits are intentional. The backend candle reader caches by
    // (coin, interval, limit) for only ~2 seconds. Manual refresh therefore uses
    // a separate cache key from the 5-second background tail and always performs
    // a real recent-candle read in normal use.
    const response = await fetch(
      `/api/live/candles?coin=${encodeURIComponent(coin)}` +
      `&interval=${encodeURIComponent(timeframe)}&limit=${limit}`,
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
    const rows = normalize(payload?.data);
    if (!rows.length) throw new Error('Hyperliquid не вернул последние свечи');
    return rows;
  }

  function applyTail(fresh) {
    if (!series || !fresh.length) return { appended: 0, touchedCurrent: false, latestTime: null };

    const existing = existingRows();
    const beforeLast = existing.at(-1)?.time ?? null;
    let appended = 0;
    let touchedCurrent = false;

    if (beforeLast == null) {
      series.setData?.(fresh);
      return {
        appended: Math.max(0, fresh.length - 1),
        touchedCurrent: true,
        latestTime: fresh.at(-1)?.time ?? null,
      };
    }

    // Use update(), not setData()+fitContent(). This is the key behavioural fix:
    // candle data changes while the user's horizontal/vertical chart framing is
    // left exactly where they placed it. The button no longer recenters/pans.
    for (const row of fresh) {
      if (row.time < beforeLast) continue;
      if (row.time === beforeLast) touchedCurrent = true;
      if (row.time > beforeLast) appended += 1;
      series.update?.(row);
    }

    return {
      appended,
      touchedCurrent,
      latestTime: fresh.at(-1)?.time ?? beforeLast,
    };
  }

  async function refresh({ manual = false } = {}) {
    if (busy || !chart || !series || (!manual && document.hidden)) return null;
    const { symbol, interval, coin, timeframe } = currentSelection();

    busy = true;
    let previousText = null;
    if (manual) {
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      previousText = button.textContent;
      button.textContent = '…';
    }

    try {
      const fresh = await fetchTail(coin, timeframe, manual ? MANUAL_LIMIT : AUTO_LIMIT);

      // Ignore stale responses after a symbol/timeframe switch.
      if (
        coin !== String(symbol?.value || '').toUpperCase() ||
        timeframe !== String(interval?.value || '')
      ) return null;

      const result = applyTail(fresh);
      document.dispatchEvent(new CustomEvent('galka:chart-refreshed', {
        detail: {
          coin,
          interval: timeframe,
          latestTime: result.latestTime,
          appended: result.appended,
          manual,
        },
      }));

      if (manual) {
        const stamp = latestLabel(result.latestTime);
        if (result.appended > 0) {
          showToast(`Свечи обновлены · +${result.appended}${stamp ? ` · ${stamp}` : ''}`);
        } else if (result.touchedCurrent) {
          showToast(`Текущая свеча обновлена${stamp ? ` · ${stamp}` : ''}`);
        } else {
          showToast(`Свечи уже актуальны${stamp ? ` · ${stamp}` : ''}`);
        }
      }
      return result;
    } catch (error) {
      if (manual) showToast(error?.message || 'Не удалось обновить график', 'error');
      return null;
    } finally {
      busy = false;
      if (manual) {
        button.disabled = false;
        button.removeAttribute('aria-busy');
        button.textContent = previousText || '↻';
      }
    }
  }

  function startAutoRefresh() {
    if (autoTimer) clearInterval(autoTimer);
    autoTimer = setInterval(() => refresh({ manual: false }), AUTO_REFRESH_MS);
  }

  button.addEventListener('click', () => refresh({ manual: true }));

  // Returning to the app should not make the user wait for the next timer tick.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) window.setTimeout(() => refresh({ manual: false }), 150);
  });
  window.addEventListener('pageshow', () => {
    window.setTimeout(() => refresh({ manual: false }), 700);
  });
  startAutoRefresh();
})();
