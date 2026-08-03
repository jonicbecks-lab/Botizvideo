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
      // The existing per-tab header still authorizes the current page. A later
      // protected open can retry cookie creation without exposing the token.
    });
    return;
  }

  // live.js still sends its legacy session header. This non-secret sentinel lets
  // it issue requests after a restored tab; the server authenticates the
  // request with the persistent HttpOnly cookie instead.
  if (!sessionStorage.getItem(STORAGE_KEY)) {
    sessionStorage.setItem(STORAGE_KEY, 'persistent-cookie-session');
  }
})();
