(() => {
  'use strict';

  const LineStyle = Object.freeze({ Solid: 0, Dashed: 1, Dotted: 2 });
  const CrosshairMode = Object.freeze({ Normal: 0 });
  const CandlestickSeries = Symbol('CandlestickSeries');
  const MIN_VISIBLE_BARS = 20;
  const MAX_VISIBLE_BARS = 600;
  const MIN_REMAINING_BARS = 10;
  const AXIS = Object.freeze({ left: 8, right: 72, top: 12, bottom: 24 });

  function finite(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function distance(left, right) {
    return Math.hypot(right.x - left.x, right.y - left.y);
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
      this.chart.resetPriceScale();
      this.chart.clampPanOffset();
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
      this.chart.clampPanOffset();
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
      this.canvas.setAttribute('aria-label', 'График свечей. Перетаскивание двигает график, колесо и щипок изменяют масштаб, шкалы времени и цены масштабируются перетаскиванием.');
      this.canvas.setAttribute('tabindex', '0');
      this.canvas.className = 'galka-live-canvas';
      this.container.replaceChildren(this.canvas);
      this.ctx = this.canvas.getContext('2d');
      this.series = null;
      this.visibleCount = 150;
      this.panOffset = 0;
      this.manualPriceRange = null;
      this.gesture = null;
      this.activePointers = new Map();
      this.lastRows = [];
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(container);

      this.canvas.addEventListener('wheel', (event) => this.onWheel(event), { passive: false });
      this.canvas.addEventListener('pointerdown', (event) => this.onPointerDown(event));
      this.canvas.addEventListener('pointermove', (event) => this.onPointerMove(event));
      this.canvas.addEventListener('pointerup', (event) => this.onPointerEnd(event));
      this.canvas.addEventListener('pointercancel', (event) => this.onPointerEnd(event));
      this.canvas.addEventListener('lostpointercapture', (event) => this.onPointerEnd(event));
      this.canvas.addEventListener('pointerleave', () => {
        if (!this.gesture) this.setInteractionClass('plot');
      });
      this.canvas.addEventListener('dblclick', (event) => this.onDoubleClick(event));
      this.canvas.addEventListener('contextmenu', (event) => event.preventDefault());
      this.resize();
    }

    addSeries(_kind, options) {
      this.series = new LocalSeries(this, options);
      return this.series;
    }

    priceScale() {
      return {
        applyOptions: (options = {}) => {
          if (options.autoScale !== false) this.resetPriceScale();
          this.draw();
        },
      };
    }

    timeScale() {
      return {
        fitContent: () => this.fitContent(),
        scrollToRealTime: () => {
          this.panOffset = 0;
          this.draw();
        },
      };
    }

    fitContent() {
      this.panOffset = 0;
      this.visibleCount = Math.max(MIN_VISIBLE_BARS, Math.min(180, this.series?.data.length || 150));
      this.resetPriceScale();
      this.draw();
    }

    resetPriceScale() {
      this.manualPriceRange = null;
    }

    maxPanOffset() {
      return Math.max(0, (this.series?.data.length || 0) - MIN_REMAINING_BARS);
    }

    clampPanOffset() {
      this.panOffset = clamp(this.panOffset, 0, this.maxPanOffset());
    }

    geometry() {
      const width = this.canvas.clientWidth || 1;
      const height = this.canvas.clientHeight || 1;
      const plotRight = Math.max(AXIS.left + 1, width - AXIS.right);
      const plotBottom = Math.max(AXIS.top + 1, height - AXIS.bottom);
      return {
        width,
        height,
        left: AXIS.left,
        right: plotRight,
        top: AXIS.top,
        bottom: plotBottom,
        plotWidth: Math.max(1, plotRight - AXIS.left),
        plotHeight: Math.max(1, plotBottom - AXIS.top),
      };
    }

    pointFromEvent(event) {
      const rect = this.canvas.getBoundingClientRect();
      const x = clamp(event.clientX - rect.left, 0, rect.width);
      const y = clamp(event.clientY - rect.top, 0, rect.height);
      const geometry = this.geometry();
      let zone = 'plot';
      if (x >= geometry.right) zone = 'price';
      else if (y >= geometry.bottom) zone = 'time';
      return { id: event.pointerId, x, y, zone, pointerType: event.pointerType };
    }

    setInteractionClass(zone, active = '') {
      const classes = [
        'price-axis',
        'time-axis',
        'chart-dragging',
        'price-scaling',
        'time-scaling',
        'chart-pinching',
      ];
      this.canvas.classList.remove(...classes);
      if (active === 'pan') this.canvas.classList.add('chart-dragging');
      else if (active === 'price') this.canvas.classList.add('price-scaling');
      else if (active === 'time') this.canvas.classList.add('time-scaling');
      else if (active === 'pinch') this.canvas.classList.add('chart-pinching');
      else if (zone === 'price') this.canvas.classList.add('price-axis');
      else if (zone === 'time') this.canvas.classList.add('time-axis');
    }

    visibleWindow() {
      const all = this.series?.data || [];
      const end = Math.max(0, Math.floor(all.length - this.panOffset));
      const count = clamp(Math.round(this.visibleCount), MIN_VISIBLE_BARS, MAX_VISIBLE_BARS);
      const start = Math.max(0, end - count);
      return { all, start, end, count, rows: all.slice(start, end) };
    }

    setTimeWindow(visibleCount, anchorLogical, anchorRatio) {
      const allLength = this.series?.data.length || 0;
      const nextCount = clamp(Math.round(visibleCount), MIN_VISIBLE_BARS, MAX_VISIBLE_BARS);
      const ratio = clamp(anchorRatio, 0, 1);
      const nextStart = anchorLogical - ratio * nextCount;
      const nextEnd = nextStart + nextCount;
      this.visibleCount = nextCount;
      this.panOffset = allLength - nextEnd;
      this.clampPanOffset();
      this.draw();
    }

    zoomTime(factor, anchorX) {
      const geometry = this.geometry();
      const windowState = this.visibleWindow();
      if (!windowState.all.length) return;
      const ratio = clamp((anchorX - geometry.left) / geometry.plotWidth, 0, 1);
      const anchorLogical = windowState.start + ratio * windowState.count;
      this.setTimeWindow(windowState.count * factor, anchorLogical, ratio);
    }

    onWheel(event) {
      event.preventDefault();
      const rect = this.canvas.getBoundingClientRect();
      const geometry = this.geometry();
      const x = clamp(event.clientX - rect.left, geometry.left, geometry.right);
      if (Math.abs(event.deltaX) > Math.abs(event.deltaY) && !event.ctrlKey) {
        this.panOffset += event.deltaX / geometry.plotWidth * this.visibleCount;
        this.clampPanOffset();
        this.draw();
        return;
      }
      const delta = clamp(event.deltaY, -240, 240);
      const factor = Math.exp(delta * 0.0018);
      this.zoomTime(factor, x);
    }

    onPointerDown(event) {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      const point = this.pointFromEvent(event);
      this.activePointers.set(event.pointerId, point);
      this.canvas.setPointerCapture(event.pointerId);
      this.canvas.focus({ preventScroll: true });
      event.preventDefault();

      const plotPointers = [...this.activePointers.values()].filter((item) => item.zone === 'plot');
      if (plotPointers.length >= 2) {
        this.startPinch(plotPointers[0], plotPointers[1]);
        return;
      }

      if (point.zone === 'price') this.startPriceScale(point);
      else if (point.zone === 'time') this.startTimeScale(point);
      else {
        this.gesture = { type: 'pan', startX: point.x, startOffset: this.panOffset };
        this.setInteractionClass(point.zone, 'pan');
      }
    }

    onPointerMove(event) {
      const point = this.pointFromEvent(event);
      if (!this.activePointers.has(event.pointerId)) {
        if (!this.gesture) this.setInteractionClass(point.zone);
        return;
      }

      this.activePointers.set(event.pointerId, point);
      event.preventDefault();
      const plotPointers = [...this.activePointers.values()].filter((item) => item.zone === 'plot');
      if (plotPointers.length >= 2) {
        if (this.gesture?.type !== 'pinch') this.startPinch(plotPointers[0], plotPointers[1]);
        this.updatePinch(plotPointers[0], plotPointers[1]);
        return;
      }

      if (!this.gesture) return;
      if (this.gesture.type === 'pan') {
        const geometry = this.geometry();
        const bars = (this.gesture.startX - point.x) / geometry.plotWidth * this.visibleCount;
        this.panOffset = this.gesture.startOffset + bars;
        this.clampPanOffset();
        this.draw();
      } else if (this.gesture.type === 'price') this.updatePriceScale(point);
      else if (this.gesture.type === 'time') this.updateTimeScale(point);
    }

    onPointerEnd(event) {
      const point = this.activePointers.get(event.pointerId);
      this.activePointers.delete(event.pointerId);
      try {
        if (this.canvas.hasPointerCapture(event.pointerId)) this.canvas.releasePointerCapture(event.pointerId);
      } catch (_) {
        // The browser can release capture before pointercancel/lostpointercapture arrives.
      }
      if (this.activePointers.size < 2 || this.gesture?.type !== 'pinch') this.gesture = null;
      const remaining = [...this.activePointers.values()][0];
      this.setInteractionClass(remaining?.zone || point?.zone || 'plot');
    }

    startPinch(first, second) {
      const geometry = this.geometry();
      const windowState = this.visibleWindow();
      const midpointX = (first.x + second.x) / 2;
      const ratio = clamp((midpointX - geometry.left) / geometry.plotWidth, 0, 1);
      this.gesture = {
        type: 'pinch',
        startDistance: Math.max(1, distance(first, second)),
        startCount: windowState.count,
        anchorLogical: windowState.start + ratio * windowState.count,
      };
      this.setInteractionClass('plot', 'pinch');
    }

    updatePinch(first, second) {
      if (this.gesture?.type !== 'pinch') return;
      const geometry = this.geometry();
      const currentDistance = Math.max(1, distance(first, second));
      const midpointX = (first.x + second.x) / 2;
      const ratio = clamp((midpointX - geometry.left) / geometry.plotWidth, 0, 1);
      const visibleCount = this.gesture.startCount * this.gesture.startDistance / currentDistance;
      this.setTimeWindow(visibleCount, this.gesture.anchorLogical, ratio);
    }

    startTimeScale(point) {
      const windowState = this.visibleWindow();
      const geometry = this.geometry();
      const ratio = clamp((point.x - geometry.left) / geometry.plotWidth, 0, 1);
      this.gesture = {
        type: 'time',
        startX: point.x,
        startCount: windowState.count,
        anchorLogical: windowState.start + ratio * windowState.count,
        anchorRatio: ratio,
      };
      this.setInteractionClass(point.zone, 'time');
    }

    updateTimeScale(point) {
      if (this.gesture?.type !== 'time') return;
      const delta = point.x - this.gesture.startX;
      const factor = Math.exp(-delta / 180);
      this.setTimeWindow(
        this.gesture.startCount * factor,
        this.gesture.anchorLogical,
        this.gesture.anchorRatio,
      );
    }

    autoPriceRange(rows = this.lastRows) {
      const validRows = rows || [];
      const linePrices = (this.series?.lines || []).map((line) => finite(line.price)).filter((value) => value > 0);
      const lows = validRows.map((row) => finite(row.low)).filter(Number.isFinite);
      const highs = validRows.map((row) => finite(row.high)).filter(Number.isFinite);
      let minimum = Math.min(...lows, ...linePrices);
      let maximum = Math.max(...highs, ...linePrices);
      if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return { min: 0, max: 1, span: 1 };
      if (!(maximum > minimum)) {
        maximum += 1;
        minimum -= 1;
      }
      const padding = (maximum - minimum) * 0.05;
      minimum -= padding;
      maximum += padding;
      return { min: minimum, max: maximum, span: maximum - minimum };
    }

    currentPriceRange(rows = this.lastRows) {
      return this.manualPriceRange || this.autoPriceRange(rows);
    }

    startPriceScale(point) {
      const geometry = this.geometry();
      const range = this.currentPriceRange();
      const ratio = clamp((point.y - geometry.top) / geometry.plotHeight, 0, 1);
      const anchorPrice = range.max - ratio * range.span;
      this.gesture = {
        type: 'price',
        startY: point.y,
        startSpan: range.span,
        anchorRatio: ratio,
        anchorPrice,
      };
      this.manualPriceRange = { ...range };
      this.setInteractionClass(point.zone, 'price');
    }

    updatePriceScale(point) {
      if (this.gesture?.type !== 'price') return;
      const delta = point.y - this.gesture.startY;
      const factor = Math.exp(delta / 180);
      const minimumSpan = Math.max(Math.abs(this.gesture.anchorPrice) * 1e-8, 1e-8);
      const maximumSpan = Math.max(this.gesture.startSpan * 1000, minimumSpan * 10);
      const span = clamp(this.gesture.startSpan * factor, minimumSpan, maximumSpan);
      const max = this.gesture.anchorPrice + this.gesture.anchorRatio * span;
      this.manualPriceRange = { min: max - span, max, span };
      this.draw();
    }

    onDoubleClick(event) {
      const point = this.pointFromEvent(event);
      event.preventDefault();
      if (point.zone === 'price') {
        this.resetPriceScale();
        this.draw();
      } else if (point.zone === 'time') this.fitContent();
    }

    resize() {
      const rect = this.container.getBoundingClientRect();
      const dpr = Math.max(1, window.devicePixelRatio || 1);
      const width = Math.max(1, Math.floor(rect.width));
      const height = Math.max(1, Math.floor(rect.height));
      this.canvas.width = Math.floor(width * dpr);
      this.canvas.height = Math.floor(height * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.draw();
    }

    draw() {
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
      if (!rows.length) return;

      const range = this.currentPriceRange(rows);
      const minimum = range.min;
      const maximum = range.max;
      const y = (value) => top + (maximum - value) / range.span * plotHeight;

      ctx.strokeStyle = '#202936';
      ctx.lineWidth = 1;
      ctx.font = '11px system-ui, sans-serif';
      ctx.fillStyle = textColor;
      for (let i = 0; i <= 4; i += 1) {
        const yy = top + (plotHeight * i / 4);
        ctx.beginPath();
        ctx.moveTo(left, yy);
        ctx.lineTo(right, yy);
        ctx.stroke();
        const value = maximum - range.span * i / 4;
        ctx.fillText(value.toFixed(Math.abs(value) < 1000 ? 2 : 0), right + 6, yy + 4);
      }

      ctx.strokeStyle = '#293241';
      ctx.beginPath();
      ctx.moveTo(right + 0.5, top);
      ctx.lineTo(right + 0.5, bottom);
      ctx.lineTo(left, bottom + 0.5);
      ctx.stroke();

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
        const label = `${line.title || ''} ${value.toFixed(value < 1000 ? 2 : 0)}`.trim();
        ctx.font = '11px system-ui, sans-serif';
        const labelWidth = ctx.measureText(label).width + 8;
        ctx.fillRect(right - labelWidth, yy - 8, labelWidth, 16);
        ctx.fillStyle = '#081016';
        ctx.fillText(label, right - labelWidth + 4, yy + 4);
        ctx.restore();
      }

      const firstTime = new Date(finite(rows[0].time) * 1000);
      const lastTime = new Date(finite(rows.at(-1).time) * 1000);
      ctx.fillStyle = textColor;
      ctx.font = '11px system-ui, sans-serif';
      ctx.fillText(firstTime.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }), left, height - 7);
      const lastLabel = lastTime.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
      ctx.fillText(lastLabel, Math.max(left, right - ctx.measureText(lastLabel).width), height - 7);
    }
  }

  window.LightweightCharts = Object.freeze({
    createChart: (container, options) => new LocalChart(container, options),
    CandlestickSeries,
    CrosshairMode,
    LineStyle,
  });
})();
