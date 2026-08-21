(() => {
  'use strict';

  const charts = window.LightweightCharts;
  if (!charts?.createChart) return;

  const originalCreateChart = charts.createChart;
  let chart = null;
  let series = null;
  let optimisticLines = [];
  let creatingOptimisticLines = false;

  function removeOptimisticLines() {
    if (!series || !optimisticLines.length) return;
    const rows = optimisticLines;
    optimisticLines = [];
    for (const line of rows) {
      try {
        series.removePriceLine?.(line);
      } catch (_) {
        // The authoritative live.js renderer may already have rebuilt the chart.
      }
    }
  }

  function clearAllPriceLines() {
    if (!series) return;
    optimisticLines = [];
    const lines = Array.isArray(series.lines) ? [...series.lines] : [];
    if (lines.length && typeof series.removePriceLine === 'function') {
      for (const line of lines) {
        try {
          series.removePriceLine(line);
        } catch (_) {
          // UI cleanup is best effort; the next status render remains authoritative.
        }
      }
      chart?.draw?.();
      return;
    }
    if (Array.isArray(series.lines)) {
      series.lines = [];
      chart?.draw?.();
    }
  }

  function finitePrice(value) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : 0;
  }

  const bridge = {
    clearPriceLines: clearAllPriceLines,

    showCampaign(campaign) {
      if (!series || !campaign) return;
      clearAllPriceLines();
      const style = charts.LineStyle || {};
      creatingOptimisticLines = true;
      try {
        const galka = finitePrice(campaign.galkaPrice);
        if (galka) {
          optimisticLines.push(series.createPriceLine({
            price: galka,
            color: '#ff9800',
            lineWidth: 2,
            lineStyle: style.Solid ?? 0,
            axisLabelVisible: true,
            title: 'GALKA',
          }));
        }
        for (const level of campaign.levels || []) {
          const value = finitePrice(level.price);
          if (!value) continue;
          const filled = ['filled', 'partial'].includes(String(level.status || ''));
          optimisticLines.push(series.createPriceLine({
            price: value,
            color: filled ? '#16c784' : '#7c8797',
            lineWidth: 1,
            lineStyle: style.Dashed ?? 2,
            axisLabelVisible: true,
            title: `L${level.index}`,
          }));
        }
      } finally {
        creatingOptimisticLines = false;
      }
      chart?.draw?.();
    },
  };

  window.GalkaLiveUiBridge = bridge;
  window.LightweightCharts = Object.freeze({
    ...charts,
    createChart(container, options) {
      const created = originalCreateChart(container, options);
      chart = created;
      const originalAddSeries = created.addSeries.bind(created);
      created.addSeries = (...args) => {
        const createdSeries = originalAddSeries(...args);
        series = createdSeries;

        // Optimistic lines are only a bridge between the exchange response and
        // live.js's next authoritative status render. As soon as live.js creates
        // its first real price line, remove the temporary set so no duplicates
        // remain on the chart.
        if (typeof createdSeries.createPriceLine === 'function') {
          const originalCreatePriceLine = createdSeries.createPriceLine.bind(createdSeries);
          createdSeries.createPriceLine = (...lineArgs) => {
            if (!creatingOptimisticLines && optimisticLines.length) {
              removeOptimisticLines();
            }
            return originalCreatePriceLine(...lineArgs);
          };
        }
        return createdSeries;
      };
      return created;
    },
  });

  // -------------------------------------------------------------------------
  // Trading UI fast lane
  // -------------------------------------------------------------------------
  // The exchange operation can be complete while an older /status request is
  // still in flight. Previously that stale response could redraw the old GALKA
  // for many seconds, even though the server latency log correctly showed a
  // 6-10 second placement/cancel. Keep a tiny client-side mutation epoch and
  // render the POST response immediately; stale status responses are patched so
  // they can never roll the UI backwards.

  const previousFetch = window.fetch.bind(window);
  let mutationEpoch = 0;
  const latestCampaignMutation = new Map();

  function requestMeta(input, init) {
    try {
      const request = input instanceof Request ? input : null;
      const url = new URL(request ? request.url : String(input), location.href);
      const method = String(init?.method || request?.method || 'GET').toUpperCase();
      let body = null;
      if (typeof init?.body === 'string' && init.body) {
        try {
          body = JSON.parse(init.body);
        } catch (_) {
          body = null;
        }
      }
      return { path: url.pathname, method, body };
    } catch (_) {
      return { path: '', method: String(init?.method || 'GET').toUpperCase(), body: null };
    }
  }

  function currentCoin() {
    return String(document.getElementById('symbolSelect')?.value || '').toUpperCase();
  }

  function formatPrice(value, coin) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '';
    return number.toFixed(coin === 'SOL' ? 4 : 2);
  }

  function applyCampaignUi(campaign) {
    const coin = String(campaign?.coin || '').toUpperCase();
    if (!coin || currentCoin() !== coin) return;

    bridge.showCampaign(campaign);

    const status = document.getElementById('campaignStatus');
    const action = document.getElementById('previewButton');
    const input = document.getElementById('galkaInput');
    const cancel = document.getElementById('cancelCampaign');
    if (status) {
      const filled = (campaign.levels || []).filter((row) =>
        ['filled', 'partial'].includes(String(row.status || ''))).length;
      status.textContent = `${coin} · ждём ${filled}/8`;
      status.className = 'campaign-status waiting';
    }
    if (input) {
      input.value = formatPrice(campaign.galkaPrice, coin);
      input.disabled = true;
    }
    if (action) {
      action.disabled = false;
      action.textContent = 'Снять GALKA';
      action.dataset.quickCancel = '1';
    }
    if (cancel) cancel.disabled = false;

    document.dispatchEvent(new CustomEvent('galka:campaign-ui-updated', {
      detail: { coin, active: true, campaign },
    }));
  }

  function applyNoCampaignUi(coin) {
    const normalized = String(coin || '').toUpperCase();
    if (!normalized || currentCoin() !== normalized) return;

    bridge.clearPriceLines();
    const status = document.getElementById('campaignStatus');
    const action = document.getElementById('previewButton');
    const input = document.getElementById('galkaInput');
    const details = document.getElementById('campaignDetails');
    const cancel = document.getElementById('cancelCampaign');
    if (status) {
      status.textContent = `${normalized} · нет GALKA`;
      status.className = 'campaign-status idle';
    }
    if (input) {
      input.disabled = false;
      input.value = '';
    }
    if (action) {
      action.dataset.quickCancel = '';
      action.disabled = false;
      action.textContent = 'Поставить GALKA';
    }
    if (details) {
      details.innerHTML = '<div class="campaign-card"><small>Нет активной GALKA для выбранной монеты.</small></div>';
    }
    if (cancel) cancel.disabled = true;

    document.dispatchEvent(new CustomEvent('galka:campaign-ui-updated', {
      detail: { coin: normalized, active: false, campaign: null },
    }));
  }

  function jsonResponseLike(response, payload) {
    const headers = new Headers(response.headers);
    headers.set('Content-Type', 'application/json; charset=utf-8');
    headers.delete('Content-Length');
    return new Response(JSON.stringify(payload), {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  }

  async function patchStaleStatus(response, requestEpoch) {
    if (!response.ok || requestEpoch >= mutationEpoch) return response;
    try {
      const payload = await response.clone().json();
      const campaigns = payload?.data?.campaigns;
      if (!campaigns || typeof campaigns !== 'object') return response;
      let changed = false;
      for (const [coin, mutation] of latestCampaignMutation.entries()) {
        if (mutation.epoch <= requestEpoch) continue;
        if (mutation.campaign) campaigns[coin] = mutation.campaign;
        else delete campaigns[coin];
        changed = true;
      }
      return changed ? jsonResponseLike(response, payload) : response;
    } catch (_) {
      return response;
    }
  }

  window.fetch = async function galkaUiFastLaneFetch(input, init) {
    const meta = requestMeta(input, init);
    const requestEpoch = mutationEpoch;
    const response = await previousFetch(input, init);

    if (meta.method === 'GET' && meta.path === '/api/live/status') {
      return patchStaleStatus(response, requestEpoch);
    }

    if (meta.method === 'POST' && response.ok && (
      meta.path === '/api/live/campaign' || meta.path === '/api/live/cancel'
    )) {
      try {
        const payload = await response.clone().json();
        if (payload?.ok === false) return response;
        const bodyCoin = String(meta.body?.coin || '').toUpperCase();
        if (meta.path === '/api/live/campaign' && payload?.data) {
          const campaign = payload.data;
          const coin = String(campaign.coin || bodyCoin).toUpperCase();
          mutationEpoch += 1;
          latestCampaignMutation.set(coin, { epoch: mutationEpoch, campaign });
          applyCampaignUi(campaign);
        } else if (meta.path === '/api/live/cancel' && bodyCoin) {
          mutationEpoch += 1;
          latestCampaignMutation.set(bodyCoin, { epoch: mutationEpoch, campaign: null });
          applyNoCampaignUi(bodyCoin);
        }
      } catch (_) {
        // The normal live.js response handling remains authoritative on failure.
      }
    }

    return response;
  };
})();
