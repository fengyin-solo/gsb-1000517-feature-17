<template>
  <form class="locator-bar" @submit.prevent="$emit('apply')">
    <label class="loc-item">
      <span>数据编号/同源系列</span>
      <input
        :value="conditions.keyword ?? ''"
        placeholder="如 IMG-A01"
        @input="$emit('update', { keyword: ($event.target as HTMLInputElement).value })"
      />
    </label>
    <label class="loc-item">
      <span>数据源</span>
      <input
        :value="conditions.source ?? ''"
        placeholder="如 高分"
        @input="$emit('update', { source: ($event.target as HTMLInputElement).value })"
      />
    </label>
    <label class="loc-item">
      <span>获取日期起</span>
      <input
        type="date"
        :value="conditions.date_from ?? ''"
        @input="$emit('update', { date_from: ($event.target as HTMLInputElement).value })"
      />
    </label>
    <label class="loc-item">
      <span>获取日期止</span>
      <input
        type="date"
        :value="conditions.date_to ?? ''"
        @input="$emit('update', { date_to: ($event.target as HTMLInputElement).value })"
      />
    </label>
    <div class="loc-item flags">
      <span>核查旗标</span>
      <div class="flag-btns">
        <button
          v-for="option in flagOptions"
          :key="option.value"
          type="button"
          :class="['btn', { primary: conditions.flag === option.value }]"
          @click="$emit('update', { flag: conditions.flag === option.value ? undefined : option.value })"
        >
          {{ option.label }}
        </button>
      </div>
    </div>
    <div class="loc-actions">
      <button class="btn primary" type="submit">定位</button>
      <button class="btn ghost" type="button" @click="$emit('reset')">重置</button>
      <button class="btn" type="button" @click="$emit('pin')">固化定位号</button>
    </div>
    <span v-if="locatorId" class="locator-id">定位号 {{ locatorId }}（清单/图斑/汇总共享）</span>
  </form>
</template>

<script setup lang="ts">
import type { LocatorConditions, QueueFlag } from '../types'

defineProps<{
  conditions: LocatorConditions
  locatorId: string | null
}>()

defineEmits<{
  (e: 'update', patch: Partial<LocatorConditions>): void
  (e: 'apply'): void
  (e: 'reset'): void
  (e: 'pin'): void
}>()

const flagOptions: { value: QueueFlag; label: string }[] = [
  { value: 'multi_version', label: '同源多版本' },
  { value: 'resolution_gap', label: '分辨率缺口' },
  { value: 'undispatched', label: '未派发图斑' },
]
</script>

<style scoped>
.locator-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.loc-item span {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 2px;
}
.loc-item input {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
}
.flag-btns { display: flex; gap: 6px; }
.loc-actions { display: flex; gap: 6px; }
.locator-id {
  width: 100%;
  font-size: 12px;
  color: var(--brand);
}
</style>
