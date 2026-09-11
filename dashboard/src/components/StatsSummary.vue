<script setup lang="ts">
// Aggregate stats + trend charts, fetched once from GET /api/stats/summary.
import { ref, computed, onMounted } from 'vue'
import { fetchStatsSummary, type StatsSummary, type TrendPoint } from '../api'

const stats = ref<StatsSummary | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    stats.value = await fetchStatsSummary()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
})

// API returns newest-first (LIMIT on a DESC query); charts read left-to-right.
const trend = computed<TrendPoint[]>(() => {
  const t = stats.value?.trend ?? []
  return [...t].reverse()
})

// ---- stat tile formatting -------------------------------------------------

function formatLatency(ms: number | null): string {
  if (ms === null) return '—'
  return `${(ms / 1000).toFixed(1)}s`
}

function formatPercent(fraction: number | null): string {
  if (fraction === null) return '—'
  return `${(fraction * 100).toFixed(0)}%`
}

function formatCost(usd: number | null): string {
  if (usd === null) return '—'
  return `$${usd.toFixed(4)}`
}

const gatePassRateLabel = computed(() => {
  if (!stats.value || stats.value.gate_scored_runs === 0) return 'not scored yet'
  return `of ${stats.value.gate_scored_runs} scored`
})

// Shared chart geometry. Both charts use a fixed-size SVG viewBox scaled to
// fit the card via CSS — everything below is in viewBox units, not pixels.
const MARGIN = { top: 8, right: 8, bottom: 20, left: 34 }
const PLOT_WIDTH = 480
const PLOT_HEIGHT = 130
const VIEW_WIDTH = MARGIN.left + PLOT_WIDTH + MARGIN.right
const VIEW_HEIGHT = MARGIN.top + PLOT_HEIGHT + MARGIN.bottom

// 'YYYY-MM-DD' parses as UTC midnight; appending a local time avoids that
// printing as the previous day for anyone west of UTC.
function formatDay(day: string): string {
  const d = new Date(`${day}T00:00:00`)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

// Which x-axis ticks get a label — labeling all 30 days would be unreadable.
function labeledIndices(n: number): Set<number> {
  if (n <= 6) return new Set(Array.from({ length: n }, (_, i) => i))
  const step = Math.ceil(n / 6)
  const idx = new Set<number>()
  for (let i = 0; i < n; i += step) idx.add(i)
  idx.add(n - 1)
  return idx
}

// Bar chart: runs/day
const hoveredBar = ref<number | null>(null)

const barChart = computed(() => {
  const points = trend.value
  const n = points.length
  const maxRuns = Math.max(1, ...points.map((p) => p.runs))
  const slot = n > 0 ? PLOT_WIDTH / n : 0
  const barWidth = Math.min(24, Math.max(2, slot - 4)) // 4px gap between bars, 24px cap per the skill's bar spec
  const labeled = labeledIndices(n)

  const bars = points.map((p, i) => {
    const h = (p.runs / maxRuns) * PLOT_HEIGHT
    return {
      x: MARGIN.left + i * slot + (slot - barWidth) / 2,
      y: MARGIN.top + (PLOT_HEIGHT - h),
      width: barWidth,
      height: h,
      value: p.runs,
      day: p.day,
      label: formatDay(p.day),
      showXLabel: labeled.has(i),
      tickX: MARGIN.left + i * slot + slot / 2,
    }
  })

  // Two y-ticks (0 and rounded max) — simple, fine for small integer counts.
  const niceMax = Math.max(1, Math.ceil(maxRuns))
  const yTicks = [
    { value: 0, y: MARGIN.top + PLOT_HEIGHT },
    { value: niceMax, y: MARGIN.top },
  ]

  return { bars, yTicks }
})

// noUncheckedIndexedAccess makes array[i] possibly-undefined — resolve once
// here instead of re-indexing (and re-checking) in the template.
const hoveredBarData = computed(() => {
  if (hoveredBar.value === null) return null
  return barChart.value.bars[hoveredBar.value] ?? null
})

// Line chart: avg latency/day
const hoveredLine = ref<number | null>(null)

const lineChart = computed(() => {
  const points = trend.value
  const n = points.length
  const maxLatency = Math.max(1, ...points.map((p) => p.avg_latency_ms))
  const stepX = n > 1 ? PLOT_WIDTH / (n - 1) : 0
  const labeled = labeledIndices(n)

  const pts = points.map((p, i) => {
    const x = MARGIN.left + (n > 1 ? i * stepX : PLOT_WIDTH / 2)
    const y = MARGIN.top + PLOT_HEIGHT - (p.avg_latency_ms / maxLatency) * PLOT_HEIGHT
    return {
      x,
      y,
      value: p.avg_latency_ms,
      day: p.day,
      label: formatDay(p.day),
      showXLabel: labeled.has(i),
    }
  })

  const path = pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ')

  const niceMax = Math.max(1, Math.ceil(maxLatency))
  const yTicks = [
    { value: 0, y: MARGIN.top + PLOT_HEIGHT },
    { value: niceMax, y: MARGIN.top },
  ]

  return { points: pts, path, yTicks }
})

// Same as hoveredBarData above.
const hoveredLineData = computed(() => {
  if (hoveredLine.value === null) return null
  return lineChart.value.points[hoveredLine.value] ?? null
})

const lastLinePoint = computed(() => {
  const pts = lineChart.value.points
  return pts.length > 0 ? (pts[pts.length - 1] ?? null) : null
})

// One invisible hit-strip over the whole plot, instead of 30 tiny per-dot targets.
function onLinePointerMove(evt: MouseEvent) {
  const svg = evt.currentTarget as SVGRectElement
  const rect = svg.getBoundingClientRect()
  const fracX = (evt.clientX - rect.left) / rect.width
  const svgX = fracX * VIEW_WIDTH
  const points = lineChart.value.points
  if (points.length === 0) return
  let nearest = 0
  let nearestDist = Infinity
  points.forEach((p, i) => {
    const d = Math.abs(p.x - svgX)
    if (d < nearestDist) {
      nearestDist = d
      nearest = i
    }
  })
  hoveredLine.value = nearest
}

// Table view — same trend data, no hover required.
const showTable = ref(false)
</script>

<template>
  <div class="stats-summary">
    <p v-if="loading">Loading stats...</p>
    <p v-else-if="error" class="error">Failed to load stats: {{ error }}</p>

    <template v-else-if="stats">
      <div class="tiles">
        <div class="tile">
          <p class="tile-label">Total runs</p>
          <p class="tile-value">{{ stats.total_runs.toLocaleString() }}</p>
        </div>
        <div class="tile">
          <p class="tile-label">Gate pass rate</p>
          <p class="tile-value">{{ formatPercent(stats.gate_pass_rate) }}</p>
          <p class="tile-sub">{{ gatePassRateLabel }}</p>
        </div>
        <div class="tile">
          <p class="tile-label">Avg latency</p>
          <p class="tile-value">{{ formatLatency(stats.avg_latency_ms) }}</p>
        </div>
        <div class="tile">
          <p class="tile-label">Avg cost (actual)</p>
          <p class="tile-value">{{ formatCost(stats.avg_real_cost_usd) }}</p>
        </div>
        <div class="tile">
          <p class="tile-label">Avg cost (GPT-4o reference)</p>
          <p class="tile-value">{{ formatCost(stats.avg_reference_cost_usd) }}</p>
        </div>
      </div>

      <div class="trend-section">
        <div class="trend-header">
          <h3>Trend</h3>
          <button type="button" class="toggle-btn" @click="showTable = !showTable">
            {{ showTable ? 'View as chart' : 'View as table' }}
          </button>
        </div>

        <p v-if="trend.length === 0" class="empty">No trend data yet.</p>

        <table v-else-if="showTable" class="trend-table">
          <thead>
            <tr>
              <th>Day</th>
              <th>Runs</th>
              <th>Avg latency</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in trend" :key="p.day">
              <td>{{ formatDay(p.day) }}</td>
              <td>{{ p.runs }}</td>
              <td>{{ formatLatency(p.avg_latency_ms) }}</td>
            </tr>
          </tbody>
        </table>

        <div v-else class="charts">
          <!-- Bar chart: runs/day -->
          <figure class="chart-card">
            <figcaption>Runs / day</figcaption>
            <svg :viewBox="`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`" class="chart-svg">
              <!-- gridlines + y-axis labels -->
              <g v-for="tick in barChart.yTicks" :key="`gy-${tick.value}`">
                <line
                  :x1="MARGIN.left"
                  :x2="VIEW_WIDTH - MARGIN.right"
                  :y1="tick.y"
                  :y2="tick.y"
                  class="gridline"
                />
                <text :x="MARGIN.left - 6" :y="tick.y" class="axis-label y-label">{{ tick.value }}</text>
              </g>

              <!-- each bar is its own hover/focus target -->
              <g v-for="(bar, i) in barChart.bars" :key="bar.day">
                <rect
                  :x="bar.x"
                  :y="bar.y"
                  :width="bar.width"
                  :height="bar.height"
                  rx="4"
                  class="bar"
                  :class="{ hovered: hoveredBar === i }"
                  tabindex="0"
                  @pointerenter="hoveredBar = i"
                  @pointerleave="hoveredBar = null"
                  @focus="hoveredBar = i"
                  @blur="hoveredBar = null"
                >
                  <title>{{ bar.label }}: {{ bar.value }} run{{ bar.value === 1 ? '' : 's' }}</title>
                </rect>
                <text v-if="bar.showXLabel" :x="bar.tickX" :y="VIEW_HEIGHT - 4" class="axis-label x-label">
                  {{ bar.label }}
                </text>
              </g>

              <!-- tooltip: drawn last so it sits on top of everything else -->
              <g v-if="hoveredBarData" class="tooltip">
                <g :transform="`translate(${hoveredBarData.x + hoveredBarData.width / 2}, ${hoveredBarData.y - 10})`">
                  <rect x="-34" y="-20" width="68" height="20" rx="4" class="tooltip-bg" />
                  <text x="0" y="-6" class="tooltip-text">
                    {{ hoveredBarData.value }} run{{ hoveredBarData.value === 1 ? '' : 's' }}
                  </text>
                </g>
              </g>
            </svg>
          </figure>

          <!-- Line chart: avg latency/day -->
          <figure class="chart-card">
            <figcaption>Avg latency / day</figcaption>
            <svg :viewBox="`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`" class="chart-svg">
              <g v-for="tick in lineChart.yTicks" :key="`gy-${tick.value}`">
                <line
                  :x1="MARGIN.left"
                  :x2="VIEW_WIDTH - MARGIN.right"
                  :y1="tick.y"
                  :y2="tick.y"
                  class="gridline"
                />
                <text :x="MARGIN.left - 6" :y="tick.y" class="axis-label y-label">
                  {{ (tick.value / 1000).toFixed(0) }}s
                </text>
              </g>

              <text
                v-for="p in lineChart.points.filter((p) => p.showXLabel)"
                :key="`x-${p.day}`"
                :x="p.x"
                :y="VIEW_HEIGHT - 4"
                class="axis-label x-label"
              >
                {{ p.label }}
              </text>

              <path :d="lineChart.path" class="line" />

              <!-- small dot at every point, bigger + filled on hover -->
              <circle
                v-for="(p, i) in lineChart.points"
                :key="p.day"
                :cx="p.x"
                :cy="p.y"
                :r="hoveredLine === i ? 4 : 2.5"
                class="dot"
                :class="{ hovered: hoveredLine === i }"
              />

              <!-- value label at the last point -->
              <text
                v-if="lastLinePoint"
                :x="lastLinePoint.x"
                :y="lastLinePoint.y - 8"
                class="end-label"
                text-anchor="end"
              >
                {{ formatLatency(lastLinePoint.value) }}
              </text>

              <!-- crosshair hit-strip -->
              <rect
                :x="MARGIN.left"
                :y="MARGIN.top"
                :width="PLOT_WIDTH"
                :height="PLOT_HEIGHT"
                class="hit-strip"
                @pointermove="onLinePointerMove"
                @pointerleave="hoveredLine = null"
              />

              <g v-if="hoveredLineData">
                <line
                  :x1="hoveredLineData.x"
                  :x2="hoveredLineData.x"
                  :y1="MARGIN.top"
                  :y2="MARGIN.top + PLOT_HEIGHT"
                  class="crosshair"
                />
                <g :transform="`translate(${hoveredLineData.x}, ${hoveredLineData.y - 10})`">
                  <rect x="-40" y="-20" width="80" height="20" rx="4" class="tooltip-bg" />
                  <text x="0" y="-6" class="tooltip-text">
                    {{ hoveredLineData.label }}: {{ formatLatency(hoveredLineData.value) }}
                  </text>
                </g>
              </g>
            </svg>
          </figure>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* Chrome reuses assets/base.css's theme tokens (dark mode included).
   The chart blue isn't one of those tokens, so it's defined locally. */
.stats-summary {
  --series-1: #2a78d6;
  --gridline: #e1e0d9;
  --axis-ink: #898781;
  margin-bottom: 2.5rem;
}
@media (prefers-color-scheme: dark) {
  .stats-summary {
    --series-1: #3987e5;
    --gridline: #2c2c2a;
    --axis-ink: #898781;
  }
}

.error {
  color: #b00020;
}

.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 1rem;
  margin-bottom: 2rem;
}

.tile {
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 0.75rem 1rem;
}

.tile-label {
  font-size: 0.8rem;
  color: var(--color-text);
  opacity: 0.7;
}

.tile-value {
  font-size: 1.6rem;
  font-weight: 600;
  margin-top: 0.15rem;
}

.tile-sub {
  font-size: 0.75rem;
  opacity: 0.6;
  margin-top: 0.1rem;
}

.trend-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}

.toggle-btn {
  font-size: 0.85rem;
  padding: 0.3rem 0.7rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: transparent;
  color: var(--color-text);
  cursor: pointer;
}

.toggle-btn:hover {
  border-color: var(--color-border-hover);
}

.empty {
  opacity: 0.7;
}

.charts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 1.5rem;
}

.chart-card {
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 0.75rem;
}

.chart-card figcaption {
  font-size: 0.85rem;
  opacity: 0.75;
  margin-bottom: 0.25rem;
}

.chart-svg {
  width: 100%;
  height: auto;
  display: block;
  overflow: visible;
}

.gridline {
  stroke: var(--gridline);
  stroke-width: 1;
}

.axis-label {
  font-size: 9px;
  fill: var(--axis-ink);
}

.y-label {
  text-anchor: end;
  dominant-baseline: middle;
}

.x-label {
  text-anchor: middle;
}

.bar {
  fill: var(--series-1);
  cursor: pointer;
  transition: filter 0.1s;
}

.bar:focus {
  outline: none;
}

.bar.hovered {
  filter: brightness(1.15);
}

.line {
  fill: none;
  stroke: var(--series-1);
  stroke-width: 2;
  stroke-linejoin: round;
  stroke-linecap: round;
}

.dot {
  fill: var(--series-1);
  stroke: var(--color-background);
  stroke-width: 2;
}

.dot.hovered {
  fill: var(--color-background);
  stroke: var(--series-1);
}

.end-label {
  font-size: 10px;
  font-weight: 600;
  fill: var(--color-text);
}

.hit-strip {
  fill: transparent;
  cursor: crosshair;
}

.crosshair {
  stroke: var(--axis-ink);
  stroke-width: 1;
  stroke-dasharray: 2 2;
}

.tooltip-bg {
  fill: var(--color-text);
  opacity: 0.85;
}

.tooltip-text {
  fill: var(--color-background);
  font-size: 10px;
  text-anchor: middle;
}

.trend-table {
  width: 100%;
  border-collapse: collapse;
}

.trend-table th,
.trend-table td {
  text-align: left;
  padding: 0.4rem 0.75rem;
  border-bottom: 1px solid var(--color-border);
  font-variant-numeric: tabular-nums;
}
</style>
