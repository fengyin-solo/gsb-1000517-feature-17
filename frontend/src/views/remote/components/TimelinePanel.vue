<template>
  <div class="timeline-panel">
    <div class="panel-head">
      <strong>时间轴</strong>
      <span class="hint">按有效版本获取月份聚合，点击月份收窄日期，点击影像联动地图</span>
    </div>
    <div v-if="!months.length" class="empty">当前定位条件下没有影像</div>
    <ol v-else class="timeline">
      <li v-for="month in months" :key="month.month" class="month-row">
        <button
          type="button"
          :class="['month-badge', { active: conditions.date_from === `${month.month}-01` }]"
          @click="toggleMonth(month.month)"
        >
          {{ month.month }}
          <small>{{ month.series_count }} 景</small>
        </button>
        <ul class="series-list">
          <li v-for="item in month.items" :key="item.series_key" class="series-item">
            <button
              type="button"
              :class="['series-chip', { selected: selected === item.series_key }]"
              @click="$emit('select-series', item.series_key)"
            >
              <span class="series-name">{{ item.series_key }}</span>
              <span class="chip-meta">
                {{ item.effective?.resolution ?? '无有效版本' }} · {{ item.effective?.source ?? '—' }}
              </span>
              <span class="chip-flags">
                <i v-if="item.multi_version" class="tag tag-multi">{{ item.version_count }}版本</i>
                <i v-if="item.resolution_gap" class="tag tag-gap">分辨率缺口</i>
                <i v-if="item.undispatched_count" class="tag tag-pending">
                  {{ item.undispatched_count }}图斑待派发
                </i>
              </span>
            </button>
          </li>
        </ul>
      </li>
    </ol>
  </div>
</template>

<script setup lang="ts">
import type { LocatorConditions, TimelineMonth } from '../types'

defineProps<{
  months: TimelineMonth[]
  conditions: LocatorConditions
  selected: string | null
}>()

const emit = defineEmits<{
  (e: 'select-series', seriesKey: string): void
  (e: 'toggle-month', dateFrom: string): void
}>()

function toggleMonth(month: string) {
  emit('toggle-month', `${month}-01`)
}
</script>

<style scoped>
.timeline-panel {
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
.hint, .empty {
  color: var(--muted);
  font-size: 12px;
}
.timeline {
  list-style: none;
  margin: 0;
  padding: 0;
}
.month-row {
  display: flex;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px dashed var(--border);
}
.month-row:last-child { border-bottom: none; }
.month-badge {
  flex: 0 0 86px;
  border: 1px solid var(--border);
  background: #f8fafc;
  border-radius: 6px;
  padding: 6px 8px;
  cursor: pointer;
  text-align: left;
  font-weight: 600;
  font-size: 13px;
}
.month-badge small {
  display: block;
  color: var(--muted);
  font-weight: 400;
}
.month-badge.active {
  background: var(--brand);
  border-color: var(--brand);
  color: #fff;
}
.month-badge.active small { color: #dbeafe; }
.series-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
}
.series-chip {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  text-align: left;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: #fff;
  padding: 6px 10px;
  cursor: pointer;
}
.series-chip.selected {
  border-color: var(--brand);
  box-shadow: 0 0 0 2px rgba(31, 111, 235, 0.15);
}
.series-name { font-weight: 600; font-size: 13px; }
.chip-meta { color: var(--muted); font-size: 12px; }
.chip-flags { margin-left: auto; display: flex; gap: 4px; }
.tag {
  font-style: normal;
  font-size: 11px;
  border-radius: 4px;
  padding: 1px 6px;
}
.tag-multi { background: #ede9fe; color: #6d28d9; }
.tag-gap { background: #fee2e2; color: #b91c1c; }
.tag-pending { background: #fef3c7; color: #b45309; }
</style>
