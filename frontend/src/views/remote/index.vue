<template>
  <section class="page" data-module="remote-queue">
    <header class="page-head">
      <div>
        <h2>影像核查队列</h2>
        <p class="page-desc">
          时间轴与地图联动定位，标出同源多版本、影像分辨率缺口与尚未派发的解译图斑；
          结论落到遥感清单、图斑待办与存档目录汇总。
        </p>
      </div>
      <div class="tab-switch">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          type="button"
          :class="['btn', { primary: activeTab === tab.key }]"
          @click="switchTab(tab.key)"
        >
          {{ tab.label }}
        </button>
      </div>
    </header>

    <LocatorBar
      :conditions="draftConditions"
      :locator-id="locator.state.locatorId"
      @update="patchDraft"
      @apply="applyLocator"
      @reset="resetLocator"
      @pin="pinLocator"
    />

    <div v-if="message" class="notice" :class="messageKind">{{ message }}</div>

    <!-- 核查：时间轴 + 地图 + 清单 -->
    <template v-if="activeTab === 'queue'">
      <div class="link-grid">
        <TimelinePanel
          :months="timelineMonths"
          :conditions="appliedConditions"
          :selected="selectedSeries"
          @select-series="focusSeries"
          @toggle-month="toggleMonth"
        />
        <MapPanel
          v-if="mapData"
          :data="mapData"
          :conditions="appliedConditions"
          :selected="selectedSeries"
          @select-cell="selectCell"
          @select-series="focusSeries"
          @clear-cell="clearCell"
        />
      </div>
      <QueueList
        :items="queueItems"
        :has-more="queueHasMore"
        :loading="loading"
        :selected="selectedSeries"
        @next-page="loadNextQueuePage"
        @select-series="focusSeries"
        @open-parcels="openParcelsFor"
        @review="reviewImage"
        @delete-image="deleteImage"
      />
    </template>

    <!-- 图斑待办 -->
    <template v-else-if="activeTab === 'parcels'">
      <ParcelTodo
        :parcels="parcelItems"
        :has-more="parcelHasMore"
        :sending="dispatching"
        :last-result="lastDispatch"
        @dispatch="runDispatch"
        @next-page="loadNextParcelPage"
        @locate-series="focusSeries"
      />
    </template>

    <!-- 汇总 -->
    <template v-else-if="activeTab === 'summary' && summaryData">
      <SummaryPanel
        :summary="summaryData"
        @apply-flag="applySummaryFlag"
        @open-parcels-status="openParcelsByStatus"
        @locate-series="focusSeries"
      />
    </template>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { queueApi } from './api'
import LocatorBar from './components/LocatorBar.vue'
import MapPanel from './components/MapPanel.vue'
import ParcelTodo from './components/ParcelTodo.vue'
import QueueList from './components/QueueList.vue'
import SummaryPanel from './components/SummaryPanel.vue'
import TimelinePanel from './components/TimelinePanel.vue'
import type {
  DispatchResult,
  LocatorConditions,
  MapData,
  Parcel,
  QueueFlag,
  QueueSeries,
  Summary,
  TimelineMonth,
} from './types'
import { useLocator } from './useLocator'

type TabKey = 'queue' | 'parcels' | 'summary'
const tabs: { key: TabKey; label: string }[] = [
  { key: 'queue', label: '核查队列' },
  { key: 'parcels', label: '图斑待办' },
  { key: 'summary', label: '汇总页' },
]
const activeTab = ref<TabKey>('queue')

const locator = useLocator()
const draftConditions = reactive<LocatorConditions>({ ...locator.state.conditions })
const appliedConditions = ref<LocatorConditions>({ ...locator.state.conditions })

const loading = ref(false)
const message = ref('')
const messageKind = ref<'ok' | 'err'>('ok')
const selectedSeries = ref<string | null>(null)

const queueItems = ref<QueueSeries[]>([])
const queueHasMore = ref(false)
const timelineMonths = ref<TimelineMonth[]>([])
const mapData = ref<MapData | null>(null)
const parcelItems = ref<Parcel[]>([])
const parcelHasMore = ref(false)
const summaryData = ref<Summary | null>(null)
const dispatching = ref(false)
const lastDispatch = ref<DispatchResult | null>(null)

function notify(text: string, kind: 'ok' | 'err' = 'ok') {
  message.value = text
  messageKind.value = kind
}

function patchDraft(patch: Partial<LocatorConditions>) {
  Object.assign(draftConditions, patch)
}

async function applyLocator() {
  const clean = { ...draftConditions }
  locator.setConditions(clean)
  appliedConditions.value = { ...clean }
  selectedSeries.value = null
  await refreshViews()
}

async function resetLocator() {
  locator.reset()
  Object.keys(draftConditions).forEach((key) => delete draftConditions[key as keyof LocatorConditions])
  appliedConditions.value = {}
  selectedSeries.value = null
  await refreshViews()
}

async function pinLocator() {
  try {
    const { locator_id } = await queueApi.saveLocator(appliedConditions.value)
    locator.state.locatorId = locator_id
    notify(`定位条件已固化为定位号 ${locator_id}，清单 / 图斑 / 汇总凭此互相关联`)
  } catch (error) {
    notify(error instanceof Error ? error.message : '定位号固化失败', 'err')
  }
}

async function loadQueue(reset = false) {
  loading.value = true
  try {
    if (reset) locator.state.queueCursor = null
    const page = await queueApi.listQueue(
      appliedConditions.value,
      reset ? null : locator.state.queueCursor,
    )
    // 游标返回的条件快照与本地一致（服务端权威），翻页后条件不会被新参数覆盖。
    appliedConditions.value = { ...page.conditions }
    queueItems.value = reset ? page.items : [...queueItems.value, ...page.items]
    queueHasMore.value = page.has_more
    locator.state.queueCursor = page.next_cursor
  } catch (error) {
    notify(error instanceof Error ? error.message : '核查队列读取失败', 'err')
  } finally {
    loading.value = false
  }
}

async function loadNextQueuePage() {
  if (queueHasMore.value) await loadQueue(false)
}

async function loadTimeline() {
  try {
    const data = await queueApi.timeline(appliedConditions.value)
    timelineMonths.value = data.months
  } catch (error) {
    notify(error instanceof Error ? error.message : '时间轴读取失败', 'err')
  }
}

async function loadMap() {
  try {
    mapData.value = await queueApi.map(appliedConditions.value)
  } catch (error) {
    notify(error instanceof Error ? error.message : '地图读取失败', 'err')
  }
}

async function loadParcels(reset = false, status?: string) {
  if (reset) locator.state.parcelCursorId = null
  const page = await queueApi.listParcels({
    status,
    locatorId: locator.state.locatorId,
    cursorId: reset ? null : locator.state.parcelCursorId,
  })
  parcelItems.value = reset ? page.items : [...parcelItems.value, ...page.items]
  parcelHasMore.value = page.has_more
  locator.state.parcelCursorId = page.next_cursor_id
}

async function loadNextParcelPage() {
  if (parcelHasMore.value) await loadParcels(false)
}

async function loadSummary() {
  summaryData.value = await queueApi.summary(locator.state.locatorId)
}

async function refreshViews() {
  await Promise.all([loadQueue(true), loadTimeline(), loadMap()])
  if (activeTab.value === 'parcels') await loadParcels(true)
  if (activeTab.value === 'summary') await loadSummary()
}

// ---- 时间轴/地图联动 ----
function focusSeries(seriesKey: string) {
  selectedSeries.value = selectedSeries.value === seriesKey ? null : seriesKey
  draftConditions.keyword = selectedSeries.value ?? ''
  void applyLocator()
}

function toggleMonth(dateFrom: string) {
  draftConditions.date_from = appliedConditions.value.date_from === dateFrom ? '' : dateFrom
  void applyLocator()
}

function selectCell(cell: string) {
  draftConditions.cell = cell || ''
  void applyLocator()
}

function clearCell() {
  if (draftConditions.cell) {
    draftConditions.cell = ''
    void applyLocator()
  }
}

// ---- 版本动作 ----
async function reviewImage(imageId: number, result: '审核通过' | '审核驳回') {
  const note = result === '审核驳回' ? window.prompt('驳回原因（可选）') ?? '' : ''
  try {
    const res = await queueApi.review(imageId, result, note)
    notify(res.message)
    await refreshViews()
  } catch (error) {
    notify(error instanceof Error ? error.message : '审核操作失败', 'err')
  }
}

async function deleteImage(imageId: number) {
  if (!window.confirm('删除该影像？仅追溯旧版可删除，既有浏览游标仍可继续翻页。')) return
  try {
    const res = await queueApi.deleteImage(imageId)
    notify(res.message)
    await refreshViews()
  } catch (error) {
    notify(error instanceof Error ? error.message : '删除失败', 'err')
  }
}

// ---- 图斑派发 ----
async function runDispatch(payload: {
  idempotencyKey: string
  ids: number[]
  assignee: string
  conclusion: string
}) {
  dispatching.value = true
  message.value = ''
  try {
    // 失败时抛错，不改本地定位条件与勾选，可用同一幂等键重试。
    const result = await queueApi.dispatch({
      idempotency_key: payload.idempotencyKey,
      parcel_ids: payload.ids,
      assignee: payload.assignee,
      conclusion: payload.conclusion || undefined,
    })
    lastDispatch.value = result
    notify(result.message, result.conflicts.length ? 'err' : 'ok')
    await Promise.all([loadParcels(true), loadMap(), loadQueue(true), loadTimeline()])
  } catch (error) {
    // 未成功：维持此前定位上下文，保留勾选与幂等键。
    lastDispatch.value = null
    notify(
      `派发未成功，定位条件与勾选已保留，可用同一幂等键重试：${
        error instanceof Error ? error.message : '请求失败'
      }`,
      'err',
    )
  } finally {
    dispatching.value = false
  }
}

function openParcelsFor(ids: number[]) {
  activeTab.value = 'parcels'
  void loadParcels(true).then(() => {
    parcelItems.value = parcelItems.value.filter((p) => ids.includes(p.id))
  })
}

function openParcelsByStatus(status: string) {
  activeTab.value = 'parcels'
  void loadParcels(true, status)
}

function applySummaryFlag(flag: string) {
  if (flag === 'all') {
    delete draftConditions.flag
  } else {
    draftConditions.flag = flag as QueueFlag
  }
  activeTab.value = 'queue'
  void applyLocator()
}

async function switchTab(key: TabKey) {
  activeTab.value = key
  selectedSeries.value = null
  if (key === 'parcels' && !parcelItems.value.length) await loadParcels(true)
  if (key === 'summary') await loadSummary()
}

onMounted(refreshViews)
</script>

<style scoped>
.tab-switch { display: flex; gap: 6px; }
.link-grid {
  display: grid;
  grid-template-columns: minmax(300px, 1fr) minmax(320px, 1.2fr);
  gap: 12px;
  margin-bottom: 12px;
}
.notice {
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 13px;
  margin-bottom: 10px;
  border: 1px solid #bbf7d0;
  background: #f0fdf4;
}
.notice.err { border-color: #fcd34d; background: #fffbeb; color: #92400e; }
@media (max-width: 1100px) {
  .link-grid { grid-template-columns: 1fr; }
}
</style>
