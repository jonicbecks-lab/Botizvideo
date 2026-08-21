(() => {
  'use strict';

  const charts = window.LightweightCharts;
  if (!charts?.createChart) return;

  const originalCreateChart = charts.createChart.bind(charts);
  const previousFetch = window.fetch.bind(window);
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const MAX_STRUCTURE_BARS = 240;
  const CONTEXT_BARS = 20;
  const HANDLE_PICK_PX = 42;
  const ACTIVE_CAMPAIGN_STATUSES = new Set([
    'placing', 'waiting', 'open', 'closing', 'canceling', 'emergency', 'recovery',
  ]);

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function finite(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function createSvg(tag, className) {
    const node = document.createElementNS(SVG_NS, tag);
    if (className) node.setAttribute('class', className);
    return node;
  }

  function formatPrice(value) {
    const number = finite(value);
    if (!(number > 0)) return '—';
    return number.toFixed(2);
  }

  function isoMinute(timeSec) {
    if (!(Number(timeSec) > 0)) return '—';
    return new Date(Number(timeSec) * 1000).toLocaleString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function rowPayload(row) {
    if (!row || !(Number(row.time) > 0)) return null;
    const result = {
      timeMs: Math.round(Number(row.time) * 1000),
      open: finite(row.open),
      high: finite(row.high),
      low: finite(row.low),
      close: finite(row.close),
      volume: Math.max(0, finite(row.volume)),
    };
    if (!(result.open > 0 && result.high > 0 && result.low > 0 && result.close > 0)) return null;
    return result;
  }

  function nearestIndex(all, timeSec) {
    if (!all.length) return -1;
    let low = 0;
    let high = all.length - 1;
    const target = Number(timeSec);
    while (low <= high) {
      const middle = (low + high) >> 1;
      const value = Number(all[middle]?.time || 0);
      if (value === target) return middle;
      if (value < target) low = middle + 1;
      else high = middle - 1;
    }
    const left = clamp(high, 0, all.length - 1);
    const right = clamp(low, 0, all.length - 1);
    return Math.abs(Number(all[left]?.time || 0) - target) <= Math.abs(Number(all[right]?.time || 0) - target)
      ? left
      : right;
  }

  function installStructureDraft(chart, container) {
    if (!chart?.canvas || chart.__galkaStructureDraftInstalled) return chart;
    chart.__galkaStructureDraftInstalled = true;

    const canvas = chart.canvas;
    const overlay = createSvg('svg', 'galka-structure-overlay hidden');
    overlay.setAttribute('preserveAspectRatio', 'none');

    const fullLine = createSvg('line', 'galka-structure-line');
    const segment = createSvg('line', 'galka-structure-segment');
    const leftGuide = createSvg('line', 'galka-structure-guide');
    const rightGuide = createSvg('line', 'galka-structure-guide');

    const leftHandle = createSvg('g');
    const leftRing = createSvg('circle', 'galka-structure-anchor-ring');
    leftRing.setAttribute('r', '10');
    const leftMark = createSvg('path', 'galka-structure-anchor-mark');
    leftMark.setAttribute('d', 'M0 -6 A2 2 0 1 1 0 -2 M0 -2 L0 6 M-4 0 L4 0 M-7 3 C-6 7 -3 9 0 9 C3 9 6 7 7 3 M-7 3 L-4 5 M7 3 L4 5');
    leftHandle.append(leftRing, leftMark);

    const rightHandle = createSvg('path', 'galka-structure-end');
    rightHandle.setAttribute('d', 'M0 -8 L8 0 L0 8 L-8 0 Z');

    overlay.append(fullLine, segment, leftGuide, rightGuide, leftHandle, rightHandle);
    container.append(overlay);

    const toolbar = document.createElement('div');
    toolbar.className = 'galka-structure-toolbar hidden';
    const info = document.createElement('div');
    info.className = 'galka-structure-info';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.textContent = 'Отмена';
    const confirm = document.createElement('button');
    confirm.type = 'button';
    confirm.className = 'galka-structure-confirm';
    confirm.textContent = 'Подтвердить';
    confirm.disabled = true;
    toolbar.append(info, cancel, confirm);
    container.append(toolbar);

    const state = {
      active: false,
      coin: '',
      interval: '',
      galkaPrice: 0,
      anchorTime: 0,
      endTime: 0,
      phase: 'idle',
      pointerId: null,
      dragging: null,
      submitting: false,
      startedAtMs: 0,
      campaignId: '',
    };

    function currentInput() {
      return document.getElementById('galkaInput');
    }

    function previewButton() {
      return document.getElementById('previewButton');
    }

    function selectedCoin() {
      return String(document.getElementById('symbolSelect')?.value || 'BTC').toUpperCase();
    }

    function selectedInterval() {
      return String(document.getElementById('intervalSelect')?.value || '5m');
    }

    function allRows() {
      return Array.isArray(chart.series?.data) ? chart.series.data : [];
    }

    function snappedTimeAtX(x) {
      const windowState = chart.visibleWindow?.();
      const geometry = chart.geometry?.();
      if (!windowState?.all?.length || !geometry) return 0;
      const ratio = clamp((Number(x) - geometry.left) / geometry.plotWidth, 0, 0.999999);
      const logical = Math.floor(windowState.start + ratio * windowState.count);
      const index = clamp(logical, 0, windowState.all.length - 1);
      return Number(windowState.all[index]?.time || 0);
    }

    function xForTime(timeSec) {
      const windowState = chart.visibleWindow?.();
      const geometry = chart.geometry?.();
      if (!windowState?.all?.length || !geometry) return null;
      const index = nearestIndex(windowState.all, timeSec);
      if (index < 0) return null;
      const slot = geometry.plotWidth / Math.max(1, windowState.count);
      const x = geometry.left + (index - windowState.start + 0.5) * slot;
      if (x < geometry.left - 20 || x > geometry.right + 20) return null;
      return x;
    }

    function yForPrice(price) {
      const geometry = chart.geometry?.();
      const rows = chart.lastRows?.length ? chart.lastRows : chart.visibleWindow?.().rows;
      const range = chart.currentPriceRange?.(rows || []);
      if (!geometry || !range || !(range.span > 0)) return null;
      return geometry.top + (range.max - Number(price)) / range.span * geometry.plotHeight;
    }

    function setLine(node, x1, y1, x2, y2, visible = true) {
      if (!visible) {
        node.setAttribute('visibility', 'hidden');
        return;
      }
      node.setAttribute('visibility', 'visible');
      node.setAttribute('x1', String(x1));
      node.setAttribute('y1', String(y1));
      node.setAttribute('x2', String(x2));
      node.setAttribute('y2', String(y2));
    }

    function setTransform(node, x, y, visible = true) {
      node.setAttribute('visibility', visible ? 'visible' : 'hidden');
      if (visible) node.setAttribute('transform', `translate(${x} ${y})`);
    }

    function barCount() {
      const all = allRows();
      if (!all.length || !state.anchorTime || !state.endTime) return 0;
      const left = nearestIndex(all, Math.min(state.anchorTime, state.endTime));
      const right = nearestIndex(all, Math.max(state.anchorTime, state.endTime));
      return left >= 0 && right >= left ? right - left + 1 : 0;
    }

    function updateToolbar() {
      if (!state.active || state.phase === 'locked') {
        toolbar.classList.add('hidden');
        return;
      }
      toolbar.classList.remove('hidden');
      if (!state.endTime || state.phase === 'choose-right') {
        info.innerHTML = `<b>⚓ ${formatPrice(state.galkaPrice)}</b> · выбери правый край GALKA`;
        confirm.disabled = true;
        return;
      }
      const count = barCount();
      info.innerHTML = `<b>⚓ ${formatPrice(state.galkaPrice)}</b> · ${count} свеч. · ${isoMinute(state.anchorTime)} → ${isoMinute(state.endTime)}`;
      confirm.disabled = state.submitting || count < 1;
    }

    function render() {
      if (!state.active || state.coin !== selectedCoin()) {
        overlay.classList.add('hidden');
        toolbar.classList.add('hidden');
        return;
      }
      const geometry = chart.geometry?.();
      const y = yForPrice(state.galkaPrice);
      if (!geometry || y == null || y < geometry.top - 2 || y > geometry.bottom + 2) {
        overlay.classList.add('hidden');
        updateToolbar();
        return;
      }

      overlay.classList.remove('hidden');
      overlay.setAttribute('viewBox', `0 0 ${geometry.width} ${geometry.height}`);
      const locked = state.phase === 'locked';
      setLine(fullLine, geometry.left, y, geometry.right, y, !locked);

      const leftX = xForTime(state.anchorTime);
      const rightX = state.endTime ? xForTime(state.endTime) : null;
      setTransform(leftHandle, leftX ?? 0, y, leftX != null);
      setLine(leftGuide, leftX ?? 0, geometry.top, leftX ?? 0, geometry.bottom, leftX != null && !locked);
      setTransform(rightHandle, rightX ?? 0, y, rightX != null);
      setLine(rightGuide, rightX ?? 0, geometry.top, rightX ?? 0, geometry.bottom, rightX != null && !locked);
      setLine(segment, leftX ?? geometry.left, y, rightX ?? leftX ?? geometry.left, y, leftX != null && rightX != null);
      updateToolbar();
    }

    function restoreMainAction() {
      const action = previewButton();
      const input = currentInput();
      if (!action || input?.disabled) return;
      action.disabled = false;
      action.textContent = 'Поставить GALKA';
      action.dataset.structureDraft = '';
    }

    function resetState({ keepInput = false, restoreAction = true } = {}) {
      state.active = false;
      state.coin = '';
      state.interval = '';
      state.galkaPrice = 0;
      state.anchorTime = 0;
      state.endTime = 0;
      state.phase = 'idle';
      state.pointerId = null;
      state.dragging = null;
      state.submitting = false;
      state.startedAtMs = 0;
      state.campaignId = '';
      overlay.classList.add('hidden');
      toolbar.classList.add('hidden');
      if (!keepInput) {
        const input = currentInput();
        if (input && !input.disabled) input.value = '';
      }
      if (restoreAction) restoreMainAction();
      chart.draw?.();
    }

    function showLockedCampaign(campaign) {
      const setup = campaign?.researchSetup;
      const coin = String(campaign?.coin || '').toUpperCase();
      if (
        !coin ||
        coin !== selectedCoin() ||
        !setup ||
        setup.lockedForCampaign !== true ||
        !(Number(setup.anchorTimeMs) > 0) ||
        !(Number(setup.structureEndTimeMs) > 0)
      ) {
        if (state.phase === 'locked') resetState({ keepInput: true, restoreAction: false });
        return;
      }
      state.active = true;
      state.coin = coin;
      state.interval = String(setup.timeframe || '5m');
      state.galkaPrice = finite(campaign.galkaPrice || setup.galkaLevel);
      state.anchorTime = Number(setup.anchorTimeMs) / 1000;
      state.endTime = Number(setup.structureEndTimeMs) / 1000;
      state.phase = 'locked';
      state.pointerId = null;
      state.dragging = null;
      state.submitting = false;
      state.startedAtMs = Number(setup.draftStartedAtMs || 0);
      state.campaignId = String(campaign.id || '');
      render();
    }

    function beginFromCrosshair(event) {
      const input = currentInput();
      const action = previewButton();
      if (!input || input.disabled || action?.dataset?.quickCancel === '1') return false;
      const galkaPrice = finite(event.detail?.price);
      if (!(galkaPrice > 0)) return false;
      const crosshairX = finite(chart.crosshair?.x, NaN);
      const anchorTime = Number(event.detail?.timeSec || (Number.isFinite(crosshairX) ? snappedTimeAtX(crosshairX) : 0));
      if (!(anchorTime > 0)) return false;

      state.active = true;
      state.coin = selectedCoin();
      state.interval = selectedInterval();
      state.galkaPrice = galkaPrice;
      state.anchorTime = anchorTime;
      state.endTime = 0;
      state.phase = 'choose-right';
      state.pointerId = null;
      state.dragging = null;
      state.submitting = false;
      state.startedAtMs = Date.now();
      state.campaignId = '';

      input.value = formatPrice(galkaPrice);
      if (action) {
        action.dataset.structureDraft = '1';
        action.disabled = true;
        action.textContent = 'Выбери правый край';
      }
      render();
      if (navigator.vibrate) navigator.vibrate(12);
      return true;
    }

    function chooseDragTarget(pointX) {
      if (!state.endTime || state.phase === 'choose-right') return 'right';
      const leftX = xForTime(state.anchorTime);
      const rightX = xForTime(state.endTime);
      if (leftX == null) return 'right';
      if (rightX == null) return 'left';
      const leftDistance = Math.abs(pointX - leftX);
      const rightDistance = Math.abs(pointX - rightX);
      if (leftDistance <= HANDLE_PICK_PX || rightDistance <= HANDLE_PICK_PX) {
        return leftDistance <= rightDistance ? 'left' : 'right';
      }
      return leftDistance <= rightDistance ? 'left' : 'right';
    }

    function setDraggedTime(kind, timeSec) {
      if (!(timeSec > 0)) return;
      if (kind === 'left') {
        state.anchorTime = timeSec;
        if (state.endTime && state.anchorTime > state.endTime) {
          const previousEnd = state.endTime;
          state.endTime = state.anchorTime;
          state.anchorTime = previousEnd;
          state.dragging = 'right';
        }
      } else {
        state.endTime = timeSec;
        if (state.endTime < state.anchorTime) {
          const previousLeft = state.anchorTime;
          state.anchorTime = state.endTime;
          state.endTime = previousLeft;
          state.dragging = 'left';
        }
      }
    }

    function stopCanvasEvent(event) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }

    canvas.addEventListener('galka:select-price', (event) => {
      if (currentInput()?.disabled) return;
      if (!beginFromCrosshair(event)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);

    canvas.addEventListener('pointerdown', (event) => {
      if (!state.active || state.phase === 'locked' || state.submitting) return;
      const point = chart.pointFromEvent?.(event);
      if (!point || point.zone !== 'plot') return;
      stopCanvasEvent(event);
      const timeSec = snappedTimeAtX(point.x);
      if (!(timeSec > 0)) return;
      state.pointerId = event.pointerId;
      state.dragging = chooseDragTarget(point.x);
      setDraggedTime(state.dragging, timeSec);
      try {
        canvas.setPointerCapture(event.pointerId);
      } catch (_) {
        // Pointer capture is a convenience only.
      }
      render();
    }, true);

    canvas.addEventListener('pointermove', (event) => {
      if (!state.active || state.phase === 'locked' || state.submitting || state.pointerId !== event.pointerId || !state.dragging) return;
      const point = chart.pointFromEvent?.(event);
      if (!point || point.zone !== 'plot') return;
      stopCanvasEvent(event);
      setDraggedTime(state.dragging, snappedTimeAtX(point.x));
      render();
    }, true);

    function finishPointer(event) {
      if (!state.active || state.phase === 'locked' || state.pointerId !== event.pointerId) return;
      stopCanvasEvent(event);
      state.pointerId = null;
      state.dragging = null;
      if (state.endTime) state.phase = 'adjust';
      try {
        if (canvas.hasPointerCapture?.(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
      } catch (_) {
        // Android may release capture first.
      }
      render();
      if (navigator.vibrate) navigator.vibrate(8);
    }

    canvas.addEventListener('pointerup', finishPointer, true);
    canvas.addEventListener('pointercancel', finishPointer, true);

    function contextRows() {
      const all = allRows();
      if (!all.length || !state.anchorTime || !state.endTime) return null;
      let left = nearestIndex(all, state.anchorTime);
      let right = nearestIndex(all, state.endTime);
      if (left < 0 || right < 0) return null;
      if (left > right) [left, right] = [right, left];
      const totalBars = right - left + 1;
      const structureStart = totalBars > MAX_STRUCTURE_BARS ? right - MAX_STRUCTURE_BARS + 1 : left;
      return {
        all,
        left,
        right,
        totalBars,
        structureBars: all.slice(structureStart, right + 1).map(rowPayload).filter(Boolean),
        structureBarsTruncated: totalBars > MAX_STRUCTURE_BARS,
        preContextBars: all.slice(Math.max(0, left - CONTEXT_BARS), left).map(rowPayload).filter(Boolean),
        postContextBars: all.slice(right + 1, Math.min(all.length, right + 1 + CONTEXT_BARS)).map(rowPayload).filter(Boolean),
      };
    }

    function buildResearchSetup() {
      const context = contextRows();
      if (!context) throw new Error('Не удалось прочитать свечи выбранной GALKA');
      return {
        schemaVersion: 1,
        selectionMethod: 'manual_crosshair_structure_v1',
        symbol: state.coin,
        timeframe: state.interval,
        galkaLevel: state.galkaPrice,
        anchorTimeMs: Math.round(state.anchorTime * 1000),
        structureEndTimeMs: Math.round(state.endTime * 1000),
        selectedAtMs: Date.now(),
        draftStartedAtMs: state.startedAtMs,
        fullStructureBarCount: context.totalBars,
        structureBarsTruncated: context.structureBarsTruncated,
        anchorCandle: rowPayload(context.all[context.left]),
        structureEndCandle: rowPayload(context.all[context.right]),
        preContextBars: context.preContextBars,
        structureBars: context.structureBars,
        postContextBarsAtPlacement: context.postContextBars,
        lockedForCampaign: true,
      };
    }

    async function submit() {
      if (!state.active || state.phase === 'locked' || !state.endTime || state.submitting) return;
      const token = sessionStorage.getItem('galkaLiveSession') || '';
      if (!token) {
        const toast = document.getElementById('toast');
        if (toast) {
          toast.textContent = 'Открой терминал через защищённую ссылку из Termux';
          toast.className = 'toast error';
        }
        return;
      }
      state.submitting = true;
      confirm.disabled = true;
      confirm.textContent = 'Отправка…';
      updateToolbar();

      try {
        const researchSetup = buildResearchSetup();
        const response = await fetch('/api/live/campaign', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Galka-Session': token,
          },
          body: JSON.stringify({
            coin: state.coin,
            galkaPrice: state.galkaPrice,
            confirmation: 'PLACE_REAL_ORDERS',
            researchSetup,
          }),
          cache: 'no-store',
          credentials: 'same-origin',
        });
        const payload = await response.json();
        if (!response.ok || payload?.ok === false) {
          throw new Error(payload?.error || `HTTP ${response.status}`);
        }
        const campaign = payload.data;
        showLockedCampaign(campaign);
        document.dispatchEvent(new CustomEvent('galka:structure-committed', {
          detail: { campaign, researchSetup },
        }));
        const toast = document.getElementById('toast');
        if (toast) {
          toast.textContent = 'GALKA + якорь зафиксированы, реальные лимитки выставлены';
          toast.className = 'toast ok';
          setTimeout(() => toast.classList.add('hidden'), 4500);
        }
      } catch (error) {
        state.submitting = false;
        confirm.textContent = 'Подтвердить';
        confirm.disabled = false;
        updateToolbar();
        const toast = document.getElementById('toast');
        if (toast) {
          toast.textContent = error.message || 'Не удалось выставить GALKA';
          toast.className = 'toast error';
          setTimeout(() => toast.classList.add('hidden'), 4500);
        }
      }
    }

    cancel.addEventListener('click', () => resetState());
    confirm.addEventListener('click', submit);
    document.getElementById('symbolSelect')?.addEventListener('change', () => {
      if (state.active) resetState({ keepInput: true, restoreAction: state.phase !== 'locked' });
    });
    document.getElementById('intervalSelect')?.addEventListener('change', () => {
      if (state.active && state.phase !== 'locked') resetState();
    });
    document.addEventListener('galka:campaign-ui-updated', (event) => {
      if (event.detail?.active) {
        showLockedCampaign(event.detail?.campaign);
      } else if (state.phase === 'locked') {
        resetState({ keepInput: true, restoreAction: false });
      }
    });

    const originalDraw = chart.draw.bind(chart);
    chart.draw = (...args) => {
      const result = originalDraw(...args);
      requestAnimationFrame(render);
      return result;
    };

    const api = {
      active: () => state.active && state.phase !== 'locked',
      locked: () => state.phase === 'locked',
      cancel: resetState,
      showLockedCampaign,
      clearLocked() {
        if (state.phase === 'locked') resetState({ keepInput: true, restoreAction: false });
      },
      snapshot: () => state.active ? {
        coin: state.coin,
        timeframe: state.interval,
        galkaLevel: state.galkaPrice,
        anchorTimeMs: state.anchorTime * 1000,
        structureEndTimeMs: state.endTime * 1000,
        phase: state.phase,
      } : null,
    };
    window.GalkaStructureDraft = api;
    return chart;
  }

  // Restore the locked anchor after page reload/status refresh without adding a
  // blocking request to the trading path. The response is returned immediately;
  // a clone is inspected asynchronously only for UI annotation.
  window.fetch = function galkaStructureStatusFetch(input, init) {
    const responsePromise = previousFetch(input, init);
    try {
      const request = input instanceof Request ? input : null;
      const url = new URL(request ? request.url : String(input), location.href);
      const method = String(init?.method || request?.method || 'GET').toUpperCase();
      if (method === 'GET' && url.pathname === '/api/live/status') {
        responsePromise.then((response) => {
          if (!response.ok) return;
          response.clone().json().then((payload) => {
            const coin = String(document.getElementById('symbolSelect')?.value || '').toUpperCase();
            const campaign = payload?.data?.campaigns?.[coin];
            if (campaign && ACTIVE_CAMPAIGN_STATUSES.has(String(campaign.status || '')) && campaign.researchSetup) {
              window.GalkaStructureDraft?.showLockedCampaign(campaign);
            } else {
              window.GalkaStructureDraft?.clearLocked();
            }
          }).catch(() => {});
        }).catch(() => {});
      }
    } catch (_) {
      // Never affect the actual request.
    }
    return responsePromise;
  };

  window.LightweightCharts = Object.freeze({
    ...charts,
    createChart(container, options) {
      return installStructureDraft(originalCreateChart(container, options), container);
    },
  });
})();
