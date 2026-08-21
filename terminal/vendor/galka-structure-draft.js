(() => {
  'use strict';

  const charts = window.LightweightCharts;
  if (!charts?.createChart) return;

  const originalCreateChart = charts.createChart.bind(charts);
  const previousFetch = window.fetch.bind(window);
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const MAX_STRUCTURE_BARS = 240;
  const CONTEXT_BARS = 20;
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

  function estimateInterval(all) {
    for (let index = all.length - 1; index > 0; index -= 1) {
      const delta = Number(all[index]?.time || 0) - Number(all[index - 1]?.time || 0);
      if (delta > 0) return delta;
    }
    return 300;
  }

  function installStructureDraft(chart, container) {
    if (!chart?.canvas || chart.__galkaStructureDraftInstalled) return chart;
    chart.__galkaStructureDraftInstalled = true;

    const canvas = chart.canvas;
    const overlay = createSvg('svg', 'galka-structure-overlay hidden');
    overlay.setAttribute('preserveAspectRatio', 'none');

    const fullLine = createSvg('line', 'galka-structure-line');
    const shape = createSvg('polyline', 'galka-structure-shape');
    const leftGuide = createSvg('line', 'galka-structure-guide');
    const rightGuide = createSvg('line', 'galka-structure-guide');

    const anchorHandle = createSvg('g');
    const anchorRing = createSvg('circle', 'galka-structure-anchor-ring');
    anchorRing.setAttribute('r', '10');
    const anchorMark = createSvg('path', 'galka-structure-anchor-mark');
    anchorMark.setAttribute('d', 'M0 -6 A2 2 0 1 1 0 -2 M0 -2 L0 6 M-4 0 L4 0 M-7 3 C-6 7 -3 9 0 9 C3 9 6 7 7 3 M-7 3 L-4 5 M7 3 L4 5');
    anchorHandle.append(anchorRing, anchorMark);

    const leftHandle = createSvg('path', 'galka-structure-boundary galka-structure-boundary-left');
    leftHandle.setAttribute('d', 'M0 -8 L8 0 L0 8 L-8 0 Z');
    const rightHandle = createSvg('path', 'galka-structure-boundary galka-structure-boundary-right');
    rightHandle.setAttribute('d', 'M0 -8 L8 0 L0 8 L-8 0 Z');

    overlay.append(fullLine, shape, leftGuide, rightGuide, leftHandle, rightHandle, anchorHandle);
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
      leftTime: 0,
      leftPrice: 0,
      rightTime: 0,
      rightPrice: 0,
      phase: 'idle',
      pointerId: null,
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

    function timeAtX(x) {
      const windowState = chart.visibleWindow?.();
      const geometry = chart.geometry?.();
      if (!windowState?.all?.length || !geometry) return 0;
      const ratio = clamp((Number(x) - geometry.left) / geometry.plotWidth, 0, 1);
      const logical = windowState.start + ratio * windowState.count;
      const interval = estimateInterval(windowState.all);
      const lowerLogical = Math.floor(logical);
      const fraction = logical - lowerLogical;
      let baseTime;
      if (lowerLogical < 0) {
        baseTime = Number(windowState.all[0]?.time || 0) + lowerLogical * interval;
      } else if (lowerLogical >= windowState.all.length) {
        baseTime = Number(windowState.all.at(-1)?.time || 0) + (lowerLogical - windowState.all.length + 1) * interval;
      } else {
        baseTime = Number(windowState.all[lowerLogical]?.time || 0);
      }
      return baseTime + fraction * interval;
    }

    function xForTime(timeSec) {
      const windowState = chart.visibleWindow?.();
      const geometry = chart.geometry?.();
      if (!windowState?.all?.length || !geometry || !(Number(timeSec) > 0)) return null;
      const all = windowState.all;
      const interval = estimateInterval(all);
      const target = Number(timeSec);
      let logical;
      if (target <= Number(all[0]?.time || 0)) {
        logical = (target - Number(all[0]?.time || 0)) / interval;
      } else if (target >= Number(all.at(-1)?.time || 0)) {
        logical = all.length - 1 + (target - Number(all.at(-1)?.time || 0)) / interval;
      } else {
        const index = nearestIndex(all, target);
        const base = Number(all[index]?.time || 0);
        logical = index + (target - base) / interval;
      }
      const x = geometry.left + (logical - windowState.start) / windowState.count * geometry.plotWidth;
      return x >= geometry.left - 20 && x <= geometry.right + 20 ? x : null;
    }

    function priceAtY(y) {
      const geometry = chart.geometry?.();
      const rows = chart.lastRows?.length ? chart.lastRows : chart.visibleWindow?.().rows;
      const range = chart.currentPriceRange?.(rows || []);
      if (!geometry || !range || !(range.span > 0)) return 0;
      const clamped = clamp(Number(y), geometry.top, geometry.bottom);
      return range.max - ((clamped - geometry.top) / geometry.plotHeight) * range.span;
    }

    function yForPrice(price) {
      const geometry = chart.geometry?.();
      const rows = chart.lastRows?.length ? chart.lastRows : chart.visibleWindow?.().rows;
      const range = chart.currentPriceRange?.(rows || []);
      if (!geometry || !range || !(range.span > 0)) return null;
      return geometry.top + (range.max - Number(price)) / range.span * geometry.plotHeight;
    }

    function setLine(node, x1, y1, x2, y2, visible = true) {
      node.setAttribute('visibility', visible ? 'visible' : 'hidden');
      if (!visible) return;
      node.setAttribute('x1', String(x1));
      node.setAttribute('y1', String(y1));
      node.setAttribute('x2', String(x2));
      node.setAttribute('y2', String(y2));
    }

    function setTransform(node, x, y, visible = true) {
      node.setAttribute('visibility', visible ? 'visible' : 'hidden');
      if (visible) node.setAttribute('transform', `translate(${x} ${y})`);
    }

    function setShape(points, visible = true) {
      shape.setAttribute('visibility', visible ? 'visible' : 'hidden');
      if (!visible) return;
      shape.setAttribute('points', points.map(([x, y]) => `${x},${y}`).join(' '));
    }

    function structureBarCount() {
      const all = allRows();
      if (!all.length || !state.leftTime || !state.rightTime) return 0;
      let left = nearestIndex(all, state.leftTime);
      let right = nearestIndex(all, state.rightTime);
      if (left < 0 || right < 0) return 0;
      if (left > right) [left, right] = [right, left];
      return right - left + 1;
    }

    function updateToolbar() {
      if (!state.active || state.phase === 'locked') {
        toolbar.classList.add('hidden');
        return;
      }
      toolbar.classList.remove('hidden');
      if (state.phase === 'choose-left') {
        info.innerHTML = `<b>⚓ ${formatPrice(state.galkaPrice)}</b> · двигай левую границу`;
        confirm.textContent = 'Левая ✓';
        confirm.disabled = state.submitting || !(state.leftTime > 0 && state.leftPrice > 0);
        return;
      }
      if (state.phase === 'choose-right') {
        const count = structureBarCount();
        info.innerHTML = `<b>⚓ ${formatPrice(state.galkaPrice)}</b> · двигай правую границу · ${count} свеч.`;
        confirm.textContent = 'Выставить лимитки';
        confirm.disabled = state.submitting || !(state.rightTime > 0 && state.rightPrice > 0) || count < 1;
      }
    }

    function render() {
      if (!state.active || state.coin !== selectedCoin()) {
        overlay.classList.add('hidden');
        toolbar.classList.add('hidden');
        return;
      }
      const geometry = chart.geometry?.();
      const anchorY = yForPrice(state.galkaPrice);
      if (!geometry || anchorY == null) {
        overlay.classList.add('hidden');
        updateToolbar();
        return;
      }

      overlay.classList.remove('hidden');
      overlay.setAttribute('viewBox', `0 0 ${geometry.width} ${geometry.height}`);
      const locked = state.phase === 'locked';
      setLine(fullLine, geometry.left, anchorY, geometry.right, anchorY, !locked);

      const anchorX = xForTime(state.anchorTime);
      const leftX = xForTime(state.leftTime);
      const rightX = xForTime(state.rightTime);
      const leftY = yForPrice(state.leftPrice);
      const rightY = yForPrice(state.rightPrice);

      setTransform(anchorHandle, anchorX ?? 0, anchorY, anchorX != null);
      setTransform(leftHandle, leftX ?? 0, leftY ?? 0, leftX != null && leftY != null);
      setTransform(rightHandle, rightX ?? 0, rightY ?? 0, rightX != null && rightY != null && state.phase !== 'choose-left');
      setLine(leftGuide, leftX ?? 0, geometry.top, leftX ?? 0, geometry.bottom, leftX != null && !locked);
      setLine(rightGuide, rightX ?? 0, geometry.top, rightX ?? 0, geometry.bottom, rightX != null && state.phase !== 'choose-left' && !locked);

      const shapePoints = [];
      if (leftX != null && leftY != null) shapePoints.push([leftX, leftY]);
      if (anchorX != null) shapePoints.push([anchorX, anchorY]);
      if (rightX != null && rightY != null && state.phase !== 'choose-left') shapePoints.push([rightX, rightY]);
      setShape(shapePoints, shapePoints.length >= 2);
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
      state.leftTime = 0;
      state.leftPrice = 0;
      state.rightTime = 0;
      state.rightPrice = 0;
      state.phase = 'idle';
      state.pointerId = null;
      state.submitting = false;
      state.startedAtMs = 0;
      state.campaignId = '';
      chart.crosshair = null;
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
      const anchorMs = Number(setup?.anchorTimeMs || 0);
      const leftMs = Number(setup?.leftBoundaryTimeMs || setup?.anchorTimeMs || 0);
      const rightMs = Number(setup?.rightBoundaryTimeMs || setup?.structureEndTimeMs || 0);
      if (!coin || coin !== selectedCoin() || !setup || setup.lockedForCampaign !== true || !(anchorMs > 0) || !(leftMs > 0) || !(rightMs > 0)) {
        if (state.phase === 'locked') resetState({ keepInput: true, restoreAction: false });
        return;
      }
      state.active = true;
      state.coin = coin;
      state.interval = String(setup.timeframe || '5m');
      state.galkaPrice = finite(campaign.galkaPrice || setup.galkaLevel);
      state.anchorTime = anchorMs / 1000;
      state.leftTime = leftMs / 1000;
      state.leftPrice = finite(setup.leftBoundaryPrice || state.galkaPrice);
      state.rightTime = rightMs / 1000;
      state.rightPrice = finite(setup.rightBoundaryPrice || state.galkaPrice);
      state.phase = 'locked';
      state.pointerId = null;
      state.submitting = false;
      state.startedAtMs = Number(setup.draftStartedAtMs || 0);
      state.campaignId = String(campaign.id || '');
      chart.crosshair = null;
      render();
    }

    function beginFromCrosshair(event) {
      const input = currentInput();
      const action = previewButton();
      if (!input || input.disabled || action?.dataset?.quickCancel === '1') return false;
      const galkaPrice = finite(event.detail?.price);
      if (!(galkaPrice > 0)) return false;
      const crosshairX = finite(chart.crosshair?.x, NaN);
      const anchorTime = Number(event.detail?.timeSec || (Number.isFinite(crosshairX) ? timeAtX(crosshairX) : 0));
      if (!(anchorTime > 0)) return false;

      state.active = true;
      state.coin = selectedCoin();
      state.interval = selectedInterval();
      state.galkaPrice = galkaPrice;
      state.anchorTime = anchorTime;
      state.leftTime = anchorTime;
      state.leftPrice = galkaPrice;
      state.rightTime = 0;
      state.rightPrice = 0;
      state.phase = 'choose-left';
      state.pointerId = null;
      state.submitting = false;
      state.startedAtMs = Date.now();
      state.campaignId = '';

      input.value = formatPrice(galkaPrice);
      if (action) {
        action.dataset.structureDraft = '1';
        action.disabled = true;
        action.textContent = 'Разметка GALKA';
      }
      render();
      if (navigator.vibrate) navigator.vibrate(12);
      return true;
    }

    function stopCanvasEvent(event) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }

    function updateActiveBoundary(point) {
      if (!point || point.zone !== 'plot') return;
      const time = timeAtX(point.x);
      const price = priceAtY(point.y);
      if (!(time > 0 && price > 0)) return;
      if (state.phase === 'choose-left') {
        state.leftTime = Math.min(time, state.anchorTime);
        state.leftPrice = price;
      } else if (state.phase === 'choose-right') {
        state.rightTime = Math.max(time, state.anchorTime);
        state.rightPrice = price;
      }
      chart.setCrosshair?.({ ...point, zone: 'plot' });
      chart.draw?.();
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
      state.pointerId = event.pointerId;
      updateActiveBoundary(point);
      try {
        canvas.setPointerCapture(event.pointerId);
      } catch (_) {
        // Pointer capture is a convenience only.
      }
      render();
    }, true);

    canvas.addEventListener('pointermove', (event) => {
      if (!state.active || state.phase === 'locked' || state.submitting || state.pointerId !== event.pointerId) return;
      const point = chart.pointFromEvent?.(event);
      if (!point || point.zone !== 'plot') return;
      stopCanvasEvent(event);
      updateActiveBoundary(point);
      render();
    }, true);

    function finishPointer(event) {
      if (!state.active || state.phase === 'locked' || state.pointerId !== event.pointerId) return;
      stopCanvasEvent(event);
      state.pointerId = null;
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
      if (!all.length || !state.leftTime || !state.rightTime) return null;
      let left = nearestIndex(all, state.leftTime);
      let right = nearestIndex(all, state.rightTime);
      if (left < 0 || right < 0) return null;
      if (left > right) [left, right] = [right, left];
      const anchor = nearestIndex(all, state.anchorTime);
      const totalBars = right - left + 1;
      const structureStart = totalBars > MAX_STRUCTURE_BARS ? right - MAX_STRUCTURE_BARS + 1 : left;
      return {
        all,
        left,
        right,
        anchor,
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
        schemaVersion: 2,
        selectionMethod: 'manual_crosshair_structure_v2',
        symbol: state.coin,
        timeframe: state.interval,
        galkaLevel: state.galkaPrice,
        anchorTimeMs: Math.round(state.anchorTime * 1000),
        anchorPrice: state.galkaPrice,
        leftBoundaryTimeMs: Math.round(state.leftTime * 1000),
        leftBoundaryPrice: state.leftPrice,
        rightBoundaryTimeMs: Math.round(state.rightTime * 1000),
        rightBoundaryPrice: state.rightPrice,
        structureStartTimeMs: Math.round(state.leftTime * 1000),
        structureEndTimeMs: Math.round(state.rightTime * 1000),
        selectedAtMs: Date.now(),
        draftStartedAtMs: state.startedAtMs,
        fullStructureBarCount: context.totalBars,
        structureBarsTruncated: context.structureBarsTruncated,
        anchorCandle: context.anchor >= 0 ? rowPayload(context.all[context.anchor]) : null,
        leftBoundaryCandle: rowPayload(context.all[context.left]),
        rightBoundaryCandle: rowPayload(context.all[context.right]),
        structureEndCandle: rowPayload(context.all[context.right]),
        preContextBars: context.preContextBars,
        structureBars: context.structureBars,
        postContextBarsAtPlacement: context.postContextBars,
        lockedForCampaign: true,
      };
    }

    function showToast(message, type) {
      const toast = document.getElementById('toast');
      if (!toast) return;
      toast.textContent = message;
      toast.className = `toast ${type || ''}`;
      setTimeout(() => toast.classList.add('hidden'), 4500);
    }

    async function submit() {
      if (!state.active || state.phase !== 'choose-right' || !state.rightTime || state.submitting) return;
      const token = sessionStorage.getItem('galkaLiveSession') || '';
      if (!token) {
        showToast('Открой терминал через защищённую ссылку из Termux', 'error');
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
        showToast('GALKA размечена, реальные лимитки выставлены', 'ok');
      } catch (error) {
        state.submitting = false;
        confirm.textContent = 'Выставить лимитки';
        confirm.disabled = false;
        updateToolbar();
        showToast(error.message || 'Не удалось выставить GALKA', 'error');
      }
    }

    function confirmStage() {
      if (!state.active || state.phase === 'locked' || state.submitting) return;
      if (state.phase === 'choose-left') {
        if (!(state.leftTime > 0 && state.leftPrice > 0)) return;
        state.phase = 'choose-right';
        state.rightTime = state.anchorTime;
        state.rightPrice = state.galkaPrice;
        render();
        if (navigator.vibrate) navigator.vibrate(10);
        return;
      }
      if (state.phase === 'choose-right') submit();
    }

    cancel.addEventListener('click', () => resetState());
    confirm.addEventListener('click', confirmStage);
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

    window.GalkaStructureDraft = {
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
        leftBoundaryTimeMs: state.leftTime * 1000,
        leftBoundaryPrice: state.leftPrice,
        rightBoundaryTimeMs: state.rightTime * 1000,
        rightBoundaryPrice: state.rightPrice,
        phase: state.phase,
      } : null,
    };
    return chart;
  }

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