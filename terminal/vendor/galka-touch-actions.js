(() => {
  'use strict';

  const charts = window.LightweightCharts;
  if (!charts || typeof charts.createChart !== 'function') return;

  const originalCreateChart = charts.createChart.bind(charts);
  const HOLD_MS = 450;
  const MOVE_SLOP = 9;
  const DOUBLE_TAP_MS = 320;

  function installTouchActions(chart, container) {
    if (!chart?.canvas || typeof chart.pointFromEvent !== 'function') return;

    const canvas = chart.canvas;
    canvas.setAttribute(
      'aria-label',
      'График свечей. Одним пальцем двигай график. Удерживай палец, чтобы включить перекрестие. После этого касанием двигай перекрестие, а кнопкой плюс выбирай цену GALKA.',
    );

    const svgNs = 'http://www.w3.org/2000/svg';
    const overlay = document.createElementNS(svgNs, 'svg');
    overlay.classList.add('galka-touch-overlay');
    overlay.setAttribute('preserveAspectRatio', 'none');

    const plus = document.createElementNS(svgNs, 'g');
    plus.classList.add('galka-touch-plus', 'hidden');
    plus.setAttribute('role', 'button');
    plus.setAttribute('tabindex', '0');
    plus.setAttribute('aria-label', 'Поставить GALKA по цене перекрестия');

    const plusCircle = document.createElementNS(svgNs, 'circle');
    plusCircle.classList.add('galka-touch-plus-circle');
    plusCircle.setAttribute('r', '19');
    const plusText = document.createElementNS(svgNs, 'text');
    plusText.classList.add('galka-touch-plus-text');
    plusText.setAttribute('x', '0');
    plusText.setAttribute('y', '1');
    plusText.textContent = '+';
    plus.append(plusCircle, plusText);
    overlay.append(plus);
    container.append(overlay);

    const state = {
      pointers: new Map(),
      gesture: null,
      holdTimer: null,
      crosshairPinned: false,
      lastTapAt: 0,
    };

    function clearHold() {
      clearTimeout(state.holdTimer);
      state.holdTimer = null;
    }

    function plotPointers() {
      return [...state.pointers.values()].filter((point) => point.zone === 'plot');
    }

    function currentCrosshairPrice() {
      if (!chart.crosshair) return null;
      const geometry = chart.geometry();
      const range = chart.currentPriceRange(chart.lastRows);
      if (!range || !(range.span > 0)) return null;
      const y = Math.max(geometry.top, Math.min(geometry.bottom, chart.crosshair.y));
      return range.max - ((y - geometry.top) / geometry.plotHeight) * range.span;
    }

    function positionPlus() {
      const input = document.getElementById('galkaInput');
      if (!state.crosshairPinned || !chart.crosshair || input?.disabled) {
        plus.classList.add('hidden');
        return;
      }
      const geometry = chart.geometry();
      const x = Math.max(geometry.left + 18, geometry.right - 22);
      const y = Math.max(geometry.top + 18, Math.min(geometry.bottom - 18, chart.crosshair.y));
      overlay.setAttribute('viewBox', `0 0 ${geometry.width} ${geometry.height}`);
      plus.setAttribute('transform', `translate(${x} ${y})`);
      plus.classList.remove('hidden');
    }

    function redraw() {
      chart.draw();
      positionPlus();
    }

    function setPinnedCrosshair(point) {
      state.crosshairPinned = true;
      chart.setCrosshair(point);
      chart.gesture = null;
      chart.setInteractionClass('plot');
      if (navigator.vibrate) navigator.vibrate(12);
      redraw();
    }

    function clearPinnedCrosshair() {
      clearHold();
      state.crosshairPinned = false;
      chart.crosshair = null;
      chart.gesture = null;
      chart.setInteractionClass('plot');
      redraw();
    }

    function startPinch() {
      clearHold();
      const points = plotPointers();
      if (points.length < 2) return;
      chart.crosshair = state.crosshairPinned ? chart.crosshair : null;
      chart.startPinch(points[0], points[1]);
      state.gesture = { type: 'pinch' };
      redraw();
    }

    function updatePinch() {
      const points = plotPointers();
      if (points.length < 2) return;
      if (state.gesture?.type !== 'pinch') startPinch();
      chart.updatePinch(points[0], points[1]);
      positionPlus();
    }

    function startTouch(event, point) {
      if (point.zone === 'price') {
        clearHold();
        chart.crosshair = null;
        chart.startPriceScale(point);
        state.gesture = { type: 'price' };
        plus.classList.add('hidden');
        return;
      }
      if (point.zone === 'time') {
        clearHold();
        chart.crosshair = null;
        chart.startTimeScale(point);
        state.gesture = { type: 'time' };
        plus.classList.add('hidden');
        return;
      }

      if (state.crosshairPinned) {
        chart.setCrosshair(point);
        state.gesture = {
          type: 'crosshair',
          pointerId: event.pointerId,
          startX: point.x,
          startY: point.y,
          moved: false,
        };
        redraw();
        return;
      }

      chart.crosshair = null;
      state.gesture = {
        type: 'pending',
        pointerId: event.pointerId,
        startX: point.x,
        startY: point.y,
        startOffset: chart.panOffset,
        latestPoint: point,
        moved: false,
      };
      clearHold();
      state.holdTimer = setTimeout(() => {
        if (state.gesture?.type !== 'pending' || state.gesture.pointerId !== event.pointerId) return;
        setPinnedCrosshair(state.gesture.latestPoint);
        state.gesture = {
          type: 'crosshair',
          pointerId: event.pointerId,
          startX: state.gesture.latestPoint.x,
          startY: state.gesture.latestPoint.y,
          moved: false,
          activatedByHold: true,
        };
      }, HOLD_MS);
    }

    function onPointerDown(event) {
      if (event.pointerType !== 'touch') return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const point = chart.pointFromEvent(event);
      state.pointers.set(event.pointerId, point);
      canvas.setPointerCapture(event.pointerId);
      canvas.focus({ preventScroll: true });

      if (plotPointers().length >= 2) {
        startPinch();
        return;
      }
      startTouch(event, point);
    }

    function onPointerMove(event) {
      if (event.pointerType !== 'touch' || !state.pointers.has(event.pointerId)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const point = chart.pointFromEvent(event);
      state.pointers.set(event.pointerId, point);

      if (plotPointers().length >= 2) {
        updatePinch();
        return;
      }

      const gesture = state.gesture;
      if (!gesture) return;
      if (gesture.type === 'pending') {
        gesture.latestPoint = point;
        const dx = point.x - gesture.startX;
        const dy = point.y - gesture.startY;
        if (Math.hypot(dx, dy) <= MOVE_SLOP) return;
        clearHold();
        gesture.type = 'pan';
        gesture.moved = true;
        chart.setInteractionClass('plot', 'pan');
      }

      if (gesture.type === 'pan') {
        const geometry = chart.geometry();
        const deltaX = point.x - gesture.startX;
        const draggedBars = deltaX / geometry.plotWidth * chart.visibleCount;
        chart.panOffset = gesture.startOffset + draggedBars;
        chart.clampPanOffset();
        chart.crosshair = null;
        redraw();
      } else if (gesture.type === 'crosshair') {
        if (Math.hypot(point.x - gesture.startX, point.y - gesture.startY) > MOVE_SLOP) {
          gesture.moved = true;
        }
        chart.setCrosshair(point);
        redraw();
      } else if (gesture.type === 'price') {
        chart.updatePriceScale(point);
      } else if (gesture.type === 'time') {
        chart.updateTimeScale(point);
      }
    }

    function onPointerEnd(event) {
      if (event.pointerType !== 'touch' || !state.pointers.has(event.pointerId)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const point = state.pointers.get(event.pointerId);
      const finished = state.gesture;
      state.pointers.delete(event.pointerId);
      clearHold();
      try {
        if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
      } catch (_) {
        // Android can release pointer capture before pointercancel arrives.
      }

      if (finished?.type === 'crosshair' && point?.zone === 'plot') {
        if (!finished.moved && !finished.activatedByHold) {
          const now = performance.now();
          if (now - state.lastTapAt <= DOUBLE_TAP_MS) {
            state.lastTapAt = 0;
            clearPinnedCrosshair();
            return;
          }
          state.lastTapAt = now;
        }
        chart.setCrosshair(point);
      }

      if (state.pointers.size >= 2) {
        startPinch();
        return;
      }
      chart.gesture = null;
      state.gesture = null;
      chart.setInteractionClass(point?.zone || 'plot');
      redraw();
    }

    function onPointerLeave(event) {
      if (event.pointerType !== 'touch' || !state.crosshairPinned) return;
      event.stopImmediatePropagation();
      positionPlus();
    }

    function selectCrosshairPrice() {
      const price = currentCrosshairPrice();
      if (!(Number(price) > 0)) return;
      canvas.dispatchEvent(new CustomEvent('galka:select-price', {
        detail: { price: Number(price), source: 'touch-plus' },
      }));
      clearPinnedCrosshair();
    }

    plus.addEventListener('pointerdown', (event) => {
      event.stopPropagation();
    });
    plus.addEventListener('click', selectCrosshairPrice);
    plus.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      selectCrosshairPrice();
    });

    canvas.addEventListener('pointerdown', onPointerDown, { capture: true, passive: false });
    canvas.addEventListener('pointermove', onPointerMove, { capture: true, passive: false });
    canvas.addEventListener('pointerup', onPointerEnd, { capture: true, passive: false });
    canvas.addEventListener('pointercancel', onPointerEnd, { capture: true, passive: false });
    canvas.addEventListener('pointerleave', onPointerLeave, { capture: true });

    new ResizeObserver(positionPlus).observe(container);
  }

  window.LightweightCharts = Object.freeze({
    ...charts,
    createChart(container, options) {
      const chart = originalCreateChart(container, options);
      installTouchActions(chart, container);
      return chart;
    },
  });
})();
