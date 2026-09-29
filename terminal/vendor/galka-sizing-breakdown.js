(() => {
  'use strict';

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

  function renderWholeDollarPreview(data, box) {
    const account = finite(data.accountValue);
    const reserved = finite(data.reservedMargin, 0);
    const available = finite(data.availableMargin);
    const ceiling = finite(data.wholeDollarCeiling);
    const target = finite(data.targetMargin);
    const actual = finite(data.requiredMargin);
    const entryFees = finite(data.estimatedEntryFeeReserve, 0);
    const technicalBuffer = finite(data.technicalBufferUsd, 0);
    const technicalReserve = finite(data.technicalReserveRequired, entryFees + technicalBuffer);
    const remainder = finite(data.cashLeftAfterMargin);
    const stepDown = finite(data.wholeDollarStepDown, 0);
    if (account == null || actual == null) return false;

    const parts = [
      `Счёт ${money(account)}`,
      reserved > 0 ? `другие GALKA ${money(reserved)}` : 'другие GALKA $0.00',
      available != null ? `доступно ${money(available)}` : null,
      ceiling != null ? `целые $: ${money(ceiling)}` : null,
      stepDown > 0 ? `тех. коррекция −${money(stepDown)}` : null,
      target != null ? `цель ${money(target)}` : null,
      `фактически ${money(actual)}`,
      entryFees > 0 ? `резерв комиссии входа ${money(entryFees)}` : null,
      technicalBuffer > 0 ? `тех. запас ${money(technicalBuffer)}` : null,
      technicalReserve > 0 ? `нужно оставить ≥ ${money(technicalReserve)}` : null,
      remainder != null ? `останется ${money(remainder)}` : null,
    ].filter(Boolean);
    box.textContent = parts.join(' · ');
    return true;
  }

  function renderLegacyPreview(data, box) {
    const account = finite(data.accountValue);
    const reserved = finite(data.reservedMargin, 0);
    const target = finite(data.targetMargin);
    const actual = finite(data.requiredMargin);
    const fraction = finite(data.targetMarginFraction, data.legacyMaxMarginFraction);
    if (account == null || actual == null) return;

    const allowed = fraction != null ? account * fraction : null;
    const policyReserve = allowed != null ? Math.max(0, account - allowed) : null;
    const roundingReserve = target != null ? Math.max(0, target - actual) : null;
    const parts = [
      `Счёт ${money(account)}`,
      fraction != null ? `старый лимит ${(fraction * 100).toFixed(0)}%` : null,
      allowed != null ? `разрешено ${money(allowed)}` : null,
      reserved > 0 ? `уже зарезервировано ${money(reserved)}` : 'другие GALKA $0.00',
      target != null ? `цель ${money(target)}` : null,
      `фактически после округления ${money(actual)}`,
      policyReserve != null ? `страховой остаток ${money(policyReserve)}` : null,
      roundingReserve != null && roundingReserve >= 0.005 ? `округление ${money(roundingReserve)}` : null,
    ].filter(Boolean);
    box.textContent = parts.join(' · ');
  }

  function renderPreview(data) {
    if (!data || typeof data !== 'object') return;
    const box = ensureBox();
    if (!box) return;
    if (String(data.sizingPolicy || '').startsWith('whole_dollars_')) {
      if (renderWholeDollarPreview(data, box)) return;
    }
    renderLegacyPreview(data, box);
  }

  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    try {
      const request = args[0];
      const url = typeof request === 'string' ? request : String(request?.url || '');
      if (url.includes('/api/live/preview')) {
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
