(() => {
  'use strict';

  const LineStyle = Object.freeze({ Solid: 0, Dashed: 1, Dotted: 2 });
  const CrosshairMode = Object.freeze({ Normal: 0 });
  const CandlestickSeries = Symbol('CandlestickSeries');

  function finite(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  class LocalSeries {
    constructor(chart, options) {
      this.chart = chart;
      this.options = options || {};
      this.data = [];
      this.lines = [];
    }

    setData(rows) {
      this.data = Array.isArray(rows) ? rows.slice().sort((a, b) => a.time - b.time) : [];
      this.chart.draw();
    }

    update(row) {
      if (!row || !Number.isFinite(Number(row.time))) return;
      const last = this.data.at(-1);
      if (last && Number(last.time) === Number(row.time)) this.data[this.data.length - 1] = row;
      else if (!last || Number(row.time) > Number(last.time)) this.data.push(row);
      else {
        const index = this.data.findIndex((item) => Number(item.time) === Number(row.time));
        if (index >= 0) this.data[index] = row;
      }
      this.chart.draw();
    }

    createPriceLine(options) {
      const line = { ...options, id: crypto.randomUUID ? crypto.randomUUID() : String(Math.random()) };
      this.lines.push(line);
      this.chart.draw();
      return line;
    }

    removePriceLine(line) {
      this.lines = this.lines.filter((item) => item !== line);
      this.chart.draw();
    }
  }

  class LocalChart {
    constructor(container, options) {
      this.container = container;
      this.options = options || {};
      this.canvas = document.createElement('canvas');
      this.canvas.setAttribute('aria-label', 'График свечей');
      this.canvas.style.width = '100%';
      this.canvas.style.height = '100%';
      this.canvas.style.display = 'block';
      this.container.replaceChildren(this.canvas);
      this.ctx = this.canvas.getContext('2d');
      this.series = null;
      this.visibleCount = 150;
      this.panOffset = 0;
      this.dragStart = null;
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(container);
      this.canvas.addEventListener('wheel', (event) => {
        event.preventDefault();
        const factor = event.deltaY > 0 ? 1.15 : 0.87;
        this.visibleCount = Math.max(30, Math.min(600, Math.round(this.visibleCount * factor)));
        this.draw();
      }, { passive: false });
      this.canvas.addEventListener('pointerdown', (event) => {
        this.dragStart = { x: event.clientX, offset: this.panOffset };
        this.canvas.setPointerCapture(event.pointerId);
      });
      this.canvas.addEventListener('pointermove', (event) => {
        if (!this.dragStart) return;
        const width = Math.max(1, this.canvas.clientWidth);
        const bars = Math.round((this.dragStart.x - event.clientX) / width * this.visibleCount);
        const maxOffset = Math.max(0, (this.series?.data.length || 0) - 10);
        this.panOffset = Math.max(0, Math.min(maxOffset, this.dragStart.offset + bars));
        this.draw();
      });
      this.canvas.addEventListener('pointerup', () => { this.dragStart = null; });
      this.canvas.addEventListener('pointercancel', () => { this.dragStart = null; });
      this.resize();
    }

    addSeries(_kind, options) {
      this.series = new LocalSeries(this, options);
      return this.series;
    }

    priceScale() {
      return { applyOptions: () => this.draw() };
    }

    timeScale() {
      return {
        fitContent: () => {
          this.panOffset = 0;
          this.visibleCount = Math.max(30, Math.min(180, this.series?.data.length || 150));
          this.draw();
        },
      };
    }

    resize() {
      const rect = this.container.getBoundingClientRect();
      const dpr = Math.max(1, window.devicePixelRatio || 1);
      const width = Math.max(1, Math.floor(rect.width));
      const height = Math.max(1, Math.floor(rect.height));
      this.canvas.width = Math.floor(width * dpr);
      this.canvas.height = Math.floor(height * dpr);
      this.canvas.style.width = `${width}px`;
      this.canvas.style.height = `${height}px`;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.draw();
    }

    draw() {
      const ctx = this.ctx;
      if (!ctx) return;
      const width = this.canvas.clientWidth || 1;
      const height = this.canvas.clientHeight || 1;
      const background = this.options?.layout?.background?.color || '#0b0f15';
      const textColor = this.options?.layout?.textColor || '#9aa4b2';
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = background;
      ctx.fillRect(0, 0, width, height);
      const all = this.series?.data || [];
      if (!all.length) return;

      const end = Math.max(0, all.length - this.panOffset);
      const start = Math.max(0, end - this.visibleCount);
      const rows = all.slice(start, end);
      if (!rows.length) return;

      const left = 8;
      const right = 72;
      const top = 12;
      const bottom = 24;
      const plotWidth = Math.max(1, width - left - right);
      const plotHeight = Math.max(1, height - top - bottom);
      const linePrices = (this.series?.lines || []).map((line) => finite(line.price)).filter((v) => v > 0);
      let min = Math.min(...rows.map((row) => finite(row.low)), ...linePrices);
      let max = Math.max(...rows.map((row) => finite(row.high)), ...linePrices);
      if (!(max > min)) { max += 1; min -= 1; }
      const padding = (max - min) * 0.05;
      min -= padding;
      max += padding;
      const y = (value) => top + (max - value) / (max - min) * plotHeight;

      ctx.strokeStyle = '#202936';
      ctx.lineWidth = 1;
      ctx.font = '11px system-ui, sans-serif';
      ctx.fillStyle = textColor;
      for (let i = 0; i <= 4; i += 1) {
        const yy = top + (plotHeight * i / 4);
        ctx.beginPath();
        ctx.moveTo(left, yy);
        ctx.lineTo(width - right, yy);
        ctx.stroke();
        const value = max - (max - min) * i / 4;
        ctx.fillText(value.toFixed(value < 1000 ? 2 : 0), width - right + 6, yy + 4);
      }

      const slot = plotWidth / rows.length;
      const bodyWidth = Math.max(1, Math.min(8, slot * 0.68));
      const up = this.series?.options?.upColor || '#16c784';
      const down = this.series?.options?.downColor || '#ef5350';
      rows.forEach((row, index) => {
        const x = left + slot * index + slot / 2;
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
        ctx.save();
        ctx.strokeStyle = line.color || '#7c8797';
        ctx.fillStyle = line.color || '#7c8797';
        ctx.lineWidth = finite(line.lineWidth, 1);
        if (line.lineStyle === LineStyle.Dashed) ctx.setLineDash([6, 5]);
        else if (line.lineStyle === LineStyle.Dotted) ctx.setLineDash([2, 4]);
        ctx.beginPath();
        ctx.moveTo(left, yy);
        ctx.lineTo(width - right, yy);
        ctx.stroke();
        ctx.setLineDash([]);
        const label = `${line.title || ''} ${value.toFixed(value < 1000 ? 2 : 0)}`.trim();
        ctx.font = '11px system-ui, sans-serif';
        const labelWidth = ctx.measureText(label).width + 8;
        ctx.fillRect(width - right - labelWidth, yy - 8, labelWidth, 16);
        ctx.fillStyle = '#081016';
        ctx.fillText(label, width - right - labelWidth + 4, yy + 4);
        ctx.restore();
      }

      const firstTime = new Date(finite(rows[0].time) * 1000);
      const lastTime = new Date(finite(rows.at(-1).time) * 1000);
      ctx.fillStyle = textColor;
      ctx.font = '11px system-ui, sans-serif';
      ctx.fillText(firstTime.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }), left, height - 7);
      const lastLabel = lastTime.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
      ctx.fillText(lastLabel, Math.max(left, width - right - ctx.measureText(lastLabel).width), height - 7);
    }
  }

  window.LightweightCharts = Object.freeze({
    createChart: (container, options) => new LocalChart(container, options),
    CandlestickSeries,
    CrosshairMode,
    LineStyle,
  });
})();
