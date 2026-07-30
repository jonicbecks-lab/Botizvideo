(() => {
  'use strict';

  const charts = window.LightweightCharts;
  if (!charts || typeof charts.createChart !== 'function') return;

  const originalCreateChart = charts.createChart.bind(charts);

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function installRelativeCrosshair(chart) {
    if (!chart?.canvas || typeof chart.setCrosshair !== 'function' || chart.__galkaRelativeCrosshairInstalled) {
      return chart;
    }
    chart.__galkaRelativeCrosshairInstalled = true;

    const canvas = chart.canvas;
    const originalSetCrosshair = chart.setCrosshair.bind(chart);
    const touchPointers = new Set();
    let relativeGesture = null;
    let suppressReleaseSnap = false;

    chart.setCrosshair = function setRelativeCrosshair(point) {
      if (suppressReleaseSnap && point?.zone === 'plot') return;

      if (
        relativeGesture &&
        point?.zone === 'plot' &&
        touchPointers.has(relativeGesture.pointerId)
      ) {
        const geometry = chart.geometry();
        const adjusted = {
          ...point,
          x: clamp(
            relativeGesture.crosshairStartX + point.x - relativeGesture.touchStartX,
            geometry.left,
            geometry.right,
          ),
          y: clamp(
            relativeGesture.crosshairStartY + point.y - relativeGesture.touchStartY,
            geometry.top,
            geometry.bottom,
          ),
          zone: 'plot',
        };
        originalSetCrosshair(adjusted);
        return;
      }
      originalSetCrosshair(point);
    };

    canvas.addEventListener('pointerdown', (event) => {
      if (event.pointerType !== 'touch') return;
      touchPointers.add(event.pointerId);
      const point = chart.pointFromEvent(event);
      if (touchPointers.size === 1 && point.zone === 'plot' && chart.crosshair) {
        relativeGesture = {
          pointerId: event.pointerId,
          touchStartX: point.x,
          touchStartY: point.y,
          crosshairStartX: chart.crosshair.x,
          crosshairStartY: chart.crosshair.y,
        };
      } else if (touchPointers.size > 1) {
        relativeGesture = null;
      }
    }, { capture: true, passive: true });

    function finishPointer(event) {
      if (event.pointerType !== 'touch') return;
      const endingRelativeGesture = relativeGesture?.pointerId === event.pointerId;
      const pinnedAtRelease = endingRelativeGesture && chart.crosshair
        ? { x: chart.crosshair.x, y: chart.crosshair.y }
        : null;

      if (endingRelativeGesture) suppressReleaseSnap = true;
      touchPointers.delete(event.pointerId);

      queueMicrotask(() => {
        if (endingRelativeGesture) {
          suppressReleaseSnap = false;
          relativeGesture = null;
        }
        if (!pinnedAtRelease) return;
        chart.crosshair = pinnedAtRelease;
        chart.draw();
      });
    }

    canvas.addEventListener('pointerup', finishPointer, { capture: true, passive: true });
    canvas.addEventListener('pointercancel', finishPointer, { capture: true, passive: true });
    canvas.addEventListener('lostpointercapture', finishPointer, { capture: true, passive: true });

    return chart;
  }

  window.LightweightCharts = Object.freeze({
    ...charts,
    createChart(container, options) {
      return installRelativeCrosshair(originalCreateChart(container, options));
    },
  });
})();
