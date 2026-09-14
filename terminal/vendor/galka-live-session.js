(() => {
  'use strict';

  const STORAGE_KEY = 'galkaLiveSession';
  const params = new URLSearchParams(location.hash.replace(/^#/, ''));
  const token = params.get('token');

  if (token) {
    sessionStorage.setItem(STORAGE_KEY, token);
    fetch('/api/live/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
      cache: 'no-store',
      credentials: 'same-origin',
    }).catch(() => {
      // The current tab can still use the legacy header. V2 normally boots via
      // /open and does not expose a token in the URL.
    });
  } else if (!sessionStorage.getItem(STORAGE_KEY)) {
    // V2 authenticates with a persistent HttpOnly cookie. Keep the legacy
    // client-side guard satisfied without storing a real secret in JS storage.
    sessionStorage.setItem(STORAGE_KEY, 'persistent-cookie-session');
  }

  // Android may suspend/restore the browser while the local server is restarted.
  // If an API call ever receives 401, repair the local cookie through /open and
  // reload the terminal automatically instead of leaving the user on a dead tab.
  if (!window.fetch.__galkaSessionRecoveryWrapped) {
    const originalFetch = window.fetch.bind(window);
    let recovering = false;

    async function recoveringFetch(input, init) {
      const response = await originalFetch(input, init);
      const url = typeof input === 'string' ? input : input?.url || '';
      const isApi = /\/api\/live\//.test(String(url));
      const isSessionBootstrap = /\/api\/live\/session(?:\?|$)/.test(String(url));

      if (response.status === 401 && isApi && !isSessionBootstrap && !recovering) {
        recovering = true;
        setTimeout(() => {
          location.replace('/open');
        }, 0);
      }
      return response;
    }

    recoveringFetch.__galkaSessionRecoveryWrapped = true;
    window.fetch = recoveringFetch;
  }
})();
