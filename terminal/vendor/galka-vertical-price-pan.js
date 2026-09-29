(() => {
  'use strict';

  const charts = window.LightweightCharts;
  if (!charts || typeof charts.createChart !== 'function') return;

  const originalCreateChart = charts.createChart.bind(charts);

  function installVerticalPan(chart, container, state) {
    const pointers = new Map();
    let gesture = null;
    let lastTapAt = 0;

    const resetButton = document.createElement('button');
    resetButton.type = 'button';
    resetButton.className = 'vertical-price-reset hidden';
    resetButton.textContent = 'К цене';
    resetButton.setAttribute('aria-label', 'Вернуть график к автоматическому центру');
    container.parentElement?.appendChild(resetButton);

    const requestAutoscale = () => {
      if (!state.series) return;
      state.series.applyOptions({ autoscaleInfoProvider: state.provider });
      resetButton.classList.toggle('hidden', Math.abs(state.offsetRatio) < 0.001);
    };

    const reset = () => {
      state.offsetRatio = 0;
      requestAutoscale();
    };

    resetButton.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      reset();
    });

    container.addEventListener('pointerdown', (event) => {
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (pointers.size !== 1) {
        gesture = null;
        return;
      }
      gesture = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        startOffset: state.offsetRatio,
        vertical: false,
      };
    }, { capture: true, passive: true });

    container.addEventListener('pointermove', (event) => {
      if (!pointers.has(event.pointerId)) return;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (!gesture || gesture.pointerId !== event.pointerId || pointers.size !== 1) return;

      const dx = event.clientX - gesture.startX;
      const dy = event.clientY - gesture.startY;
      if (!gesture.vertical) {
        if (Math.hypot(dx, dy) < 9) return;
        if (Math.abs(dy) <= Math.abs(dx) * 1.2) {
          gesture = null;
          return;
        }
        gesture.vertical = true;
        try {
          container.setPointerCapture(event.pointerId);
        } catch (_) {
          // Pointer capture is optional on older Android WebViews.
        }
      }

      event.preventDefault();
      event.stopImmediatePropagation();

      const height = Math.max(1, container.getBoundingClientRect().height);
      const nextOffset = gesture.startOffset + (dy / height) * 1.35;
      state.offsetRatio = Math.max(-4, Math.min(4, nextOffset));
      requestAutoscale();
    }, { capture: true, passive: false });

    const finishPointer = (event) => {
      const wasVertical = !!gesture?.vertical && gesture.pointerId === event.pointerId;
      pointers.delete(event.pointerId);
      if (gesture?.pointerId === event.pointerId) gesture = null;
      if (wasVertical) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }

      if (pointers.size === 0 && event.pointerType === 'touch') {
        const now = Date.now();
        if (now - lastTapAt < 320) {
          reset();
          lastTapAt = 0;
        } else {
          lastTapAt = now;
        }
      }
    };

    container.addEventListener('pointerup', finishPointer, { capture: true, passive: false });
    container.addEventListener('pointercancel', finishPointer, { capture: true, passive: false });

    window.addEventListener('galka:price-center', reset);
  }

  charts.createChart = function patchedCreateChart(container, options) {
    const chart = originalCreateChart(container, options);
    const state = {
      series: null,
      offsetRatio: 0,
      provider: null,
    };

    state.provider = (baseImplementation) => {
      const base = baseImplementation();
      const range = base?.priceRange;
      if (!range || !Number.isFinite(range.minValue) || !Number.isFinite(range.maxValue)) {
        return base;
      }
      const span = range.maxValue - range.minValue;
      if (!(span > 0) || Math.abs(state.offsetRatio) < 0.001) return base;
      const shift = span * state.offsetRatio;
      return {
        ...base,
        priceRange: {
          minValue: range.minValue - shift,
          maxValue: range.maxValue - shift,
        },
      };
    };

    const originalAddSeries = chart.addSeries.bind(chart);
    chart.addSeries = function patchedAddSeries(definition, seriesOptions = {}) {
      const series = originalAddSeries(definition, {
        ...seriesOptions,
        autoscaleInfoProvider: state.provider,
      });
      if (!state.series) state.series = series;
      return series;
    };

    installVerticalPan(chart, container, state);
    return chart;
  };
})();
