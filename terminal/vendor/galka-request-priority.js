(() => {
  'use strict';

  const nativeFetch = window.fetch.bind(window);
  const foregroundPaths = new Set([
    '/api/live/preview',
    '/api/live/campaign',
    '/api/live/cancel',
    '/api/live/reconcile',
    '/api/live/close-near-market',
    '/api/live/emergency',
    '/api/live/queue/activate',
    '/api/live/queue/delete',
  ]);
  // /status is deliberately NOT background here. It is a cheap UI state read
  // using isolated/cached market data and is the one read we want immediately
  // after placement/cancel. Heavy display/research reads wait instead.
  const backgroundPaths = new Set([
    '/api/live/candles',
    '/api/live/history',
    '/api/live/clusters',
    '/api/live/queue',
  ]);
  const BACKGROUND_SETTLE_MS = 1200;

  let foregroundCount = 0;
  let waiters = [];
  let quietUntil = 0;
  let releaseTimer = null;

  function requestMeta(input, init) {
    try {
      const request = input instanceof Request ? input : null;
      const url = new URL(request ? request.url : String(input), location.href);
      const method = String(init?.method || request?.method || 'GET').toUpperCase();
      return { path: url.pathname, method };
    } catch (_) {
      return { path: '', method: String(init?.method || 'GET').toUpperCase() };
    }
  }

  function backgroundBlocked() {
    return foregroundCount > 0 || performance.now() < quietUntil;
  }

  function releaseBackground() {
    clearTimeout(releaseTimer);
    releaseTimer = null;
    if (foregroundCount !== 0 || !waiters.length) return;
    const remaining = quietUntil - performance.now();
    if (remaining > 0) {
      releaseTimer = setTimeout(releaseBackground, remaining + 5);
      return;
    }
    const pending = waiters;
    waiters = [];
    for (const resolve of pending) resolve();
  }

  function waitForForeground() {
    if (!backgroundBlocked()) return Promise.resolve();
    return new Promise((resolve) => waiters.push(resolve));
  }

  function setBusyUi(path, busy) {
    const reconcile = document.getElementById('reconcileState');
    if (path === '/api/live/reconcile' && reconcile) {
      reconcile.setAttribute('aria-busy', busy ? 'true' : 'false');
      if (busy) {
        reconcile.disabled = true;
        reconcile.textContent = 'Сверка…';
      } else {
        reconcile.textContent = 'Сверить';
      }
    }
  }

  window.fetch = async function prioritizedFetch(input, init) {
    const meta = requestMeta(input, init);
    const isForeground = meta.method !== 'GET' && foregroundPaths.has(meta.path);
    const isBackground = meta.method === 'GET' && backgroundPaths.has(meta.path);

    if (isBackground && backgroundBlocked()) {
      await waitForForeground();
    }

    if (isForeground) {
      foregroundCount += 1;
      clearTimeout(releaseTimer);
      releaseTimer = null;
      setBusyUi(meta.path, true);
      document.dispatchEvent(new CustomEvent('galka:foreground-request', {
        detail: { active: true, path: meta.path, count: foregroundCount },
      }));
    }

    try {
      return await nativeFetch(input, init);
    } finally {
      if (isForeground) {
        foregroundCount = Math.max(0, foregroundCount - 1);
        setBusyUi(meta.path, false);
        document.dispatchEvent(new CustomEvent('galka:foreground-request', {
          detail: { active: false, path: meta.path, count: foregroundCount },
        }));
        if (foregroundCount === 0) {
          // Do not unleash candles/history/clusters at the exact moment the
          // exchange response arrives. Give the trading UI one frame to update
          // and /status a short quiet lane first.
          quietUntil = Math.max(quietUntil, performance.now() + BACKGROUND_SETTLE_MS);
        }
        releaseBackground();
      }
    }
  };

  window.GalkaRequestPriority = Object.freeze({
    foregroundActive: () => foregroundCount > 0,
    backgroundBlocked,
  });
})();
