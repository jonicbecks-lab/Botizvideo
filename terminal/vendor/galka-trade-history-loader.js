const STORAGE_KEY = 'galka-live-trade-markers-v1';
const VALID_COINS = new Set(['BTC', 'ETH', 'SOL']);
const MAX_MARKERS = 240;

function selectedCoin() {
  const value = String(document.getElementById('symbolSelect')?.value || 'BTC').toUpperCase();
  return VALID_COINS.has(value) ? value : 'BTC';
}

function loadStore() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (_) {
    return {};
  }
}

function mergeMarkers(coin, markers) {
  const store = loadStore();
  store[coin] ||= {};
  for (const marker of markers || []) {
    if (!marker?.key || !(Number(marker.time) > 0)) continue;
    store[coin][marker.key] = { ...store[coin][marker.key], ...marker, coin };
  }
  const rows = Object.values(store[coin])
    .sort((left, right) => Number(left.time || 0) - Number(right.time || 0))
    .slice(-MAX_MARKERS);
  store[coin] = Object.fromEntries(rows.map((row) => [row.key, row]));
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  } catch (_) {
    // Marker persistence is best-effort only.
  }
  window.GalkaTradeMarkers?.refresh?.();
}

let busy = false;
async function refreshHistory() {
  if (busy) return;
  const sessionToken = sessionStorage.getItem('galkaLiveSession') || '';
  if (!sessionToken) return;
  const coin = selectedCoin();
  busy = true;
  try {
    const response = await fetch(
      `/api/live/history?coin=${encodeURIComponent(coin)}&limit=24`,
      {
        headers: { 'X-Galka-Session': sessionToken },
        cache: 'no-store',
        credentials: 'same-origin',
      },
    );
    const payload = await response.json();
    if (!response.ok || payload?.ok === false) return;
    mergeMarkers(coin, payload?.data?.markers || []);
  } catch (_) {
    // Historical markers must never affect the trading UI or API calls.
  } finally {
    busy = false;
  }
}

document.getElementById('symbolSelect')?.addEventListener('change', () => {
  setTimeout(refreshHistory, 0);
});

await refreshHistory();
setInterval(refreshHistory, 60_000);
