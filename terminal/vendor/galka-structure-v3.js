(() => {
  'use strict';

  const charts = window.LightweightCharts;
  if (!charts?.createChart) return;

  const originalCreateChart = charts.createChart.bind(charts);
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const MAX_STRUCTURE_BARS = 240;
  const CONTEXT_BARS = 20;
  const MIN_SIDE_OFFSET_SEC = 1;

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
    return number > 0 ? number.toFixed(2) : '—';
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

  function install(chart, container) {
    if (!chart?.canvas || chart.__galkaStructureV3Installed) return chart;
    chart.__galkaStructureV3Installed = true;

    const canvas = chart.canvas;
    const overlay = createSvg('svg', 'galka-structure-overlay galka-structure-v3-overlay hidden');
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
    toolbar.className = 'galka-structure-toolbar galka-structure-v3-toolbar hidden';
    const info = document.createElement('div');
    info.className = 'galka-structure-info';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.textContent = 'Отмена';
    const confirm = document.createElement('button');
    confirm.type = 'button';
    confirm.className = 'galka-structure-confirm galka-structure-v3-confirm';
    confirm.textContent = 'Якорь ✓';
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
      anchorSelectedAtMs: 0,
      leftSelectedAtMs: 0,
      rightSelectedAtMs: 0,
      leftTouched: false,
      rightTouched: false,
    };

    function selectedCoin() {
      return String(document.getElementById('symbolSelect')?.value || 'BTC').toUpperCase();
    }

    function selectedInterval() {
      return String(document.getElementById('intervalSelect')?.value || '5m');
    }

    function currentInput() {
      return document.getElementById('galkaInput');
    }

    function previewButton() {
      return document.getElementById('previewButton');
    }

    function allRows() {
      return Array.isArray(chart.series?.data) ? chart.series.data : [];
    }

    function snapToCandle(timeSec) {
      const all = allRows();
      const index = nearestIndex(all, timeSec);
      return index >= 0 ? Number(all[index]?.time || 0) : Number(timeSec || 0);
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

    function updateToolbar() {
      if (!state.active) {
        toolbar.classList.add('hidden');
        return;
      }
      toolbar.classList.remove('hidden');
      if (state.phase === 'choose-anchor') {
        info.innerHTML = `<b>⚓ ${formatPrice(state.galkaPrice)}</b> · двигай якорь только влево/вправо`;
        confirm.textContent = 'Якорь ✓';
        confirm.disabled = state.submitting || !(state.anchorTime > 0);
      } else if (state.phase === 'choose-left') {
        info.innerHTML = `<b>⚓ ${formatPrice(state.galkaPrice)}</b> · поставь левую сторону GALKA`;
        confirm.textContent = 'Левая ✓';
        confirm.disabled = state.submitting || !state.leftTouched;
      } else if (state.phase === 'choose-right') {
        info.innerHTML = `<b>⚓ ${formatPrice(state.galkaPrice)}</b> · поставь правую сторону GALKA`;
        confirm.textContent = 'Выставить лимитки';
        confirm.disabled = state.submitting || !state.rightTouched;
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
      if (!geometry || anchorY == null) return;

      overlay.classList.remove('hidden');
      overlay.setAttribute('viewBox', `0 0 ${geometry.width} ${geometry.height}`);
      setLine(fullLine, geometry.left, anchorY, geometry.right, anchorY, true);

      const anchorX = xForTime(state.anchorTime);
      const leftX = xForTime(state.leftTime);
      const rightX = xForTime(state.rightTime);
      const leftY = yForPrice(state.leftPrice);
      const rightY = yForPrice(state.rightPrice);
      const showLeft = state.phase === 'choose-left' || state.phase === 'choose-right';
      const showRight = state.phase === 'choose-right';

      setTransform(anchorHandle, anchorX ?? 0, anchorY, anchorX != null);
      setTransform(leftHandle, leftX ?? 0, leftY ?? 0, showLeft && leftX != null && leftY != null);
      setTransform(rightHandle, rightX ?? 0, rightY ?? 0, showRight && rightX != null && rightY != null);
      setLine(leftGuide, leftX ?? 0, geometry.top, leftX ?? 0, geometry.bottom, showLeft && leftX != null);
      setLine(rightGuide, rightX ?? 0, geometry.top, rightX ?? 0, geometry.bottom, showRight && rightX != null);

      const points = [];
      if (showLeft && leftX != null && leftY != null) points.push([leftX, leftY]);
      if (anchorX != null) points.push([anchorX, anchorY]);
      if (showRight && rightX != null && rightY != null) points.push([rightX, rightY]);
      setShape(points, points.length >= 2);
      updateToolbar();
    }

    function resetState({ keepInput = false } = {}) {
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
      state.anchorSelectedAtMs = 0;
      state.leftSelectedAtMs = 0;
      state.rightSelectedAtMs = 0;
      state.leftTouched = false;
      state.rightTouched = false;
      overlay.classList.add('hidden');
      toolbar.classList.add('hidden');
      chart.crosshair = null;
      if (!keepInput) {
        const input = currentInput();
        if (input && !input.disabled) input.value = '';
      }
      const action = previewButton();
      if (action && !currentInput()?.disabled) {
        action.disabled = false;
        action.textContent = 'Поставить GALKA';
      }
      chart.draw?.();
    }

    function beginFromSelection(event) {
      const input = currentInput();
      const action = previewButton();
      if (!input || input.disabled || action?.dataset?.quickCancel === '1') return false;
      const galkaPrice = finite(event.detail?.price);
      if (!(galkaPrice > 0)) return false;

      const crosshairX = finite(chart.crosshair?.x, NaN);
      const rawTime = Number(event.detail?.timeSec || (Number.isFinite(crosshairX) ? timeAtX(crosshairX) : 0));
      if (!(rawTime > 0)) return false;

      const anchorTime = snapToCandle(rawTime);
      if (!(anchorTime > 0)) return false;

      state.active = true;
      state.coin = selectedCoin();
      state.interval = selectedInterval();
      state.galkaPrice = galkaPrice;
      state.anchorTime = anchorTime;
      state.leftTime = 0;
      state.leftPrice = 0;
      state.rightTime = 0;
      state.rightPrice = 0;
      state.phase = 'choose-anchor';
      state.pointerId = null;
      state.submitting = false;
      state.startedAtMs = Date.now();
      state.leftTouched = false;
      state.rightTouched = false;

      input.value = formatPrice(galkaPrice);
      if (action) {
        action.disabled = true;
        action.textContent = 'Разметка GALKA';
      }
      chart.crosshair = null;
      render();
      if (navigator.vibrate) navigator.vibrate(12);
      return true;
    }

    function pointFromEvent(event) {
      const point = chart.pointFromEvent?.(event);
      return point?.zone === 'plot' ? point : null;
    }

    function updateActivePoint(point) {
      if (!point) return;
      const geometry = chart.geometry?.();
      if (!geometry) return;
      if (state.phase === 'choose-anchor') {
        const snapped = snapToCandle(timeAtX(point.x));
        if (snapped > 0) state.anchorTime = snapped;
        const anchorY = yForPrice(state.galkaPrice);
        chart.setCrosshair?.({ ...point, x: xForTime(state.anchorTime) ?? point.x, y: anchorY ?? point.y, zone: 'plot' });
      } else if (state.phase === 'choose-left') {
        const time = Math.min(timeAtX(point.x), state.anchorTime - MIN_SIDE_OFFSET_SEC);
        const price = priceAtY(point.y);
        if (time > 0 && price > 0) {
          state.leftTime = time;
          state.leftPrice = price;
          state.leftTouched = true;
          chart.setCrosshair?.({ ...point, zone: 'plot' });
        }
      } else if (state.phase === 'choose-right') {
        const time = Math.max(timeAtX(point.x), state.anchorTime + MIN_SIDE_OFFSET_SEC);
        const price = priceAtY(point.y);
        if (time > 0 && price > 0) {
          state.rightTime = time;
          state.rightPrice = price;
          state.rightTouched = true;
          chart.setCrosshair?.({ ...point, zone: 'plot' });
        }
      }
      chart.draw?.();
      render();
    }

    function eventHitsCanvas(event) {
      return event.target === canvas || event.composedPath?.().includes(canvas);
    }

    function stopInteraction(event) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }

    function onSelection(event) {
      if (state.active) return;
      if (!beginFromSelection(event)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }

    function onPointerDown(event) {
      if (!state.active || state.submitting || !eventHitsCanvas(event)) return;
      const point = pointFromEvent(event);
      if (!point) return;
      stopInteraction(event);
      state.pointerId = event.pointerId;
      updateActivePoint(point);
    }

    function onPointerMove(event) {
      if (!state.active || state.submitting || state.pointerId !== event.pointerId || !eventHitsCanvas(event)) return;
      const point = pointFromEvent(event);
      if (!point) return;
      stopInteraction(event);
      updateActivePoint(point);
    }

    function onPointerEnd(event) {
      if (!state.active || state.pointerId !== event.pointerId) return;
      if (eventHitsCanvas(event)) stopInteraction(event);
      state.pointerId = null;
      chart.crosshair = null;
      chart.draw?.();
      render();
      if (navigator.vibrate) navigator.vibrate(8);
    }

    window.addEventListener('galka:select-price', onSelection, { capture: true });
    window.addEventListener('pointerdown', onPointerDown, { capture: true, passive: false });
    window.addEventListener('pointermove', onPointerMove, { capture: true, passive: false });
    window.addEventListener('pointerup', onPointerEnd, { capture: true, passive: false });
    window.addEventListener('pointercancel', onPointerEnd, { capture: true, passive: false });

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
      if (!(state.leftTime < state.anchorTime && state.anchorTime < state.rightTime)) {
        throw new Error('Границы GALKA должны быть по разные стороны от якоря');
      }
      return {
        schemaVersion: 2,
        selectionMethod: 'manual_crosshair_structure_v3',
        anchorPlacementMethod: 'manual_horizontal_snap_to_candle',
        boundaryPlacementMethod: 'manual_free_xy',
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
        anchorSelectedAtMs: state.anchorSelectedAtMs,
        leftSelectedAtMs: state.leftSelectedAtMs,
        rightSelectedAtMs: state.rightSelectedAtMs,
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

    function showToast(message, type = '') {
      const toast = document.getElementById('toast');
      if (!toast) return;
      toast.textContent = message;
      toast.className = `toast ${type}`;
      setTimeout(() => toast.classList.add('hidden'), 4500);
    }

    async function submit() {
      if (!state.active || state.phase !== 'choose-right' || !state.rightTouched || state.submitting) return;
      const token = sessionStorage.getItem('galkaLiveSession') || '';
      if (!token) {
        showToast('Открой терминал через защищённую ссылку из Termux', 'error');
        return;
      }
      state.submitting = true;
      confirm.disabled = true;
      confirm.textContent = 'Отправка…';

      try {
        state.rightSelectedAtMs = Date.now();
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
        if (!response.ok || payload?.ok === false) throw new Error(payload?.error || `HTTP ${response.status}`);
        resetState({ keepInput: true });
        document.dispatchEvent(new CustomEvent('galka:structure-committed', {
          detail: { campaign: payload.data, researchSetup },
        }));
        showToast('GALKA размечена, реальные лимитки выставлены', 'ok');
      } catch (error) {
        state.submitting = false;
        confirm.textContent = 'Выставить лимитки';
        confirm.disabled = false;
        showToast(error.message || 'Не удалось выставить GALKA', 'error');
        updateToolbar();
      }
    }

    function confirmStage() {
      if (!state.active || state.submitting) return;
      const all = allRows();
      const interval = estimateInterval(all);
      if (state.phase === 'choose-anchor') {
        state.anchorSelectedAtMs = Date.now();
        state.phase = 'choose-left';
        state.leftTime = state.anchorTime - interval;
        state.leftPrice = state.galkaPrice;
        state.leftTouched = false;
      } else if (state.phase === 'choose-left') {
        if (!state.leftTouched) return;
        state.leftSelectedAtMs = Date.now();
        state.phase = 'choose-right';
        state.rightTime = state.anchorTime + interval;
        state.rightPrice = state.galkaPrice;
        state.rightTouched = false;
      } else if (state.phase === 'choose-right') {
        submit();
        return;
      }
      chart.crosshair = null;
      chart.draw?.();
      render();
      if (navigator.vibrate) navigator.vibrate(10);
    }

    cancel.addEventListener('click', () => resetState());
    confirm.addEventListener('click', confirmStage);
    document.getElementById('symbolSelect')?.addEventListener('change', () => {
      if (state.active) resetState({ keepInput: true });
    });
    document.getElementById('intervalSelect')?.addEventListener('change', () => {
      if (state.active) resetState();
    });

    const originalDraw = chart.draw.bind(chart);
    chart.draw = (...args) => {
      const result = originalDraw(...args);
      requestAnimationFrame(render);
      return result;
    };

    window.GalkaStructureV3 = {
      active: () => state.active,
      cancel: resetState,
      snapshot: () => state.active ? {
        coin: state.coin,
        timeframe: state.interval,
        galkaLevel: state.galkaPrice,
        anchorTimeMs: state.anchorTime * 1000,
        leftBoundaryTimeMs: state.leftTime * 1000,
        leftBoundaryPrice: state.leftPrice,
        rightBoundaryTimeMs: state.rightTime * 1000,
        rightBoundaryPrice: state.rightPrice,
        leftTouched: state.leftTouched,
        rightTouched: state.rightTouched,
        phase: state.phase,
      } : null,
    };

    return chart;
  }

  window.LightweightCharts = Object.freeze({
    ...charts,
    createChart(container, options) {
      return install(originalCreateChart(container, options), container);
    },
  });
})();
