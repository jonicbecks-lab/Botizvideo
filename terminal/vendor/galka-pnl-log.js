(() => {
  'use strict';

  const STORAGE_KEY = 'galka-live-campaign-pnl-v1';
  const COMPLETION_RE = /кампания завершена|recovery завершён flat|позиция фактически закрыта|закрыта на GALKA/i;
  let latestStatus = null;
  let scheduled = false;

  function finite(value, fallback = null) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function loadStore() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (_) {
      return {};
    }
  }

  function saveStore(store) {
    try {
      const rows = Object.entries(store)
        .sort((left, right) => Number(left[1]?.time || 0) - Number(right[1]?.time || 0))
        .slice(-120);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(rows)));
    } catch (_) {
      // PnL decoration is best-effort only.
    }
  }

  function summaryFromCampaign(campaign) {
    if (!campaign?.id) return null;
    const gross = finite(campaign.cycleClosedPnl, 0) + finite(campaign.l1RealizedPnl, 0);
    const fees = Math.max(0, finite(campaign.cycleFees, 0));
    const finalNet = finite(campaign.finalClosedPnl);
    const net = finalNet != null ? finalNet : gross - fees;
    const hadTrade = Number(campaign.cycleDeepest || 0) > 0 ||
      (campaign.levels || []).some((level) => Number(level.filledSize || 0) > 0);
    if (!hadTrade || !campaign.completedAt) return null;
    return {
      campaignId: String(campaign.id),
      gross,
      fees,
      net,
      time: Date.parse(String(campaign.completedAt || '')) || Date.now(),
    };
  }

  function money(value, signed = true) {
    const number = Number(value || 0);
    const sign = signed && number >= 0 ? '+' : '';
    return `${sign}$${number.toFixed(2)}`;
  }

  function capture(status) {
    if (!status || typeof status !== 'object') return;
    latestStatus = status;
    const store = loadStore();
    for (const campaign of Object.values(status.campaigns || {})) {
      const summary = summaryFromCampaign(campaign);
      if (summary) store[summary.campaignId] = summary;
    }
    saveStore(store);
    scheduleDecorate();
  }

  function scheduleDecorate() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      decorate();
    });
  }

  function decorate() {
    if (!latestStatus) return;
    const cards = [...document.querySelectorAll('#events .event')];
    if (!cards.length) return;
    const events = (latestStatus.events || []).slice().reverse().slice(0, 40);
    const store = loadStore();

    cards.forEach((card, index) => {
      const event = events[index];
      if (!event || !COMPLETION_RE.test(String(event.message || ''))) return;
      const campaignId = String(event?.meta?.campaignId || '');
      if (!campaignId) return;
      const summary = store[campaignId];
      if (!summary) return;

      let line = card.querySelector('[data-galka-pnl-summary]');
      if (!line) {
        line = document.createElement('small');
        line.dataset.galkaPnlSummary = '1';
        card.appendChild(line);
      }
      line.textContent =
        `Брутто ${money(summary.gross)} · комиссия -$${Number(summary.fees || 0).toFixed(2)} · нетто ${money(summary.net)}`;
    });
  }

  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    try {
      const request = args[0];
      const url = typeof request === 'string' ? request : String(request?.url || '');
      if (url.includes('/api/live/status')) {
        response.clone().json().then((payload) => {
          if (payload?.ok !== false && payload?.data) capture(payload.data);
        }).catch(() => {});
      }
    } catch (_) {
      // Never interfere with the trading response.
    }
    return response;
  };

  const observer = new MutationObserver(scheduleDecorate);
  document.addEventListener('DOMContentLoaded', () => {
    const events = document.getElementById('events');
    if (events) observer.observe(events, { childList: true, subtree: true });
    scheduleDecorate();
  });
})();
