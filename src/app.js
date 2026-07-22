export const LEGACY_VIEWER_LIMITS = Object.freeze({
  archiveBytes: 32 * 1024 * 1024,
  jsonBytes: 64 * 1024 * 1024,
  candles: 500_000,
  trades: 100_000,
  fills: 500_000,
  zipEntries: 100,
});

const $ = (id) => document.getElementById(id);
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const cleanText = (value, maxLength = 240) => String(value ?? '—')
  .replace(/[\u0000-\u001f\u007f]/g, ' ')
  .slice(0, maxLength);

export function toTimestamp(value, label = 'time') {
  const raw = typeof value === 'number' ? value : Date.parse(String(value));
  const seconds = raw > 1_000_000_000_000 ? raw / 1_000 : raw;
  if (!Number.isFinite(seconds) || seconds < 0 || seconds > 8_640_000_000) {
    throw new Error(`Некорректное время ${label}`);
  }
  return Math.floor(seconds);
}

function finiteNumber(value, label, { positive = false } = {}) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || (positive && !(parsed > 0))) {
    throw new Error(`Некорректное число ${label}`);
  }
  return parsed;
}

function optionalTimestamp(value, label) {
  return value == null || value === '' ? null : toTimestamp(value, label);
}

function optionalNumber(value, label) {
  return value == null || value === '' ? null : finiteNumber(value, label);
}

export function validatePackage(source) {
  if (!isRecord(source) || !isRecord(source.meta)) {
    throw new Error('Пакет должен содержать объект meta');
  }
  if (!Array.isArray(source.candles) || !Array.isArray(source.trades)) {
    throw new Error('candles и trades должны быть массивами');
  }
  if (!source.candles.length || source.candles.length > LEGACY_VIEWER_LIMITS.candles) {
    throw new Error(`Недопустимое количество свечей: ${source.candles.length}`);
  }
  if (source.trades.length > LEGACY_VIEWER_LIMITS.trades) {
    throw new Error(`Слишком много сделок: ${source.trades.length}`);
  }

  const candles = source.candles.map((item, index) => {
    if (!isRecord(item)) throw new Error(`Свеча ${index + 1} должна быть объектом`);
    const candle = {
      ...item,
      time: toTimestamp(item.time, `candles[${index}].time`),
      open: finiteNumber(item.open, `candles[${index}].open`, { positive: true }),
      high: finiteNumber(item.high, `candles[${index}].high`, { positive: true }),
      low: finiteNumber(item.low, `candles[${index}].low`, { positive: true }),
      close: finiteNumber(item.close, `candles[${index}].close`, { positive: true }),
    };
    if (candle.high < Math.max(candle.open, candle.close)
      || candle.low > Math.min(candle.open, candle.close)
      || candle.low > candle.high) {
      throw new Error(`Некорректный OHLC candles[${index}]`);
    }
    return candle;
  }).sort((left, right) => left.time - right.time);
  for (let index = 1; index < candles.length; index += 1) {
    if (candles[index].time === candles[index - 1].time) {
      throw new Error(`Дублирующая свеча ${candles[index].time}`);
    }
  }

  let fillCount = 0;
  const trades = source.trades.map((item, index) => {
    if (!isRecord(item)) throw new Error(`Сделка ${index + 1} должна быть объектом`);
    const fills = item.fills == null ? [] : item.fills;
    if (!Array.isArray(fills)) throw new Error(`trades[${index}].fills должен быть массивом`);
    fillCount += fills.length;
    if (fillCount > LEGACY_VIEWER_LIMITS.fills) throw new Error('Слишком много исполнений в пакете');
    const normalizedFills = fills.map((fill, fillIndex) => {
      if (!isRecord(fill)) throw new Error(`trades[${index}].fills[${fillIndex}] должен быть объектом`);
      return {
        ...fill,
        time: optionalTimestamp(fill.time, `trades[${index}].fills[${fillIndex}].time`),
        price: optionalNumber(fill.price, `trades[${index}].fills[${fillIndex}].price`),
      };
    });
    return {
      ...item,
      id: cleanText(item.id ?? `trade-${index + 1}`, 120),
      pnl: optionalNumber(item.pnl, `trades[${index}].pnl`) ?? 0,
      v_low: optionalNumber(item.v_low, `trades[${index}].v_low`),
      break_price: optionalNumber(item.break_price, `trades[${index}].break_price`),
      last_limit: optionalNumber(item.last_limit, `trades[${index}].last_limit`),
      risk_money: optionalNumber(item.risk_money, `trades[${index}].risk_money`),
      avg_entry: optionalNumber(item.avg_entry, `trades[${index}].avg_entry`),
      exit_price: optionalNumber(item.exit_price, `trades[${index}].exit_price`),
      v_low_time: optionalTimestamp(item.v_low_time, `trades[${index}].v_low_time`),
      break_time: optionalTimestamp(item.break_time, `trades[${index}].break_time`),
      exit_time: optionalTimestamp(item.exit_time, `trades[${index}].exit_time`),
      fills: normalizedFills,
    };
  });
  return { ...source, candles, trades };
}

function byteLength(input) {
  if (typeof input?.size === 'number') return input.size;
  if (typeof input?.byteLength === 'number') return input.byteLength;
  return NaN;
}

function zipEntrySize(entry) {
  return Number(entry?._data?.uncompressedSize);
}

export async function loadPackageFile(file, jszip = globalThis.JSZip) {
  const compressedBytes = byteLength(file);
  if (!Number.isFinite(compressedBytes) || compressedBytes < 0
    || compressedBytes > LEGACY_VIEWER_LIMITS.archiveBytes) {
    throw new Error('Файл слишком большой или его размер неизвестен');
  }
  const lowerName = String(file.name || '').toLowerCase();
  if (!lowerName.endsWith('.zip')) {
    if (compressedBytes > LEGACY_VIEWER_LIMITS.jsonBytes) throw new Error('JSON-файл слишком большой');
    const text = await file.text();
    if (new TextEncoder().encode(text).byteLength > LEGACY_VIEWER_LIMITS.jsonBytes) {
      throw new Error('JSON-файл превышает безопасный лимит');
    }
    return validatePackage(JSON.parse(text));
  }
  if (!jszip) throw new Error('Локальный ZIP-модуль не загрузился');
  const archive = await jszip.loadAsync(file, { createFolders: false });
  const entries = Object.values(archive.files).filter((entry) => !entry.dir);
  if (!entries.length || entries.length > LEGACY_VIEWER_LIMITS.zipEntries) {
    throw new Error('Недопустимое количество файлов в архиве');
  }
  let totalUncompressedBytes = 0;
  for (const candidate of entries) {
    const candidateBytes = zipEntrySize(candidate);
    if (!Number.isFinite(candidateBytes) || candidateBytes < 0
      || candidateBytes > LEGACY_VIEWER_LIMITS.jsonBytes) {
      throw new Error('Файл в архиве превышает безопасный лимит');
    }
    totalUncompressedBytes += candidateBytes;
  }
  if (totalUncompressedBytes > LEGACY_VIEWER_LIMITS.jsonBytes * 2) {
    throw new Error('Распакованный архив превышает безопасный лимит');
  }
  const preferred = ['package.json', 'galka.json', 'result.json']
    .map((name) => archive.file(name))
    .find(Boolean);
  const jsonEntries = entries.filter((entry) => entry.name.toLowerCase().endsWith('.json'));
  const entry = preferred || (jsonEntries.length === 1 ? jsonEntries[0] : null);
  if (!entry) throw new Error('В архиве нет однозначного JSON-пакета');
  const uncompressedBytes = zipEntrySize(entry);
  if (!Number.isFinite(uncompressedBytes) || uncompressedBytes < 0
    || uncompressedBytes > LEGACY_VIEWER_LIMITS.jsonBytes) {
    throw new Error('Распакованный JSON превышает безопасный лимит');
  }
  const text = await entry.async('text');
  if (new TextEncoder().encode(text).byteLength > LEGACY_VIEWER_LIMITS.jsonBytes) {
    throw new Error('Распакованный JSON превышает безопасный лимит');
  }
  return validatePackage(JSON.parse(text));
}

let chart;
let candleSeries;
let priceLines = [];

function createElement(tag, { className = '', text = '' } = {}) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = cleanText(text, 2_000);
  return node;
}

function metricRow(label, value) {
  const row = createElement('div', { className: 'metric' });
  row.append(createElement('span', { text: label }), createElement('strong', { text: value ?? '—' }));
  return row;
}

function initChart() {
  const library = globalThis.LightweightCharts;
  if (!library) throw new Error('Локальный графический модуль не загрузился');
  chart = library.createChart($('chart'), {
    layout: { background: { color: '#0b1117' }, textColor: '#aebbc7' },
    grid: { vertLines: { color: '#18232d' }, horzLines: { color: '#18232d' } },
    timeScale: { timeVisible: true, secondsVisible: false },
    rightPriceScale: { borderColor: '#30404d' },
  });
  candleSeries = chart.addCandlestickSeries
    ? chart.addCandlestickSeries({
      upColor: '#26a69a', downColor: '#ef5350', wickUpColor: '#26a69a',
      wickDownColor: '#ef5350', borderVisible: false,
    })
    : chart.addSeries(library.CandlestickSeries, {});
  new ResizeObserver(() => chart.resize($('chart').clientWidth, $('chart').clientHeight))
    .observe($('chart'));
}

function clearLines() {
  for (const line of priceLines) candleSeries.removePriceLine(line);
  priceLines = [];
}

function addLine(value, title, color) {
  if (!(value > 0)) return;
  priceLines.push(candleSeries.createPriceLine({
    price: value,
    title,
    color,
    lineWidth: 1,
    lineStyle: globalThis.LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true,
  }));
}

function renderSummary(data) {
  const summary = data.summary || {};
  const container = $('summary');
  container.replaceChildren(createElement('h2', { text: 'Сводка' }));
  const rows = [
    ['Инструмент', data.meta.symbol],
    ['Таймфрейм', data.meta.timeframe],
    ['Сделок', summary.trades ?? data.trades.length],
    ['Win rate', summary.win_rate == null ? '—' : `${(finiteNumber(summary.win_rate, 'summary.win_rate') * 100).toFixed(1)}%`],
    ['PnL', summary.net_pnl ?? '—'],
    ['Max DD', summary.max_drawdown ?? '—'],
    ['Модель', data.meta.model_id ?? '—'],
  ];
  container.append(...rows.map(([label, value]) => metricRow(label, value)));
}

function focusTrade(trade) {
  clearLines();
  addLine(trade.v_low, 'V-low', '#f5c451');
  addLine(trade.last_limit, 'Последняя лимитка', '#ef6b73');
  addLine(trade.avg_entry, 'Средний вход', '#62c987');
  addLine(trade.exit_price, 'Выход', '#8aa8ff');
  const container = $('tradeDetails');
  container.replaceChildren(createElement('h2', { text: 'Детали сделки' }));
  const rows = [
    ['ID', trade.id], ['V-low', trade.v_low], ['Пробой', trade.break_price],
    ['Исполнено лимиток', trade.fills.length], ['Риск, деньги', trade.risk_money],
    ['Средний вход', trade.avg_entry], ['Выход', trade.exit_price], ['PnL', trade.pnl],
    ['Причина выхода', trade.exit_reason],
  ];
  container.append(...rows.map(([label, value]) => metricRow(label, value)));
  container.append(createElement('p', {
    className: 'muted',
    text: `Параметры: ${JSON.stringify(trade.parameters || {})}`,
  }));
  const times = [trade.v_low_time, trade.break_time, ...trade.fills.map((fill) => fill.time), trade.exit_time]
    .filter((value) => value != null);
  if (times.length) {
    chart.timeScale().setVisibleRange({ from: Math.min(...times) - 3_600, to: Math.max(...times) + 3_600 });
  }
}

function show(data) {
  candleSeries.setData(data.candles);
  const markers = [];
  for (const trade of data.trades) {
    if (trade.v_low_time != null) markers.push({ time: trade.v_low_time, position: 'belowBar', color: '#f5c451', shape: 'circle', text: `V ${cleanText(trade.id, 48)}` });
    if (trade.break_time != null) markers.push({ time: trade.break_time, position: 'aboveBar', color: '#ef6b73', shape: 'arrowDown', text: 'Пробой' });
    for (const fill of trade.fills) {
      if (fill.time != null) markers.push({ time: fill.time, position: 'belowBar', color: '#62c987', shape: 'arrowUp', text: `L${cleanText(fill.level ?? '', 12)}` });
    }
    if (trade.exit_time != null) markers.push({ time: trade.exit_time, position: 'aboveBar', color: trade.pnl >= 0 ? '#62c987' : '#ef6b73', shape: 'arrowDown', text: `Exit ${trade.pnl.toFixed(2)}` });
  }
  markers.sort((left, right) => left.time - right.time);
  if (candleSeries.setMarkers) candleSeries.setMarkers(markers);
  else globalThis.LightweightCharts.createSeriesMarkers(candleSeries, markers);
  renderSummary(data);
  const select = $('tradeSelect');
  select.replaceChildren();
  data.trades.forEach((trade, index) => {
    const option = createElement('option', {
      text: `${trade.id} | ${trade.pnl >= 0 ? '+' : ''}${trade.pnl.toFixed(2)} | ${cleanText(trade.status || 'closed', 32)}`,
    });
    option.value = String(index);
    select.append(option);
  });
  select.onchange = () => focusTrade(data.trades[Number(select.value)]);
  if (data.trades.length) {
    select.value = '0';
    focusTrade(data.trades[0]);
  } else {
    chart.timeScale().fitContent();
  }
}

function renderError(error) {
  const container = $('summary');
  container.replaceChildren(
    createElement('h2', { text: 'Ошибка файла' }),
    createElement('div', { className: 'error', text: error?.message || String(error) }),
  );
}

function bootstrap() {
  try {
    initChart();
  } catch (error) {
    renderError(error);
    return;
  }
  $('fileInput').addEventListener('change', async (event) => {
    try {
      const file = event.target.files?.[0];
      if (file) show(await loadPackageFile(file));
    } catch (error) {
      renderError(error);
    } finally {
      event.target.value = '';
    }
  });
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') bootstrap();
