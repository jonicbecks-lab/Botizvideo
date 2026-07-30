const chartRoot = document.getElementById('chart');
const stateOutput = document.getElementById('touchLabState');

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

function showCrosshairState() {
  const value = priceAtCrosshair();
  stateOutput.textContent = value == null
    ? 'Удерживай палец 0,45 с, затем перемещай перекрестие.'
    : `Перекрестие: ${value.toFixed(2)}. Отпусти палец — позиция должна остаться здесь.`;
}

for (const name of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel']) {
  chart.canvas.addEventListener(name, () => {
    requestAnimationFrame(showCrosshairState);
  }, { capture: false, passive: true });
}

new ResizeObserver(() => {
  requestAnimationFrame(showCrosshairState);
}).observe(chartRoot);
