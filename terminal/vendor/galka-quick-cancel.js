(() => {
  'use strict';

  const statusButton = document.getElementById('campaignStatus');
  const actionButton = document.getElementById('previewButton');
  const symbolSelect = document.getElementById('symbolSelect');
  const galkaInput = document.getElementById('galkaInput');
  const campaignDetails = document.getElementById('campaignDetails');
  const cancelDrawerButton = document.getElementById('cancelCampaign');
  const toast = document.getElementById('toast');
  if (!statusButton || !actionButton || !symbolSelect) return;

  const sessionToken = sessionStorage.getItem('galkaLiveSession') || '';
  let cancelBusy = false;
  let reapplyTimer = null;

  function showToast(message, type = '') {
    if (!toast) return;
    toast.textContent = message;
    toast.className = `toast ${type}`;
    setTimeout(() => toast.classList.add('hidden'), 4500);
  }

  function waitingWithoutPosition() {
    const text = statusButton.textContent || '';
    const coin = symbolSelect.value || '';
    return text.startsWith(`${coin} · ждём `);
  }

  function applyQuickCancel() {
    clearTimeout(reapplyTimer);
    if (cancelBusy || !waitingWithoutPosition()) {
      actionButton.dataset.quickCancel = '';
      return;
    }
    actionButton.dataset.quickCancel = '1';
    actionButton.disabled = false;
    actionButton.textContent = 'Снять GALKA';
  }

  function applyConfirmedCancelUi(coin) {
    window.GalkaLiveUiBridge?.clearPriceLines?.();

    statusButton.textContent = `${coin} · нет GALKA`;
    statusButton.className = 'campaign-status idle';

    actionButton.dataset.quickCancel = '';
    actionButton.disabled = false;
    actionButton.textContent = 'Поставить GALKA';

    if (galkaInput) {
      galkaInput.disabled = false;
      galkaInput.value = '';
    }
    if (campaignDetails) {
      campaignDetails.innerHTML = '<div class="campaign-card"><small>Нет активной GALKA для выбранной монеты.</small></div>';
    }
    if (cancelDrawerButton) cancelDrawerButton.disabled = true;
  }

  async function cancelWaitingGalka() {
    if (cancelBusy || !waitingWithoutPosition()) return;
    const coin = symbolSelect.value;
    if (!confirm(`Снять GALKA ${coin} и отменить все её ожидающие лимитки?`)) return;

    cancelBusy = true;
    actionButton.disabled = true;
    actionButton.textContent = 'Отмена…';
    const started = performance.now();

    try {
      const response = await fetch('/api/live/cancel', {
        method: 'POST',
        headers: {
          'X-Galka-Session': sessionToken,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ coin }),
        cache: 'no-store',
        credentials: 'same-origin',
      });
      const payload = await response.json();
      if (!response.ok || payload?.ok === false) {
        throw new Error(payload?.error || `HTTP ${response.status}`);
      }

      applyConfirmedCancelUi(coin);
      const elapsedSeconds = Math.max(0, (performance.now() - started) / 1000);
      showToast(`GALKA снята за ${elapsedSeconds.toFixed(1)} с`, 'ok');
      cancelBusy = false;

      // Do not reload the whole terminal here. A full reload re-downloads the
      // historical candle/cluster window and made a successful cancel look stuck.
      // The regular status loop will reconcile the in-memory LIVE state shortly.
    } catch (error) {
      showToast(error.message || 'Не удалось снять GALKA', 'error');
      cancelBusy = false;
      applyQuickCancel();
    }
  }

  actionButton.addEventListener('click', (event) => {
    if (actionButton.dataset.quickCancel !== '1') return;
    event.preventDefault();
    event.stopImmediatePropagation();
    cancelWaitingGalka();
  }, true);

  const observer = new MutationObserver(() => {
    reapplyTimer = setTimeout(applyQuickCancel, 0);
  });
  observer.observe(statusButton, { childList: true, subtree: true, characterData: true });
  observer.observe(actionButton, { attributes: true, childList: true, subtree: true });
  symbolSelect.addEventListener('change', () => setTimeout(applyQuickCancel, 0));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') setTimeout(applyQuickCancel, 0);
  });

  applyQuickCancel();
})();
