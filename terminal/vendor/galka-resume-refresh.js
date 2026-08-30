(() => {
  'use strict';

  const REFRESH_AFTER_MS = 3_000;
  const REFRESH_COOLDOWN_MS = 5_000;

  let hiddenAt = document.visibilityState === 'hidden' ? Date.now() : 0;
  let lastRefreshAt = 0;
  let recoveryTimer = null;

  function controlsState() {
    const symbol = document.getElementById('symbolSelect');
    const interval = document.getElementById('intervalSelect');
    const watermark = document.getElementById('watermark');
    if (!symbol || !interval || !watermark) return null;
    const expected = `${symbol.value} · ${interval.value} · HYPERLIQUID`;
    return { symbol, interval, watermark, matches: watermark.textContent === expected };
  }

  function requestRefresh(reason, force = false) {
    if (document.visibilityState !== 'visible') return;
    const now = Date.now();
    if (!force && now - lastRefreshAt < REFRESH_COOLDOWN_MS) return;
    const state = controlsState();
    if (!state) return;

    lastRefreshAt = now;
    clearTimeout(recoveryTimer);
    recoveryTimer = setTimeout(() => {
      if (document.visibilityState !== 'visible') return;
      const current = controlsState();
      if (!current) return;

      // One refresh only. The old implementation could call symbol.onchange,
      // interval.onchange and location.reload for the same Android resume, each
      // starting another large candle request. live.js now queues the latest
      // requested timeframe if an older candle request is still finishing.
      if (typeof current.interval.onchange === 'function') {
        Promise.resolve(current.interval.onchange()).catch(() => {});
      }

      window.dispatchEvent(new CustomEvent('galka:resume-refresh', {
        detail: { reason, at: Date.now() },
      }));
    }, 80);
  }

  function recover(reason) {
    if (document.visibilityState !== 'visible') return;
    const sleptMs = hiddenAt ? Date.now() - hiddenAt : 0;
    hiddenAt = 0;
    const state = controlsState();
    if (sleptMs >= REFRESH_AFTER_MS || state?.matches === false) {
      requestRefresh(`${reason}:${sleptMs}`, true);
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      hiddenAt = Date.now();
      return;
    }
    recover('visibilitychange');
  });

  window.addEventListener('pageshow', () => recover('pageshow'), { passive: true });
  window.addEventListener('focus', () => recover('focus'), { passive: true });
})();