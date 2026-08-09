(() => {
  'use strict';

  // Android browsers may freeze a background tab while a fetch is in flight.
  // live.js deliberately serializes candle/status reads; after a frozen request
  // the old page can therefore look LIVE while never accepting the next refresh.
  // A document reload is the safest recovery because the trading engine lives in
  // the Termux server, not in this page. sessionStorage survives same-tab reloads.
  const HARD_RELOAD_AFTER_MS = 8_000;
  const SOFT_REFRESH_AFTER_MS = 2_000;
  const RELOAD_GUARD_MS = 5_000;

  let hiddenAt = document.visibilityState === 'hidden' ? Date.now() : 0;
  let lastReloadRequestAt = 0;
  let recoveryTimer = null;

  function controlsMatch() {
    const symbol = document.getElementById('symbolSelect');
    const interval = document.getElementById('intervalSelect');
    const watermark = document.getElementById('watermark');
    if (!symbol || !interval || !watermark) return true;
    const expected = `${symbol.value} · ${interval.value} · HYPERLIQUID`;
    return watermark.textContent === expected;
  }

  function hardReload(reason) {
    const now = Date.now();
    if (now - lastReloadRequestAt < RELOAD_GUARD_MS) return;
    lastReloadRequestAt = now;
    try {
      sessionStorage.setItem('galkaResumeReason', reason);
    } catch (_) {
      // Session storage is optional; reload still restores the page from server.
    }
    location.reload();
  }

  function softRefresh(reason) {
    if (document.visibilityState !== 'visible') return;
    clearTimeout(recoveryTimer);
    recoveryTimer = setTimeout(() => {
      if (document.visibilityState !== 'visible') return;
      const symbol = document.getElementById('symbolSelect');
      const interval = document.getElementById('intervalSelect');

      // Re-run the public handlers so runtime.coin/runtime.interval cannot remain
      // out of sync with the visible controls.
      if (symbol && typeof symbol.onchange === 'function') {
        Promise.resolve(symbol.onchange()).catch(() => hardReload(`${reason}:symbol`));
      }
      if (interval && typeof interval.onchange === 'function') {
        Promise.resolve(interval.onchange()).catch(() => hardReload(`${reason}:interval`));
      }

      window.dispatchEvent(new CustomEvent('galka:resume-refresh', {
        detail: { reason, at: Date.now() },
      }));

      // If the old page was frozen mid-request the handlers can silently return
      // because their busy flags are still set. Detect the visible symptom and
      // rebuild the document instead of waiting indefinitely.
      setTimeout(() => {
        if (document.visibilityState === 'visible' && !controlsMatch()) {
          hardReload(`${reason}:stale-controls`);
        }
      }, 1_500);
    }, 50);
  }

  function recover(reason) {
    if (document.visibilityState !== 'visible') return;
    const sleptMs = hiddenAt ? Date.now() - hiddenAt : 0;
    hiddenAt = 0;

    if (sleptMs >= HARD_RELOAD_AFTER_MS) {
      hardReload(`${reason}:${sleptMs}`);
      return;
    }
    if (sleptMs >= SOFT_REFRESH_AFTER_MS || !controlsMatch()) {
      softRefresh(reason);
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      hiddenAt = Date.now();
      return;
    }
    recover('visibilitychange');
  });

  window.addEventListener('pageshow', (event) => {
    if (event.persisted) hardReload('pageshow-bfcache');
    else if (!controlsMatch()) softRefresh('pageshow-controls');
  }, { passive: true });

  window.addEventListener('focus', () => recover('focus'), { passive: true });
})();
