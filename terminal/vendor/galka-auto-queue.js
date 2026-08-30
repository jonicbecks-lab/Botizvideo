(() => {
  'use strict';

  const button = document.getElementById('autoQueueButton');
  const clearButton = document.getElementById('autoQueueClear');
  const symbol = document.getElementById('symbolSelect');
  const toast = document.getElementById('toast');
  if (!button || !clearButton || !symbol) return;

  const drafts = new Map();
  let queueState = null;
  let busy = false;
  let toastTimer = null;
  let pollTimer = null;

  function currentCoin() {
    return String(symbol.value || 'BTC').toUpperCase();
  }

  function formatPrice(value, coin = currentCoin()) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return number.toFixed(coin === 'SOL' ? 4 : 2);
  }

  function showToast(message, type = 'ok') {
    if (!toast) return;
    toast.textContent = message;
    toast.className = `toast ${type}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.add('hidden'), 4500);
  }

  async function api(path, { method = 'GET', body } = {}) {
    const token = sessionStorage.getItem('galkaLiveSession') || '';
    const headers = token ? { 'X-Galka-Session': token } : {};
    if (body) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      cache: 'no-store',
      credentials: 'same-origin',
    });
    const payload = await response.json();
    if (!response.ok || payload?.ok === false) {
      throw new Error(payload?.error || `HTTP ${response.status}`);
    }
    return payload.data;
  }

  function render() {
    const coin = currentCoin();
    const draft = Number(drafts.get(coin) || 0);
    const row = queueState && queueState.coin === coin ? queueState : null;
    button.className = 'auto-queue-button';
    clearButton.classList.add('hidden');

    if (draft > 0) {
      button.textContent = `AUTO ${formatPrice(draft, coin)}`;
      button.title = 'Поставить выбранную перекрестием цену в очередь после текущей GALKA';
      button.disabled = busy;
      button.classList.add('draft');
      return;
    }

    if (!row || row.status === 'activated') {
      button.textContent = 'AUTO';
      button.title = 'Во время активной GALKA выбери следующую цену перекрестием и нажми AUTO';
      button.disabled = true;
      return;
    }

    const label = formatPrice(row.galkaPrice, coin);
    if (row.status === 'queued') {
      button.textContent = `AUTO ${label} · ждёт`;
      button.title = 'Следующая GALKA в очереди. Она активируется только после нормального завершения текущей.';
      button.disabled = true;
      button.classList.add('queued');
      clearButton.classList.remove('hidden');
    } else if (row.status === 'paused') {
      button.textContent = `AUTO ${label} · ▶`;
      button.title = `AUTO на паузе: ${row.pausedReason || 'нужна ручная активация'}`;
      button.disabled = busy;
      button.classList.add('paused');
      clearButton.classList.remove('hidden');
    } else if (row.status === 'invalidated') {
      button.textContent = `AUTO ${label} · перебита`;
      button.title = row.invalidatedReason || 'Уровень уже был перебит после постановки в очередь';
      button.disabled = true;
      button.classList.add('invalidated');
      clearButton.classList.remove('hidden');
    } else if (row.status === 'activating') {
      button.textContent = `AUTO ${label} · ...`;
      button.title = 'AUTO GALKA активируется';
      button.disabled = true;
      button.classList.add('activating');
    } else {
      button.textContent = `AUTO ${label}`;
      button.disabled = true;
    }
    clearButton.disabled = busy || row.status === 'activating';
  }

  async function refreshQueue({ silent = true } = {}) {
    const coin = currentCoin();
    try {
      queueState = await api(`/api/live/queue?coin=${encodeURIComponent(coin)}`);
      render();
    } catch (error) {
      if (!silent) showToast(error.message || 'Не удалось прочитать AUTO очередь', 'error');
    }
  }

  document.addEventListener('galka:auto-queue-price', (event) => {
    const coin = String(event.detail?.coin || currentCoin()).toUpperCase();
    const price = Number(event.detail?.price);
    if (!(price > 0)) return;
    drafts.set(coin, price);
    if (coin === currentCoin()) render();
  });

  button.addEventListener('click', async () => {
    if (busy) return;
    const coin = currentCoin();
    const draft = Number(drafts.get(coin) || 0);
    const row = queueState && queueState.coin === coin ? queueState : null;

    busy = true;
    render();
    try {
      if (draft > 0) {
        queueState = await api('/api/live/queue', {
          method: 'POST',
          body: {
            coin,
            galkaPrice: draft,
            confirmation: 'QUEUE_REAL_GALKA',
          },
        });
        drafts.delete(coin);
        showToast(`AUTO GALKA ${formatPrice(queueState.galkaPrice, coin)} поставлена в очередь.`);
      } else if (row?.status === 'paused') {
        const result = await api('/api/live/queue/activate', {
          method: 'POST',
          body: {
            coin,
            confirmation: 'ACTIVATE_QUEUED_GALKA',
          },
        });
        queueState = result?.queue || null;
        showToast(`AUTO GALKA ${formatPrice(row.galkaPrice, coin)} активирована.`, 'ok');
      }
    } catch (error) {
      showToast(error.message || 'AUTO действие не выполнено', 'error');
    } finally {
      busy = false;
      await refreshQueue({ silent: true });
      render();
    }
  });

  clearButton.addEventListener('click', async () => {
    if (busy || !queueState) return;
    const coin = currentCoin();
    busy = true;
    render();
    try {
      await api('/api/live/queue/delete', {
        method: 'POST',
        body: {
          coin,
          confirmation: 'DELETE_QUEUED_GALKA',
        },
      });
      queueState = null;
      drafts.delete(coin);
      showToast('AUTO GALKA удалена из очереди.');
    } catch (error) {
      showToast(error.message || 'Не удалось удалить AUTO GALKA', 'error');
    } finally {
      busy = false;
      render();
    }
  });

  symbol.addEventListener('change', () => {
    queueState = null;
    refreshQueue({ silent: true });
  });

  function schedulePoll() {
    clearInterval(pollTimer);
    // The AUTO activation itself is server-side. This poll only updates the
    // button label, so 5 seconds is enough and avoids 40 extra HTTP requests per
    // minute competing with the phone UI.
    pollTimer = setInterval(() => refreshQueue({ silent: true }), 5000);
  }

  refreshQueue({ silent: true });
  schedulePoll();
})();
