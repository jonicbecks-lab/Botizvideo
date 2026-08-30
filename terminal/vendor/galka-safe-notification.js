(() => {
  'use strict';

  const NativeNotification = window.Notification;
  if (typeof NativeNotification !== 'function') return;

  function SafeNotification(title, options) {
    try {
      return new NativeNotification(title, options);
    } catch (_) {
      if ('serviceWorker' in navigator) {
        navigator.serviceWorker.ready
          .then((registration) => registration.showNotification(title, options || {}))
          .catch(() => {});
      }
      return Object.freeze({ close() {} });
    }
  }

  Object.defineProperties(SafeNotification, {
    permission: {
      configurable: true,
      get: () => NativeNotification.permission,
    },
    requestPermission: {
      configurable: true,
      value: (...args) => NativeNotification.requestPermission(...args),
    },
  });

  try {
    window.Notification = SafeNotification;
  } catch (_) {
    // Some browsers expose a non-configurable Notification constructor. In that
    // case the trading UI continues without replacing it; notification failures
    // remain isolated by the browser and do not affect the exchange backend.
  }
})();
