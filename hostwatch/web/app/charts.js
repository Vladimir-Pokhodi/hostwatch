// Load charts on uPlot (vendored, global `uPlot`): Telegram theme colours, the owner's time
// zone, a break in the line where data is missing, a readout on tap instead of a legend.
import { clock, dayMonth, getZone, monthShort, number } from "./ui.js";

const live = new Set();

export function destroyCharts() {
  for (const chart of live) chart.destroy();
  live.clear();
}

addEventListener("resize", () => {
  for (const chart of live) {
    const width = chart.root.parentElement?.clientWidth;
    if (width && Math.abs(width - chart.width) > 2) chart.setSize({ width, height: chart.height });
  }
});

// Any CSS colour (var(), color-mix()) as [r, g, b], read back through a canvas.
const probe = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
function rgb(css) {
  const el = document.createElement("i");
  el.style.color = css;
  document.body.append(el);
  const computed = getComputedStyle(el).color;
  el.remove();
  probe.clearRect(0, 0, 1, 1);
  probe.fillStyle = "#000";
  probe.fillStyle = computed;
  probe.fillRect(0, 0, 1, 1);
  const [r, g, b] = probe.getImageData(0, 0, 1, 1).data;
  return [r, g, b];
}
const rgba = ([r, g, b], a) => `rgba(${r},${g},${b},${a})`;

function axisDates(splits) {
  const step = splits.length > 1 ? splits[1] - splits[0] : 3600;
  return splits.map((v) => {
    if (step < 86400) {
      const time = clock(v);
      return time === "00:00" ? dayMonth(v) : time;
    }
    if (step < 28 * 86400) return dayMonth(v);
    return monthShort(v);
  });
}

/**
 * lines: [{ label, values, color, peak? }]; `peak` draws the hour's peak as a lighter
 * band above the average. `range` is [start, end] in unix seconds: the axis shows the
 * whole span asked for, so a history that started yesterday looks like one.
 */
export function lineChart(target, { t: xs, lines, percent, range, onCursor, height = 150 }) {
  const hint = rgb("var(--hint)");
  const sep = rgb("var(--sep)");
  const font = `11px ${getComputedStyle(document.body).fontFamily}`;
  const sparse = xs.length < 40;
  const data = [xs];
  const series = [{}];
  const bands = [];
  for (const line of lines) {
    const colour = rgb(line.color);
    if (line.peak) {
      data.push(line.peak);
      series.push({ label: `${line.label}+`, stroke: rgba(colour, 0.45), width: 1, points: { show: false } });
    }
    data.push(line.values);
    series.push({
      label: line.label,
      stroke: rgba(colour, 1),
      width: 2,
      fill: lines.length === 1 && !line.peak ? rgba(colour, 0.1) : undefined,
      points: { show: sparse, size: 5, fill: rgba(colour, 1) },
    });
    if (line.peak) bands.push({ series: [series.length - 2, series.length - 1], fill: rgba(colour, 0.22) });
  }
  const opts = {
    width: target.clientWidth || 320,
    height,
    padding: [8, 16, 0, 0],
    legend: { show: false },
    tzDate: (ts) => uPlot.tzDate(new Date(ts * 1e3), getZone()),
    cursor: {
      y: false,
      drag: { x: false, y: false, setScale: false },
      points: { size: 7 },
    },
    scales: {
      x: { time: true, range: () => range },
      y: percent
        ? { range: [0, 100] }
        : { range: (u, min, max) => [0, max > 0 ? max * 1.15 : 1] },
    },
    axes: [
      {
        stroke: rgba(hint, 1),
        font,
        size: 26,
        space: 56,
        gap: 4,
        ticks: { show: false },
        grid: { show: false },
        values: (u, splits) => axisDates(splits),
      },
      {
        stroke: rgba(hint, 1),
        font,
        size: 34,
        space: 28,
        gap: 4,
        ticks: { show: false },
        grid: { stroke: rgba(sep, 1), width: 1 },
        values: (u, splits) => splits.map((v) => number(v, v < 10 && !percent ? 1 : 0)),
      },
    ],
    series,
    bands,
    hooks: { setCursor: [(u) => onCursor?.(u.cursor.idx ?? null)] },
  };
  const chart = new uPlot(opts, data, target);
  live.add(chart);
  // Phones: a finger moves the readout; vertical swipes still scroll the page.
  const over = chart.over;
  const follow = (event) => {
    const touch = event.touches[0];
    if (!touch) return;
    const box = over.getBoundingClientRect();
    chart.setCursor({ left: touch.clientX - box.left, top: touch.clientY - box.top });
  };
  over.addEventListener("touchstart", follow, { passive: true });
  over.addEventListener("touchmove", follow, { passive: true });
  return chart;
}
