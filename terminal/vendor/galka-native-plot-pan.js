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

    const pointers = new Map();
    let gesture = null;

    function start(event) {
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (pointers.size !== 1) {
        gesture = null;
        return;
      }

      const windowState = chart.visibleWindow();
      const rows = windowState?.rows || [];
      const fallbackRows = rows.length ? rows : (windowState?.all || []).slice(-1);
      const range = chart.currentPriceRange(fallbackRows);
      if (!range || !(Number(range.span) > 0)) return;

      gesture = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        range: {
          min: Number(range.min),
          max: Number(range.max),
          span: Number(range.span),
        },
        verticalActive: false,
      };
    }

    function move(event) {
      if (!pointers.has(event.pointerId)) return;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (!gesture || gesture.pointerId !== event.pointerId || pointers.size !== 1) return;

      const dx = event.clientX - gesture.startX;
      const dy = event.clientY - gesture.startY;
      if (!gesture.verticalActive) {
        if (Math.abs(dy) < 6) return;
        gesture.verticalActive = true;
      }

      const geometry = chart.geometry?.();
      const plotHeight = Math.max(1, Number(geometry?.plotHeight || canvas.clientHeight || 1));
      const shift = (dy / plotHeight) * gesture.range.span;
      chart.manualPriceRange = {
        min: gesture.range.min + shift,
        max: gesture.range.max + shift,
        span: gesture.range.span,
      };

      if (chart.gesture?.type === 'pan') chart.gesture.moved = true;
      chart.draw?.();
    }

    function finish(event) {
      pointers.delete(event.pointerId);
      if (gesture?.pointerId === event.pointerId) gesture = null;
      if (pointers.size !== 1) return;
      // Do not auto-start a one-finger pan after a pinch; require a fresh touch.
    }

    canvas.addEventListener('pointerdown', start, { passive: true });
    canvas.addEventListener('pointermove', move, { passive: true });
    canvas.addEventListener('pointerup', finish, { passive: true });
    canvas.addEventListener('pointercancel', finish, { passive: true });
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
