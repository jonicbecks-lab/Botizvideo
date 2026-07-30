const chartRoot = document.getElementById('chart');
const stateOutput = document.getElementById('touchLabState');
const serverBadge = document.getElementById('touchLabServerStatus');

const connection = {
  authenticated: false,
  message: 'Проверка защищённой cookie-сессии…',
  uptimeSeconds: 0,
};

function syntheticCandles(count = 360) {
  const rows = [];
  const intervalSeconds = 5 * 60;
  const end = Math.floor(Date.now() / 1000 / intervalSeconds) * intervalSeconds;
  let close = 64200;
  let seed = 0x6d2b79f5;

  function random() {
    seed = Math.imul(seed ^ (seed >>> 15), seed | 1);
    seed ^= seed + Math.imul(seed ^ (seed >>> 7), seed | 61);
    return ((seed ^ (seed >>> 14)) >>> 0) / 4294967296;
  }

  for (let index = count - 1; index >= 0; index -= 1) {
    const time = end - index * intervalSeconds;
    const wave = Math.sin((count - index) / 17) * 22 + Math.sin((count - index) / 43) * 35;
    const drift = ((count - index) / count) * 360;
    const noise = (random() - 0.5) * 95;
    const open = close;
    close = 63800 + drift + wave + noise;
    const high = Math.max(open, close) + 12 + random() * 65;
    const low = Math.min(open, close) - 12 - random() * 65;
    rows.push({ time, open, high, low, close });
  }
  return rows;
}

const chart = LightweightCharts.createChart(chartRoot, {
  autoSize: true,
  layout: {
    background: { type: 'solid', color: '#0b0f15' },
    textColor: '#9aa4b2',
  },
  grid: {
    vertLines: { visible: false },
    horzLines: { visible: false },
  },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  rightPriceScale: {
    borderColor: '#293241',
    autoScale: true,
    scaleMargins: { top: 0.08, bottom: 0.12 },
  },
  timeScale: {
    borderColor: '#293241',
    timeVisible: true,
    secondsVisible: false,
    rightOffset: 8,
    barSpacing: 7,
  },
  handleScroll: {
    mouseWheel: true,
    pressedMouseMove: true,
    horzTouchDrag: true,
    vertTouchDrag: true,
  },
  handleScale: {
    axisPressedMouseMove: true,
    mouseWheel: true,
    pinch: true,
  },
});

const series = chart.addSeries(LightweightCharts.CandlestickSeries, {
  upColor: '#16c784',
  downColor: '#ef5350',
  borderVisible: false,
  wickUpColor: '#16c784',
  wickDownColor: '#ef5350',
  priceLineVisible: false,
  lastValueVisible: true,
});

series.setData(syntheticCandles());
chart.timeScale().fitContent();

function priceAtCrosshair() {
  if (!chart.crosshair) return null;
  const geometry = chart.geometry();
  const range = chart.currentPriceRange(chart.lastRows);
  if (!range || !(range.span > 0)) return null;
  const y = Math.max(geometry.top, Math.min(geometry.bottom, chart.crosshair.y));
  return range.max - ((y - geometry.top) / geometry.plotHeight) * range.span;
}

function renderState() {
  const value = priceAtCrosshair();
  const crosshairText = value == null
    ? 'Удерживай палец 0,45 с, затем перемещай перекрестие.'
    : `Перекрестие: ${value.toFixed(2)}. Отпусти палец — позиция должна остаться здесь.`;
  stateOutput.textContent = `${connection.message} · ${crosshairText}`;
  serverBadge.textContent = connection.authenticated ? 'COOKIE OK' : 'NO SESSION';
  serverBadge.dataset.state = connection.authenticated ? 'connected' : 'disconnected';
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    cache: 'no-store',
    credentials: 'same-origin',
    ...options,
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch (_) {
    throw new Error(`HTTP ${response.status}`);
  }
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

async function bootstrapCookie() {
  const hash = new URLSearchParams(location.hash.replace(/^#/, ''));
  const token = hash.get('token');
  if (!token) return;
  try {
    await requestJson('/touch-lab/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
  } finally {
    history.replaceState(null, '', location.pathname + location.search);
  }
}

async function refreshServerStatus() {
  try {
    const payload = await requestJson('/touch-lab/status');
    connection.authenticated = true;
    connection.uptimeSeconds = Number(payload.uptimeSeconds || 0);
    connection.message = `Сервер и постоянная cookie работают · uptime ${connection.uptimeSeconds} с`;
  } catch (error) {
    connection.authenticated = false;
    connection.message = error.message === 'NO_SESSION'
      ? 'Сервер отвечает, но cookie отсутствует: выполни galka-touch-lab-open.sh'
      : `Сервер недоступен: ${error.message}`;
  }
  renderState();
}

async function initializeSession() {
  try {
    await bootstrapCookie();
  } catch (error) {
    connection.message = `Не удалось установить cookie: ${error.message}`;
  }
  await refreshServerStatus();
}

for (const name of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel']) {
  chart.canvas.addEventListener(name, () => {
    requestAnimationFrame(renderState);
  }, { capture: false, passive: true });
}

new ResizeObserver(() => {
  requestAnimationFrame(renderState);
}).observe(chartRoot);

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) void refreshServerStatus();
});
window.addEventListener('pageshow', () => {
  void refreshServerStatus();
});
setInterval(() => {
  void refreshServerStatus();
}, 5000);

renderState();
void initializeSession();
