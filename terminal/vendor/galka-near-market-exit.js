(() => {
  'use strict';

  const drawerActions = document.querySelector('.drawer-actions');
  const symbolSelect = document.getElementById('symbolSelect');
  const toast = document.getElementById('toast');
  if (!drawerActions || !symbolSelect) return;

  const button = document.createElement('button');
  button.type = 'button';
  button.id = 'closeNearMarket';
  button.className = 'danger';
  button.textContent = 'Закрыть рядом с рынком';
  drawerActions.insertBefore(button, drawerActions.lastElementChild || null);

  function notify(message, type = '') {
    if (!toast) return;
    toast.textContent = message;
    toast.className = `toast ${type}`;
    setTimeout(() => toast.classList.add('hidden'), 5500);
  }

  button.addEventListener('click', async () => {
    if (button.disabled) return;
    const coin = symbolSelect.value;
    const accepted = confirm(
      `${coin}: снять оставшиеся входы и старые TP, затем выставить весь фактический long-объём на продажу максимально близко выше рынка?\n\nОрдер будет reduce-only и post-only: short не откроется, рыночного исполнения не будет.`
    );
    if (!accepted) return;

    button.disabled = true;
    button.textContent = 'Готовим выход…';
    try {
      const token = sessionStorage.getItem('galkaLiveSession') || '';
      const response = await fetch('/api/live/close-near-market', {
        method: 'POST',
        headers: {
          'X-Galka-Session': token,
          'Content-Type': 'application/json',
        },
        credentials: 'same-origin',
        cache: 'no-store',
        body: JSON.stringify({ coin, confirmation: 'CLOSE_NEAR_MARKET' }),
      });
      const payload = await response.json();
      if (!response.ok || payload?.ok === false) {
        throw new Error(payload?.error || `HTTP ${response.status}`);
      }
      const data = payload.data || {};
      notify(`${coin}: весь объём выставлен на продажу по ${data.price}`, 'ok');
      setTimeout(() => location.reload(), 700);
    } catch (error) {
      notify(error.message || 'Не удалось выставить выход рядом с рынком', 'error');
      button.disabled = false;
      button.textContent = 'Закрыть рядом с рынком';
    }
  });
})();
