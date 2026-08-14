(() => {
  'use strict';

  const charts = window.LightweightCharts;
  if (!charts?.createChart) return;

  const originalCreateChart = charts.createChart;

  function installPlotPan(chart) {
    const canvas = chart?.canvas;
    if (!canvas || typeof chart.currentPriceRange !== 'function' || typeof chart.visibleWindow !== 'function') {
      return;
    }

    let gesture = null;

    function currentRange() {
      const windowState = chart.visibleWindow?.();
      const rows = windowState?.rows || [];
      const fallbackRows = rows.length ? rows : (windowState?.all || []).slice(-1);
      const range = chart.currentPriceRange(fallbackRows);
      if (!range || !(Number(range.span) > 0)) return null;
      return {
        min: Number(range.min),
        max: Number(range.max),
        span: Number(range.span),
      };
    }

    function start(event) {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      const geometry = chart.geometry?.();
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      if (!geometry || x < geometry.left || x > geometry.right || y < geometry.top || y > geometry.bottom) {
        gesture = null;
        return;
      }
      const range = currentRange();
      if (!range) return;
      gesture = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        range,
        mode: 'pending',
      };
    }

    function move(event) {
      if (!gesture || gesture.pointerId !== event.pointerId) return;
      if ((chart.activePointers?.size || 0) >= 2) {
        gesture = null;
        return;
      }

      const dx = event.clientX - gesture.startX;
      const dy = event.clientY - gesture.startY;
      if (gesture.mode === 'pending') {
        if (Math.hypot(dx, dy) < 7) return;
        if (Math.abs(dy) > Math.abs(dx) * 0.85) {
          gesture.mode = 'vertical';
        } else {
          gesture.mode = 'horizontal';
          return;
        }
      }
      if (gesture.mode !== 'vertical') return;

      event.preventDefault();
      event.stopImmediatePropagation();

      const geometry = chart.geometry?.();
      const plotHeight = Math.max(1, Number(geometry?.plotHeight || canvas.clientHeight || 1));
      const shift = (dy / plotHeight) * gesture.range.span;
      chart.manualPriceRange = {
        min: gesture.range.min + shift,
        max: gesture.range.max + shift,
        span: gesture.range.span,
      };
      if (chart.gesture?.type === 'pan') chart.gesture.moved = true;
      chart.crosshair = null;
      chart.draw?.();
    }

    function finish(event) {
      if (gesture?.pointerId === event.pointerId) gesture = null;
    }

    // Capture phase is intentional: once a drag is clearly vertical, prevent the
    // built-in horizontal pan handler from consuming the same touch movement.
    canvas.addEventListener('pointerdown', start, { capture: true, passive: true });
    canvas.addEventListener('pointermove', move, { capture: true, passive: false });
    canvas.addEventListener('pointerup', finish, { capture: true, passive: true });
    canvas.addEventListener('pointercancel', finish, { capture: true, passive: true });
  }

  window.LightweightCharts = Object.freeze({
    ...charts,
    createChart(container, options) {
      const chart = originalCreateChart(container, options);
      installPlotPan(chart);
      return chart;
    },
  });
})();
