const symbol = document.getElementById('symbolSelect');
const interval = document.getElementById('intervalSelect');

// live.js now starts with the same BTC/5m defaults as the HTML controls. Keep
// these assignments for browsers that restore form values across reloads, but do
// not fire another full candle request: live.js already loaded BTC/5m itself.
if (symbol) symbol.value = 'BTC';
if (interval) interval.value = '5m';

await import('./galka-trade-history-loader.js?v=2');
