<template>
  <div class="map-panel">
    <div class="panel-head">
      <strong>地图联动</strong>
      <span class="hint">点击网格按单元定位；点击影像框定位同源系列（阈值 ≤ {{ data.gap_threshold_meters }}m）</span>
    </div>
    <svg class="map-canvas" viewBox="0 0 100 100" preserveAspectRatio="none" @click.self="clearCell">
      <!-- 网格单元 -->
      <g class="grid-layer">
        <rect
          v-for="cell in cells"
          :key="cell.id"
          :x="cell.x"
          :y="cell.y"
          :width="cell.size"
          :height="cell.size"
          :class="['grid-cell', { active: conditions.cell === cell.id }]"
          @click.stop="selectCell(cell.id)"
        />
      </g>
      <!-- 有效影像覆盖范围 -->
      <g class="feature-layer">
        <rect
          v-for="feature in data.features"
          :key="feature.series_key"
          :x="feature.footprint.x"
          :y="feature.footprint.y"
          :width="feature.footprint.w"
          :height="feature.footprint.h"
          :class="['feature-box', flagClass(feature.flags), { selected: selected === feature.series_key }]"
          @click.stop="selectSeries(feature.series_key)"
        >
          <title>{{ feature.series_key }} · {{ feature.resolution }}</title>
        </rect>
        <text
          v-for="feature in data.features"
          :key="`${feature.series_key}-label`"
          :x="feature.footprint.x + 1.5"
          :y="feature.footprint.y + 6"
          class="feature-label"
          @click.stop="selectSeries(feature.series_key)"
        >{{ feature.series_key }}</text>
      </g>
      <!-- 图斑位置 -->
      <g class="parcel-layer">
        <rect
          v-for="parcel in data.parcels"
          :key="parcel.id"
          :x="parcel.footprint.x"
          :y="parcel.footprint.y"
          :width="parcel.footprint.w"
          :height="parcel.footprint.h"
          :class="['parcel-box', `parcel-${parcelStatusClass(parcel.status)}`]"
        >
          <title>{{ parcel.code }} · {{ parcel.status }}</title>
        </rect>
      </g>
    </svg>
    <ul class="legend">
      <li><i class="dot dot-multi"></i>同源多版本</li>
      <li><i class="dot dot-gap"></i>分辨率缺口</li>
      <li><i class="dot dot-pending"></i>未派发图斑</li>
      <li><i class="dot dot-cell"></i>当前网格条件</li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { LocatorConditions, MapData, QueueFlag } from '../types'

const props = defineProps<{
  data: MapData
  conditions: LocatorConditions
  selected: string | null
}>()

const emit = defineEmits<{
  (e: 'select-cell', cell: string): void
  (e: 'select-series', seriesKey: string): void
  (e: 'clear-cell'): void
}>()

const cells = computed(() => {
  const size = props.data.grid_size
  const list: { id: string; x: number; y: number; size: number }[] = []
  for (let gx = 0; gx < 100; gx += size) {
    for (let gy = 0; gy < 100; gy += size) {
      list.push({ id: `${gx / size},${gy / size}`, x: gx, y: gy, size })
    }
  }
  return list
})

function flagClass(flags: QueueFlag[]) {
  if (flags.includes('resolution_gap')) return 'box-gap'
  if (flags.includes('undispatched')) return 'box-pending'
  if (flags.includes('multi_version')) return 'box-multi'
  return 'box-ok'
}

function parcelStatusClass(status: string) {
  if (status === '待派发') return 'pending'
  if (status === '已派发') return 'dispatched'
  return 'done'
}

function selectCell(id: string) {
  emit('select-cell', props.conditions.cell === id ? '' : id)
}

function selectSeries(seriesKey: string) {
  emit('select-series', seriesKey)
}

function clearCell() {
  emit('clear-cell')
}
</script>

<style scoped>
.map-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 8px;
}
.hint {
  color: var(--muted);
  font-size: 12px;
}
.map-canvas {
  width: 100%;
  height: 360px;
  background: #f1f5f9;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: crosshair;
}
.grid-cell {
  fill: transparent;
  stroke: #cbd5e1;
  stroke-width: 0.3;
  cursor: pointer;
}
.grid-cell:hover {
  fill: rgba(31, 111, 235, 0.08);
}
.grid-cell.active {
  fill: rgba(31, 111, 235, 0.18);
  stroke: var(--brand);
  stroke-width: 0.6;
}
.feature-box {
  stroke-width: 0.8;
  cursor: pointer;
  fill-opacity: 0.25;
}
.feature-box.selected {
  stroke-width: 1.6;
  stroke-dasharray: 2 1;
}
.box-ok { fill: #16a34a; stroke: #15803d; }
.box-multi { fill: #7c3aed; stroke: #6d28d9; }
.box-gap { fill: #dc2626; stroke: #b91c1c; }
.box-pending { fill: #d97706; stroke: #b45309; }
.feature-label {
  font-size: 4px;
  fill: #0f172a;
  pointer-events: none;
  font-weight: 600;
}
.parcel-box {
  stroke: #1f2937;
  stroke-width: 0.25;
  fill-opacity: 0.85;
  pointer-events: none;
}
.parcel-pending { fill: #f59e0b; }
.parcel-dispatched { fill: #38bdf8; }
.parcel-done { fill: #22c55e; }
.legend {
  display: flex;
  gap: 14px;
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  font-size: 12px;
  color: var(--muted);
}
.dot {
  display: inline-block;
  width: 9px;
  height: 9px;
  border-radius: 2px;
  margin-right: 4px;
}
.dot-multi { background: #7c3aed; }
.dot-gap { background: #dc2626; }
.dot-pending { background: #d97706; }
.dot-cell { background: rgba(31, 111, 235, 0.35); }
</style>
