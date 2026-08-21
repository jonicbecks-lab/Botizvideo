(() => {
  'use strict';

  const MOVE_SLOP = 3;
  let forwarding = false;
  let gesture = null;

  function snapshot() {
    try {
      return window.GalkaStructureV3?.snapshot?.() || null;
    } catch (_) {
      return null;
    }
  }

  function activeBoundaryPhase(state) {
    return state?.phase === 'choose-left' || state?.phase === 'choose-right';
  }

  function chartCanvas() {
    return document.querySelector('#chart canvas');
  }

  function handleForPhase(phase) {
    const selector = phase === 'choose-left'
      ? '.galka-structure-v3-overlay .galka-structure-boundary-left'
      : '.galka-structure-v3-overlay .galka-structure-boundary-right';
    return document.querySelector(selector);
  }

  function parseTranslate(node) {
    const value = String(node?.getAttribute?.('transform') || '');
    const match = value.match(/translate\(\s*(-?[0-9.]+)[ ,]+(-?[0-9.]+)\s*\)/);
    if (!match) return null;
    const x = Number(match[1]);
    const y = Number(match[2]);
    return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
  }

  function handleClientPoint(phase) {
    const handle = handleForPhase(phase);
    const overlay = handle?.closest?.('svg');
    const local = parseTranslate(handle);
    if (!overlay || !local) return null;

    const rect = overlay.getBoundingClientRect();
    const viewBox = overlay.viewBox?.baseVal;
    const width = Number(viewBox?.width || rect.width || 1);
    const height = Number(viewBox?.height || rect.height || 1);
    if (!(rect.width > 0 && rect.height > 0 && width > 0 && height > 0)) return null;

    return {
      x: rect.left + ((local.x - Number(viewBox?.x || 0)) / width) * rect.width,
      y: rect.top + ((local.y - Number(viewBox?.y || 0)) / height) * rect.height,
    };
  }

  function eventHitsCanvas(event, canvas) {
    return event.target === canvas || event.composedPath?.().includes(canvas);
  }

  function stop(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }

  function dispatchSynthetic(type, source, clientX, clientY, buttons) {
    const canvas = chartCanvas();
    if (!canvas || typeof PointerEvent !== 'function') return false;

    forwarding = true;
    try {
      canvas.dispatchEvent(new PointerEvent(type, {
        bubbles: true,
        cancelable: true,
        composed: true,
        pointerId: source.pointerId,
        pointerType: source.pointerType || 'touch',
        isPrimary: source.isPrimary !== false,
        button: type === 'pointerdown' ? 0 : -1,
        buttons,
        pressure: buttons ? 0.5 : 0,
        clientX,
        clientY,
        screenX: source.screenX,
        screenY: source.screenY,
        ctrlKey: source.ctrlKey,
        shiftKey: source.shiftKey,
        altKey: source.altKey,
        metaKey: source.metaKey,
      }));
      return true;
    } finally {
      forwarding = false;
    }
  }

  function onPointerDown(event) {
    if (forwarding || gesture) return;
    const state = snapshot();
    if (!activeBoundaryPhase(state)) return;

    const canvas = chartCanvas();
    if (!canvas || !eventHitsCanvas(event, canvas)) return;
    const origin = handleClientPoint(state.phase);
    if (!origin) return;

    stop(event);
    gesture = {
      pointerId: event.pointerId,
      phase: state.phase,
      startX: event.clientX,
      startY: event.clientY,
      originX: origin.x,
      originY: origin.y,
      currentX: origin.x,
      currentY: origin.y,
      started: false,
      source: event,
    };
  }

  function onPointerMove(event) {
    if (forwarding || !gesture || gesture.pointerId !== event.pointerId) return;
    stop(event);

    const state = snapshot();
    if (!activeBoundaryPhase(state) || state.phase !== gesture.phase) {
      gesture = null;
      return;
    }

    const dx = event.clientX - gesture.startX;
    const dy = event.clientY - gesture.startY;
    if (!gesture.started && Math.hypot(dx, dy) < MOVE_SLOP) return;

    if (!gesture.started) {
      if (!dispatchSynthetic('pointerdown', event, gesture.originX, gesture.originY, 1)) {
        gesture = null;
        return;
      }
      gesture.started = true;
    }

    gesture.currentX = gesture.originX + dx;
    gesture.currentY = gesture.originY + dy;
    dispatchSynthetic('pointermove', event, gesture.currentX, gesture.currentY, 1);
  }

  function onPointerEnd(event) {
    if (forwarding || !gesture || gesture.pointerId !== event.pointerId) return;
    stop(event);
    if (gesture.started) {
      dispatchSynthetic('pointerup', event, gesture.currentX, gesture.currentY, 0);
    }
    gesture = null;
  }

  // Register before galka-structure-v3.js. During left/right placement this
  // captures the real finger gesture at Window level and feeds V3 a synthetic
  // gesture that starts from the visible boundary point. The user can therefore
  // drag from any clear part of the chart without covering the candle/target.
  window.addEventListener('pointerdown', onPointerDown, { capture: true, passive: false });
  window.addEventListener('pointermove', onPointerMove, { capture: true, passive: false });
  window.addEventListener('pointerup', onPointerEnd, { capture: true, passive: false });
  window.addEventListener('pointercancel', onPointerEnd, { capture: true, passive: false });
})();
