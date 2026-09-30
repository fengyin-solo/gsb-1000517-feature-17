<template>
  <div class="parcel-todo">
    <div class="panel-head">
      <strong>解译图斑待办</strong>
      <span class="hint">勾选后集中派发；同一幂等键重试不重复派发，冲突按最新结论保留</span>
    </div>

    <div class="dispatch-bar">
      <label>
        派发对象
        <input v-model="assignee" placeholder="解译人员" />
      </label>
      <label>
        解译结论（可选）
        <input v-model="conclusion" placeholder="本次提交的解译结论" />
      </label>
      <label class="idem">
        幂等键
        <input v-model="idempotencyKey" readonly />
        <button type="button" class="btn ghost" @click="regenKey">换新键</button>
      </label>
      <button
        class="btn primary"
        type="button"
        :disabled="!checked.size || !assignee || sending"
        @click="dispatch"
      >
        {{ sending ? '派发中…' : `集中派发（${checked.size}）` }}
      </button>
      <button class="btn ghost" type="button" @click="checked.clear()">清空勾选</button>
    </div>

    <div v-if="lastResult" class="result-box" :class="{ warn: lastResult.conflicts.length }">
      <span>{{ lastResult.replayed ? '幂等重放：' : '' }}{{ lastResult.message }}</span>
      <ul v-if="lastResult.conflicts.length">
        <li v-for="cf in lastResult.conflicts" :key="cf.parcel_id">
          图斑 {{ cf.parcel_id }} 冲突：保留更新的结论「{{ cf.kept_conclusion }}」（{{ cf.kept_at }}），
          忽略「{{ cf.ignored_conclusion }}」
        </li>
      </ul>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th style="width: 36px"><input type="checkbox" :checked="allChecked" @change="toggleAll" /></th>
          <th>图斑编号</th>
          <th>所属影像</th>
          <th>解译要素</th>
          <th>状态</th>
          <th>派发对象</th>
          <th>最近解译结论</th>
          <th>派发批次</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="parcel in parcels" :key="parcel.id">
          <td>
            <input
              type="checkbox"
              :checked="checked.has(parcel.id)"
              :disabled="parcel.status === '已完成'"
              @change="toggle(parcel.id)"
            />
          </td>
          <td>{{ parcel.code }}</td>
          <td>
            <button type="button" class="link" @click="$emit('locate-series', parcel.series_key)">
              {{ parcel.series_key }}
            </button>
          </td>
          <td>{{ parcel.feature }}</td>
          <td><i :class="['status', `st-${parcel.status}`]">{{ parcel.status }}</i></td>
          <td>{{ parcel.assignee ?? '—' }}</td>
          <td>
            {{ parcel.last_conclusion ?? '—' }}
            <span v-if="parcel.concluded_at" class="muted">（{{ parcel.concluded_at }}）</span>
          </td>
          <td class="mono">{{ parcel.dispatch_batch ?? '—' }}</td>
        </tr>
        <tr v-if="!parcels.length">
          <td colspan="8" class="empty-state">当前定位条件下没有图斑</td>
        </tr>
      </tbody>
    </table>
    <div class="pager">
      <span class="muted">共 {{ parcels.length }} 个图斑</span>
      <button class="btn" type="button" :disabled="!hasMore" @click="$emit('next-page')">沿游标翻下一页</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { DispatchResult, Parcel } from '../types'

const props = defineProps<{
  parcels: Parcel[]
  hasMore: boolean
  sending: boolean
  lastResult: DispatchResult | null
}>()

const emit = defineEmits<{
  (e: 'dispatch', payload: { idempotencyKey: string; ids: number[]; assignee: string; conclusion: string }): void
  (e: 'next-page'): void
  (e: 'locate-series', seriesKey: string): void
}>()

const checked = ref(new Set<number>())
const assignee = ref('')
const conclusion = ref('')
const sending = ref(false)
const idempotencyKey = ref(newKey())
const lastResult = ref<DispatchResult | null>(null)

function newKey() {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID().slice(0, 8)
    : Math.random().toString(36).slice(2, 10)
  return `dispatch-${Date.now().toString(36)}-${random}`
}

function regenKey() {
  idempotencyKey.value = newKey()
}

const allChecked = computed(
  () => props.parcels.length > 0 && props.parcels.every((p) => p.status !== '已完成' && checked.value.has(p.id)),
)

function toggle(id: number) {
  const next = new Set(checked.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  checked.value = next
}

function toggleAll(event: Event) {
  const on = (event.target as HTMLInputElement).checked
  checked.value = new Set(on ? props.parcels.filter((p) => p.status !== '已完成').map((p) => p.id) : [])
}

// 外部派发结果回显；派发成功后清空勾选（幂等键保留，便于安全重试）。
watch(
  () => props.lastResult,
  (value) => {
    lastResult.value = value
    sending.value = props.sending
    if (value && value.ok && !value.replayed && value.dispatched_parcel_ids.length) {
      const next = new Set(checked.value)
      for (const id of value.dispatched_parcel_ids) next.delete(id)
      checked.value = next
    }
  },
)
watch(
  () => props.sending,
  (value) => {
    sending.value = value
  },
)

async function dispatch() {
  emit('dispatch', {
    idempotencyKey: idempotencyKey.value,
    ids: [...checked.value],
    assignee: assignee.value.trim(),
    conclusion: conclusion.value.trim(),
  })
}
</script>

<style scoped>
.panel-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.hint, .muted { color: var(--muted); font-size: 12px; }
.dispatch-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 10px;
}
.dispatch-bar label { font-size: 12px; color: var(--muted); }
.dispatch-bar input {
  display: block;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
  margin-top: 2px;
}
.idem input { width: 240px; font-family: ui-monospace, monospace; }
.idem { display: flex; align-items: flex-end; gap: 6px; }
.result-box {
  border: 1px solid #bbf7d0;
  background: #f0fdf4;
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 13px;
  margin-bottom: 10px;
}
.result-box.warn { border-color: #fcd34d; background: #fffbeb; }
.result-box ul { margin: 4px 0 0 18px; padding: 0; }
.status { font-style: normal; font-size: 12px; border-radius: 4px; padding: 1px 8px; }
.st-待派发 { background: #fef3c7; color: #b45309; }
.st-已派发 { background: #e0f2fe; color: #0369a1; }
.st-已完成 { background: #dcfce7; color: #15803d; }
.mono { font-family: ui-monospace, monospace; font-size: 12px; }
.pager { display: flex; justify-content: space-between; margin-top: 8px; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
