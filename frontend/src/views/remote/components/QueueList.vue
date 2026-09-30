<template>
  <div class="queue-list">
    <div class="panel-head">
      <strong>遥感清单 · 核查队列</strong>
      <span class="hint">同源以审核通过版本为准，旧版仅追溯；翻页使用游标，不重不漏</span>
    </div>
    <table class="data-table">
      <thead>
        <tr>
          <th>同源系列</th>
          <th>有效版本</th>
          <th>数据源/分辨率</th>
          <th>获取日期</th>
          <th>核查标记</th>
          <th>待派发图斑</th>
          <th>版本追溯</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="item in items"
          :key="item.series_key"
          :class="{ selected: item.series_key === selected }"
          @click="$emit('select-series', item.series_key)"
        >
          <td>
            <strong>{{ item.series_key }}</strong>
            <div v-if="!item.effective_passed" class="warn">尚无审核通过版本</div>
          </td>
          <td>
            <template v-if="item.effective">
              {{ item.effective.version }} · {{ item.effective.data_code }}
              <div class="muted">{{ item.effective.review_status }}</div>
            </template>
            <span v-else class="warn">—</span>
          </td>
          <td>{{ item.effective?.source ?? '—' }} · {{ item.effective?.resolution ?? '—' }}</td>
          <td>{{ item.effective?.acquired_at ?? '—' }}</td>
          <td>
            <i v-if="item.multi_version" class="tag tag-multi">同源 {{ item.version_count }} 版本</i>
            <i v-if="item.resolution_gap" class="tag tag-gap">
              缺口 {{ item.gap_cells.join('、') }}
            </i>
            <span v-if="!item.multi_version && !item.resolution_gap" class="muted">正常</span>
          </td>
          <td>
            <button
              v-if="item.undispatched_count"
              type="button"
              class="link"
              @click.stop="$emit('open-parcels', item.undispatched_parcel_ids)"
            >
              {{ item.undispatched_count }} 个未派发
            </button>
            <span v-else class="muted">0</span>
          </td>
          <td>
            <button type="button" class="link" @click.stop="toggle(item.series_key)">
              {{ expanded[item.series_key] ? '收起' : `查看 ${item.version_count} 个版本` }}
            </button>
          </td>
        </tr>
        <tr v-if="!items.length">
          <td colspan="7" class="empty-state">当前定位条件下没有待核查影像</td>
        </tr>
      </tbody>
    </table>

    <!-- 版本追溯展开行 -->
    <div v-for="item in expandedItems" :key="`${item.series_key}-history`" class="history-box">
      <h4>{{ item.series_key }} 版本链（旧版仅追溯）</h4>
      <table class="data-table inner">
        <thead>
          <tr><th>版本</th><th>数据编号</th><th>分辨率</th><th>获取日期</th><th>质量审核</th><th>存档目录</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="ver in item.history" :key="ver.id" :class="{ effective: ver.is_effective }">
            <td>{{ ver.version }}<i v-if="ver.is_effective" class="tag tag-ok">有效</i></td>
            <td>{{ ver.data_code }}</td>
            <td>{{ ver.resolution }}</td>
            <td>{{ ver.acquired_at }}</td>
            <td>
              {{ ver.review_status }}
              <span v-if="ver.review_note" class="muted">（{{ ver.review_note }}）</span>
            </td>
            <td class="mono">{{ ver.archive_path }}</td>
            <td class="row-actions">
              <button
                v-if="ver.review_status === '待审核'"
                type="button"
                class="link"
                @click="$emit('review', ver.id, '审核通过')"
              >审核通过</button>
              <button
                v-if="ver.review_status === '待审核'"
                type="button"
                class="link danger"
                @click="$emit('review', ver.id, '审核驳回')"
              >驳回</button>
              <button
                v-if="!ver.is_effective"
                type="button"
                class="link danger"
                @click="$emit('delete-image', ver.id)"
              >删除</button>
              <span v-if="ver.is_effective" class="muted">替换后才能删</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="pager">
      <span class="muted">
        本页 {{ items.length }} 个同源系列
      </span>
      <button class="btn" type="button" :disabled="loading" @click="$emit('next-page')">
        {{ hasMore ? '沿游标翻下一页' : '已到末页' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive } from 'vue'

import type { QueueSeries } from '../types'

const props = defineProps<{
  items: QueueSeries[]
  hasMore: boolean
  loading: boolean
  selected: string | null
}>()

defineEmits<{
  (e: 'next-page'): void
  (e: 'select-series', seriesKey: string): void
  (e: 'open-parcels', parcelIds: number[]): void
  (e: 'review', imageId: number, result: '审核通过' | '审核驳回'): void
  (e: 'delete-image', imageId: number): void
}>()

const expanded = reactive<Record<string, boolean>>({})
const expandedItems = computed(() => props.items.filter((item) => expanded[item.series_key]))

function toggle(key: string) {
  expanded[key] = !expanded[key]
}
</script>

<style scoped>
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 8px;
}
.hint, .muted { color: var(--muted); font-size: 12px; }
.warn { color: #b45309; font-size: 12px; }
tr.selected { background: #eff6ff; }
tr { cursor: pointer; }
.tag {
  font-style: normal;
  font-size: 11px;
  border-radius: 4px;
  padding: 1px 6px;
  margin-right: 4px;
}
.tag-multi { background: #ede9fe; color: #6d28d9; }
.tag-gap { background: #fee2e2; color: #b91c1c; }
.tag-ok { background: #dcfce7; color: #15803d; margin-left: 4px; }
.history-box {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 10px;
  margin: 8px 0;
  background: #fbfdff;
}
.history-box h4 { margin: 0 0 6px; font-size: 13px; }
.data-table.inner { font-size: 12px; }
.effective { background: #f0fdf4; }
.mono { font-family: ui-monospace, monospace; font-size: 12px; }
.pager {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}
.link.danger { color: #b42318; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
