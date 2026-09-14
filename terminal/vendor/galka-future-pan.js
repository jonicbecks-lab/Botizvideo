(() => {
  'use strict';

  const charts = window.LightweightCharts;
  if (!charts || typeof charts.createChart !== 'function') return;

  const originalCreateChart = charts.createChart.bind(charts);
  const MIN_VISIBLE_BARS = 20;
  const MAX_VISIBLE_BARS = 600;
  const FUTURE_SPACE_FRACTION = 0.75;
  const FALLBACK_INTERVAL_SECONDS = 300;
  const LineStyle = charts.LineStyle || Object.freeze({ Solid: 0, Dashed: 1, Dotted: 2 });

  function finite(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function priceDecimals(value) {
    const absolute = Math.abs(finite(value));
    if (absolute >= 1000) return 2;
    if (absolute >= 100) return 3;
    if (absolute >= 1) return 4;
    return 6;
  }

  function formatPrice(value) {
    return finite(value).toFixed(priceDecimals(value));
  }

  function formatTime(timestamp) {
    return new Date(finite(timestamp) * 1000).toLocaleString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function estimateInterval(all) {
    for (let index = all.length - 1; index > 0; index -= 1) {
      const delta = finite(all[index]?.time) - finite(all[index - 1]?.time);
      if (delta > 0) return delta;
    }
    return FALLBACK_INTERVAL_SECONDS;
  }

  function logicalTime(windowState, logicalIndex) {
    const all = windowState.all;
    if (!all.length) return 0;
    if (logicalIndex >= 0 && logicalIndex < all.length) return finite(all[logicalIndex]?.time);
    const interval = estimateInterval(all);
    if (logicalIndex < 0) return finite(all[0]?.time) + logicalIndex * interval;
    return finite(all.at(-1)?.time) + (logicalIndex - all.length + 1) * interval;
  }

  function patchFuturePan(chart) {
    if (!chart || chart.__galkaFuturePanInstalled) return chart;
    chart.__galkaFuturePanInstalled = true;

    chart.minPanOffset = function minPanOffset() {
      const count = clamp(Math.round(this.visibleCount), MIN_VISIBLE_BARS, MAX_VISIBLE_BARS);
      return -Math.max(1, Math.floor(count * FUTURE_SPACE_FRACTION));
    };

    chart.clampPanOffset = function clampPanOffset() {
      this.panOffset = clamp(this.panOffset, this.minPanOffset(), this.maxPanOffset());
    };

    chart.visibleWindow = function visibleWindow() {
      const all = this.series?.data || [];
      const count = clamp(Math.round(this.visibleCount), MIN_VISIBLE_BARS, MAX_VISIBLE_BARS);
      const end = Math.floor(all.length - this.panOffset);
      const start = end - count;
      const dataStart = clamp(start, 0, all.length);
      const dataEnd = clamp(end, 0, all.length);
      return {
        all,
        start,
        end,
        count,
        dataStart,
        dataEnd,
        rows: all.slice(dataStart, dataEnd),
      };
    };

    chart.drawCrosshair = function drawCrosshair(ctx, geometry, windowState, range) {
      if (!this.crosshair || !windowState.all.length) return;
      const { width, height, left, right, top, bottom, plotWidth, plotHeight } = geometry;
      const x = clamp(this.crosshair.x, left, right);
      const y = clamp(this.crosshair.y, top, bottom);
      const price = range.max - ((y - top) / plotHeight) * range.span;
      const ratio = clamp((x - left) / plotWidth, 0, 0.999999);
      const logicalIndex = Math.floor(windowState.start + ratio * windowState.count);
      const time = formatTime(logicalTime(windowState, logicalIndex));
      const priceLabel = formatPrice(price);

      ctx.save();
      ctx.strokeStyle = '#8290a3';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(left, y + 0.5);
      ctx.lineTo(right, y + 0.5);
      ctx.moveTo(x + 0.5, top);
      ctx.lineTo(x + 0.5, bottom);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.font = 'bold 11px system-ui, sans-serif';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = '#334155';
      ctx.fillRect(right + 1, y - 10, Math.max(1, width - right - 2), 20);
      ctx.fillStyle = '#f8fafc';
      ctx.fillText(priceLabel, right + 6, y);

      const timeWidth = ctx.measureText(time).width + 12;
      const timeX = clamp(x - timeWidth / 2, left, Math.max(left, right - timeWidth));
      ctx.fillStyle = '#334155';
      ctx.fillRect(timeX, bottom + 1, timeWidth, Math.max(1, height - bottom - 2));
      ctx.fillStyle = '#f8fafc';
      ctx.textAlign = 'center';
      ctx.fillText(time, timeX + timeWidth / 2, bottom + Math.max(1, height - bottom) / 2);
      ctx.restore();
    };

    chart.draw = function draw() {
      const ctx = this.ctx;
      if (!ctx) return;
      const geometry = this.geometry();
      const { width, height, left, right, top, bottom, plotWidth, plotHeight } = geometry;
      const background = this.options?.layout?.background?.color || '#0b0f15';
      const textColor = this.options?.layout?.textColor || '#9aa4b2';
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = background;
      ctx.fillRect(0, 0, width, height);

      const windowState = this.visibleWindow();
      const rows = windowState.rows;
      this.lastRows = rows;
      if (!windowState.all.length) return;

      const rangeRows = rows.length ? rows : windowState.all.slice(-1);
      const range = this.currentPriceRange(rangeRows);
      const y = (value) => top + (range.max - value) / range.span * plotHeight;

      ctx.strokeStyle = '#202936';
      ctx.lineWidth = 1;
      ctx.font = '11px system-ui, sans-serif';
      ctx.fillStyle = textColor;
      for (let index = 0; index <= 4; index += 1) {
        const yy = top + (plotHeight * index / 4);
        ctx.beginPath();
        ctx.moveTo(left, yy);
        ctx.lineTo(right, yy);
        ctx.stroke();
        const value = range.max - range.span * index / 4;
        ctx.fillText(formatPrice(value), right + 6, yy + 4);
      }

      ctx.strokeStyle = '#293241';
      ctx.beginPath();
      ctx.moveTo(right + 0.5, top);
      ctx.lineTo(right + 0.5, bottom);
      ctx.lineTo(left, bottom + 0.5);
      ctx.stroke();

      const slot = plotWidth / windowState.count;
      const bodyWidth = Math.max(1, Math.min(8, slot * 0.68));
      const up = this.series?.options?.upColor || '#16c784';
      const down = this.series?.options?.downColor || '#ef5350';
      rows.forEach((row, rowIndex) => {
        const logicalIndex = windowState.dataStart + rowIndex;
        const x = left + (logicalIndex - windowState.start + 0.5) * slot;
        if (x < left - bodyWidth || x > right + bodyWidth) return;
        const open = finite(row.open);
        const close = finite(row.close);
        const high = finite(row.high);
        const low = finite(row.low);
        const color = close >= open ? up : down;
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.moveTo(x, y(high));
        ctx.lineTo(x, y(low));
        ctx.stroke();
        const bodyTop = Math.min(y(open), y(close));
        const bodyHeight = Math.max(1, Math.abs(y(close) - y(open)));
        ctx.fillRect(x - bodyWidth / 2, bodyTop, bodyWidth, bodyHeight);
      });

      for (const line of this.series?.lines || []) {
        const value = finite(line.price);
        if (!(value > 0)) continue;
        const yy = y(value);
        if (yy < top || yy > bottom) continue;
        ctx.save();
        ctx.strokeStyle = line.color || '#7c8797';
        ctx.fillStyle = line.color || '#7c8797';
        ctx.lineWidth = finite(line.lineWidth, 1);
        if (line.lineStyle === LineStyle.Dashed) ctx.setLineDash([6, 5]);
        else if (line.lineStyle === LineStyle.Dotted) ctx.setLineDash([2, 4]);
        ctx.beginPath();
        ctx.moveTo(left, yy);
        ctx.lineTo(right, yy);
        ctx.stroke();
        ctx.setLineDash([]);
        const label = `${line.title || ''} ${formatPrice(value)}`.trim();
        ctx.font = '11px system-ui, sans-serif';
        const labelWidth = ctx.measureText(label).width + 8;
        ctx.fillRect(right - labelWidth, yy - 8, labelWidth, 16);
        ctx.fillStyle = '#081016';
        ctx.fillText(label, right - labelWidth + 4, yy + 4);
        ctx.restore();
      }

      ctx.fillStyle = textColor;
      ctx.font = '11px system-ui, sans-serif';
      const firstTime = formatTime(logicalTime(windowState, windowState.start));
      const lastTime = formatTime(logicalTime(windowState, windowState.end - 1));
      ctx.fillText(firstTime, left, height - 7);
      ctx.fillText(lastTime, Math.max(left, right - ctx.measureText(lastTime).width), height - 7);
      this.drawCrosshair(ctx, geometry, windowState, range);
    };

    chart.clampPanOffset();
    return chart;
  }

  window.LightweightCharts = Object.freeze({
    ...charts,
    createChart(container, options) {
      return patchFuturePan(originalCreateChart(container, options));
    },
  });
})();
