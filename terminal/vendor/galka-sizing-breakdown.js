(() => {
  'use strict';

  let maxMarginFraction = null;

  function finite(value, fallback = null) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function money(value) {
    const number = finite(value, 0);
    return `$${number.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  function ensureBox() {
    let box = document.getElementById('galkaSizingBreakdown');
    if (box) return box;
    const summary = document.querySelector('.preview-summary');
    if (!summary) return null;
    box = document.createElement('div');
    box.id = 'galkaSizingBreakdown';
    box.style.margin = '10px 0 4px';
    box.style.padding = '10px 12px';
    box.style.border = '1px solid #293241';
    box.style.borderRadius = '10px';
    box.style.fontSize = '12px';
    box.style.lineHeight = '1.45';
    box.style.color = '#aeb8c6';
    summary.insertAdjacentElement('afterend', box);
    return box;
  }

  function renderPreview(data) {
    if (!data || typeof data !== 'object') return;
    const account = finite(data.accountValue);
    const reserved = finite(data.reservedMargin, 0);
    const target = finite(data.targetMargin);
    const actual = finite(data.requiredMargin);
    const fraction = finite(data.targetMarginFraction, maxMarginFraction);
    if (account == null || actual == null) return;

    const effectiveFraction = fraction != null
      ? fraction
      : (target != null ? Math.min(1, Math.max(0, (target + reserved) / Math.max(account, 1e-12))) : null);
    const allowed = effectiveFraction != null ? account * effectiveFraction : null;
    const policyReserve = allowed != null ? Math.max(0, account - allowed) : null;
    const roundingReserve = target != null ? Math.max(0, target - actual) : null;
    const box = ensureBox();
    if (!box) return;

    const parts = [
      `Счёт ${money(account)}`,
      effectiveFraction != null ? `лимит GALKA ${(effectiveFraction * 100).toFixed(0)}%` : null,
      allowed != null ? `разрешено ${money(allowed)}` : null,
      reserved > 0 ? `уже зарезервировано ${money(reserved)}` : 'другие GALKA: $0.00',
      target != null ? `цель ${money(target)}` : null,
      `фактически после округления ${money(actual)}`,
      policyReserve != null ? `страховой остаток ${money(policyReserve)}` : null,
      roundingReserve != null && roundingReserve >= 0.005 ? `округление ${money(roundingReserve)}` : null,
    ].filter(Boolean);
    box.textContent = parts.join(' · ');
  }

  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    try {
      const request = args[0];
      const url = typeof request === 'string' ? request : String(request?.url || '');
      if (url.includes('/api/live/status')) {
        response.clone().json().then((payload) => {
          const fraction = finite(payload?.data?.maxMarginFraction);
          if (fraction != null) maxMarginFraction = fraction;
        }).catch(() => {});
      } else if (url.includes('/api/live/preview')) {
        response.clone().json().then((payload) => {
          if (payload?.ok !== false && payload?.data) {
            setTimeout(() => renderPreview(payload.data), 0);
          }
        }).catch(() => {});
      }
    } catch (_) {
      // Purely explanatory UI; never interfere with trading/API responses.
    }
    return response;
  };
})();