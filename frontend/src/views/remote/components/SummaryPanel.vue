<template>
  <div class="summary-panel">
    <div class="panel-head">
      <strong>定位结论汇总</strong>
      <span class="hint">
        定位号：{{ summary.locator_id ?? '未固化（当前会话条件）' }} ·
        条件：{{ conditionText || '全部影像' }}
      </span>
    </div>

    <section class="sum-block">
      <h3>遥感清单</h3>
      <div class="sum-cards">
        <button
          v-for="group in summary.remote_list.groups"
          :key="group.key"
          type="button"
          class="sum-card"
          @click="$emit('apply-flag', group.key ?? 'all')"
        >
          <span class="sum-label">{{ group.label }}</span>
          <strong class="sum-value">{{ group.count }}</strong>
        </button>
      </div>
      <table class="data-table">
        <thead>
          <tr><th>同源系列</th><th>有效版本</th><th>标记</th><th>未派发</th><th>存档目录</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in summary.remote_list.items" :key="item.series_key">
            <td>
              <button type="button" class="link" @click="$emit('locate-series', item.series_key)">
                {{ item.series_key }}
              </button>
            </td>
            <td>{{ item.effective?.version ?? '—' }}（{{ item.effective?.resolution ?? '无有效版本' }}）</td>
            <td>
              <i v-if="item.multi_version" class="tag tag-multi">多版本</i>
              <i v-if="item.resolution_gap" class="tag tag-gap">缺口</i>
            </td>
            <td>{{ item.undispatched_count }}</td>
            <td class="mono">{{ item.effective?.archive_path ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="sum-block">
      <h3>图斑待办</h3>
      <div class="sum-cards">
        <button
          v-for="group in summary.parcel_todo.groups"
          :key="group.key"
          type="button"
          class="sum-card"
          @click="$emit('open-parcels-status', group.key ?? '')"
        >
          <span class="sum-label">{{ group.label }}</span>
          <strong class="sum-value">{{ group.count }}</strong>
        </button>
      </div>
    </section>

    <section class="sum-block">
      <h3>存档目录</h3>
      <table class="data-table">
        <thead>
          <tr><th>存档路径</th><th>同源系列数</th><th>版本总数</th><th>有效版本</th><th>仅追溯旧版</th></tr>
        </thead>
        <tbody>
          <tr v-for="group in summary.archive_catalog.groups" :key="group.archive_path">
            <td class="mono">{{ group.archive_path }}</td>
            <td>{{ group.series_count }}</td>
            <td>{{ group.versions }}</td>
            <td><i class="tag tag-ok">{{ group.effective }}</i></td>
            <td>{{ group.trace_only }}</td>
          </tr>
          <tr v-if="!summary.archive_catalog.groups.length">
            <td colspan="5" class="empty-state">当前定位条件下没有存档</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { LocatorConditions, Summary } from '../types'

const props = defineProps<{
  summary: Summary
}>()

defineEmits<{
  (e: 'apply-flag', flag: string): void
  (e: 'open-parcels-status', status: string): void
  (e: 'locate-series', seriesKey: string): void
}>()

const conditionText = computed(() => {
  const cond = props.summary.conditions as LocatorConditions & { bbox?: number[] }
  const parts = [
    cond.keyword && `关键字 ${cond.keyword}`,
    cond.source && `数据源 ${cond.source}`,
    cond.date_from && `起 ${cond.date_from}`,
    cond.date_to && `止 ${cond.date_to}`,
    cond.cell && `网格 ${cond.cell}`,
    cond.flag && `旗标 ${cond.flag}`,
  ].filter(Boolean)
  return parts.join(' · ')
})
</script>

<style scoped>
.panel-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; }
.hint { color: var(--muted); font-size: 12px; }
.sum-block {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.sum-block h3 { margin: 0 0 8px; font-size: 14px; }
.sum-cards { display: flex; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.sum-card {
  min-width: 120px;
  text-align: left;
  border: 1px solid var(--border);
  background: #f8fafc;
  border-radius: 6px;
  padding: 8px 10px;
  cursor: pointer;
}
.sum-card:hover { border-color: var(--brand); }
.sum-label { display: block; color: var(--muted); font-size: 12px; }
.sum-value { font-size: 20px; }
.tag { font-style: normal; font-size: 11px; border-radius: 4px; padding: 1px 6px; }
.tag-multi { background: #ede9fe; color: #6d28d9; }
.tag-gap { background: #fee2e2; color: #b91c1c; }
.tag-ok { background: #dcfce7; color: #15803d; }
.mono { font-family: ui-monospace, monospace; font-size: 12px; }
</style>
