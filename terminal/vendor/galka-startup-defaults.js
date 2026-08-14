const symbol = document.getElementById('symbolSelect');
const interval = document.getElementById('intervalSelect');

if (symbol) {
  symbol.value = 'BTC';
}

if (interval) {
  interval.value = '5m';
  if (typeof interval.onchange === 'function') {
    await interval.onchange();
  }
}

await import('./galka-trade-history-loader.js?v=2');
