(() => {
  'use strict';

  const MIN_SIDE_OFFSET_MS = 1000;

  function snapshot() {
    try {
      return window.GalkaStructureDraft?.snapshot?.() || null;
    } catch (_) {
      return null;
    }
  }

  function showToast(message) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.className = 'toast error';
    window.setTimeout(() => toast.classList.add('hidden'), 3600);
  }

  function invalidReason(state) {
    if (!state) return '';
    const anchor = Number(state.anchorTimeMs || 0);
    if (!(anchor > 0)) return '';

    if (state.phase === 'choose-left') {
      const left = Number(state.leftBoundaryTimeMs || 0);
      const price = Number(state.leftBoundaryPrice || 0);
      if (!(price > 0) || !(left < anchor - MIN_SIDE_OFFSET_MS)) {
        return 'Сначала поставь левую границу левее ⚓. Одного якоря недостаточно.';
      }
    }

    if (state.phase === 'choose-right') {
      const right = Number(state.rightBoundaryTimeMs || 0);
      const price = Number(state.rightBoundaryPrice || 0);
      if (!(price > 0) || !(right > anchor + MIN_SIDE_OFFSET_MS)) {
        return 'Сначала поставь правую границу правее ⚓. Она должна быть выбрана отдельно.';
      }
    }
    return '';
  }

  // The base draft intentionally shows a boundary at the anchor as a visual
  // starting point. Never allow that placeholder to be committed as real
  // research data: both sides must be explicitly selected by the user.
  document.addEventListener('click', (event) => {
    const button = event.target?.closest?.('.galka-structure-confirm');
    if (!button) return;
    const reason = invalidReason(snapshot());
    if (!reason) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    showToast(reason);
    if (navigator.vibrate) navigator.vibrate([18, 35, 18]);
  }, true);
})();
