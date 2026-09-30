<template>
  <section class="page queue-page" data-module="remote">
    <header class="page-head">
      <div>
        <h2>影像核查队列</h2>
        <p class="page-desc">
          时间轴与地图联动定位，标出同源多版本、影像分辨率缺口与尚未派发的解译图斑；
          定位结论统一落到遥感清单、图斑待办与存档目录。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出当前定位清单</button>
        <button class="btn primary" type="button" @click="resetLocate">重新开始定位</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">待派发图斑</span>
        <strong class="stat-value">{{ summary?.counts.unassigned ?? '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">分辨率缺口同源影像</span>
        <strong class="stat-value warn">{{ summary?.counts.resolution_gap_sources ?? '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待质检新版本</span>
        <strong class="stat-value review">{{ reviewCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">存档卷宗</span>
        <strong class="stat-value">{{ summary?.counts.archive_volumes ?? '—' }}</strong>
      </article>
    </div>

    <div v-if="trail.length" class="locate-bar">
      <span class="locate-label">定位链路：</span>
      <span v-for="(step, idx) in trail" :key="idx" class="locate-chip">
        {{ step }}<em v-if="idx < trail.length - 1"> ⇒</em>
      </span>
      <span class="locate-matched">命中影像 {{ lastMatch.images }} · 图斑 {{ lastMatch.polygons }}</span>
    </div>

    <form class="filter-bar" @submit.prevent="submitLocate">
      <label class="filter-item">
        <span>数据编号/数据源</span>
        <input v-model="locateForm.keyword" placeholder="按关键字定位" />
      </label>
      <label class="filter-item">
        <span>同源键</span>
        <input v-model="locateForm.source_key" placeholder="如 RS-NW-A" />
      </label>
      <label class="filter-item">
        <span>质量</span>
        <select v-model="locateForm.quality">
          <option value="">不限</option>
          <option>审核通过</option>
          <option>待审核</option>
          <option>审核驳回</option>
        </select>
      </label>
      <label class="filter-item">
        <span>获取日期起</span>
        <input v-model="locateForm.date_from" type="date" />
      </label>
      <label class="filter-item">
        <span>获取日期止</span>
        <input v-model="locateForm.date_to" type="date" />
      </label>
      <label class="filter-item">
        <span>解译尺度(m)</span>
        <input v-model.number="locateForm.scale" type="number" min="0.1" step="0.1" style="width: 90px" />
      </label>
      <label class="filter-check">
        <input v-model="locateForm.only_gap" type="checkbox" /> 仅看分辨率缺口
      </label>
      <label class="filter-check">
        <input v-model="includeTrace" type="checkbox" @change="reloadAll" /> 含仅追溯旧版
      </label>
      <button class="btn primary" type="submit">在当前定位内收窄</button>
    </form>

    <div class="link-grid">
      <!-- 时间轴 -->
      <section class="panel timeline-panel">
        <h3>获取时间轴</h3>
        <p class="panel-hint">点击影像可在地图定位；实线为当前有效版本，虚线为仅追溯旧版。</p>
        <div class="timeline">
          <div class="timeline-axis" />
          <button
            v-for="img in timelineImages"
            :key="img.id"
            type="button"
            class="tl-item"
            :class="{ current: img.is_current, trace: img.trace_only, review: img.status === '待质检', gap: img.resolution_gap, active: selectedImageId === img.id }"
            :style="{ left: timelinePosition(img) + '%' }"
            :title="img.gap_reasons.join('；') || img.review_note"
            @click="locateImage(img)"
          >
            <span class="tl-dot" />
            <span class="tl-label">{{ img.数据编号 }}</span>
            <span class="tl-meta">{{ img.获取日期 }} · {{ img.分辨率 }}</span>
          </button>
        </div>
      </section>

      <!-- 地图 -->
      <section class="panel map-panel">
        <h3>影像覆盖地图
          <small v-if="dragHint" class="drag-hint">{{ dragHint }}</small>
        </h3>
        <svg
          ref="mapSvg"
          class="map-canvas"
          viewBox="0 0 480 450"
          @mousedown="startDrag"
          @mousemove="moveDrag"
          @mouseup="endDrag"
          @mouseleave="cancelDrag"
        >
          <rect width="480" height="450" fill="#f1f5f9" />
          <g v-for="img in workspace?.images ?? []" :key="'img' + img.id">
            <polygon
              :points="toPoints(img.footprint)"
              :class="['map-image', { current: img.is_current, trace: img.trace_only, review: img.status === '待质检', gap: img.resolution_gap, active: selectedImageId === img.id }]"
              @mousedown.stop="locateImage(img)"
            />
            <text
              :x="centroid(img.footprint).x"
              :y="centroid(img.footprint).y - 4"
              class="map-label"
              @mousedown.stop="locateImage(img)"
            >{{ img.数据编号 }}{{ img.multi_version ? ` ×${img.version_count}` : '' }}</text>
          </g>
          <g v-for="poly in workspace?.polygons ?? []" :key="'poly' + poly.id">
            <polygon
              :points="toPoints(poly.footprint)"
              :class="['map-polygon', { pending: poly.pending_dispatch, gap: poly.resolution_gap, active: selectedPolygonId === poly.id }]"
              @mousedown.stop="locatePolygon(poly)"
            />
            <text
              :x="centroid(poly.footprint).x"
              :y="centroid(poly.footprint).y + 3"
              class="map-poly-label"
              @mousedown.stop="locatePolygon(poly)"
            >{{ poly.polygon_no }}</text>
          </g>
          <rect
            v-if="dragRect"
            :x="dragRect.x" :y="dragRect.y" :width="dragRect.w" :height="dragRect.h"
            class="map-select"
          />
        </svg>
        <div class="legend">
          <span><i class="lg current" />当前有效</span>
          <span><i class="lg review" />待质检</span>
          <span><i class="lg trace" />仅追溯</span>
          <span><i class="lg gap" />分辨率缺口</span>
          <span><i class="lg pending" />待派图斑</span>
        </div>
      </section>

      <!-- 图斑待办 + 派发 -->
      <section class="panel todo-panel">
        <h3>图斑待办 · 集中派发</h3>
        <div class="dispatch-bar">
          <input v-model="dispatchForm.assignee" placeholder="承办组，如 解译一组" />
          <input v-model="dispatchForm.idempotency_key" placeholder="幂等键（自动生成，可重放）" />
          <button class="btn primary" type="button" :disabled="!dispatchCandidates.length" @click="runDispatch">
            派发选中 {{ dispatchCandidates.length }} 个
          </button>
          <button class="btn ghost" type="button" @click="newIdempotencyKey">换新幂等键</button>
        </div>
        <ul class="todo-list">
          <li v-for="poly in todoPolygons" :key="poly.id" class="todo-item" :class="{ active: selectedPolygonId === poly.id }">
            <label class="todo-check">
              <input
                v-model="dispatchForm.polygon_ids"
                type="checkbox"
                :value="poly.id"
                :disabled="poly.assigned"
              />
            </label>
            <div class="todo-body" @click="locatePolygon(poly)">
              <div class="todo-head">
                <strong>{{ poly.polygon_no }} {{ poly.name }}</strong>
                <span class="badge" :class="poly.assigned ? 'ok' : 'alert'">{{ poly.status }}</span>
                <span v-if="poly.resolution_gap" class="badge warn">分辨率缺口</span>
                <span v-if="poly.image_trace" class="badge trace">所属为旧版影像</span>
              </div>
              <div class="todo-meta">
                底图 {{ poly.image_no }} · 要求 {{ poly.required_resolution_m }}m
                · 结论 v{{ poly.conclusion_seq }}<template v-if="poly.assignee"> · {{ poly.assignee }}</template>
              </div>
              <div v-if="poly.conclusion" class="todo-conclusion">「{{ poly.conclusion }}」</div>
              <div class="conclusion-row">
                <input v-model="conclusionDrafts[poly.id]" placeholder="登记最新解译结论（冲突时以此为准）" />
                <button class="link" type="button" @click.stop="saveConclusion(poly)">登记结论 v{{ poly.conclusion_seq + 1 }}</button>
              </div>
            </div>
          </li>
          <li v-if="!todoPolygons.length" class="empty-state">当前定位范围内没有待办图斑</li>
        </ul>
      </section>
    </div>

    <!-- 影像核查清单（游标分页） -->
    <section class="panel list-panel">
      <h3>影像核查清单
        <small class="panel-hint">
          索引快照 v{{ listPage?.index_version ?? '—' }}
          <template v-if="listPage?.stale">（游标快照已归档，已平滑回退最新快照）</template>
          · 共 {{ listPage?.total ?? 0 }} 条，已载 {{ listItems.length }} 条
        </small>
      </h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>数据编号</th><th>同源/版本</th><th>数据源</th><th>分辨率</th><th>获取日期</th>
            <th>质量</th><th>状态</th><th>核查标记</th><th>图斑</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in listItems" :key="row.id" :class="{ trace: row.trace_only, active: selectedImageId === row.id }">
            <td>
              <a class="link" @click="locateImage(row)">{{ row.数据编号 }}</a>
              <div class="cell-sub">{{ row.archive_code }}</div>
            </td>
            <td>{{ row.source_key }}<div class="cell-sub">V{{ row.version }}<template v-if="row.multi_version"> · 共 {{ row.version_count }} 版</template></div></td>
            <td>{{ row.数据源 }}</td>
            <td>
              {{ row.分辨率 }}
              <span v-if="row.resolution_gap" class="badge warn">缺口</span>
            </td>
            <td>{{ row.获取日期 }}</td>
            <td>{{ row.quality }}</td>
            <td><span class="badge" :class="statusClass(row)">{{ row.status }}</span></td>
            <td>
              <span v-for="reason in row.gap_reasons" :key="reason" class="flag warn">{{ reason }}</span>
              <span v-if="!row.gap_reasons.length" class="cell-sub">—</span>
            </td>
            <td>
              <span v-if="row.polygon_total">共 {{ row.polygon_total }} · 未派 {{ row.polygon_pending }}</span>
              <span v-else class="cell-sub">无图斑</span>
            </td>
            <td class="row-actions">
              <button v-if="row.quality === '待审核'" class="link" type="button" @click="review(row, '审核通过')">通过</button>
              <button v-if="row.quality === '待审核'" class="link danger" type="button" @click="review(row, '审核驳回')">驳回</button>
              <button class="link" type="button" @click="replaceImage(row)">替换新版</button>
              <button v-if="!row.tombstoned" class="link danger" type="button" @click="removeImage(row)">下线</button>
            </td>
          </tr>
        </tbody>
      </table>
      <footer class="pager">
        <button class="btn" type="button" :disabled="loading" @click="reloadList">回到首页</button>
        <button class="btn primary" type="button" :disabled="!listPage?.next_cursor || loading" @click="loadNextPage">
          沿浏览游标加载下一页
        </button>
        <span class="cell-sub">删除/替换后旧游标仍按原快照翻页，行内容实时刷新，不重不漏</span>
      </footer>
    </section>

    <!-- 定位结论汇总 -->
    <section class="panel summary-panel">
      <h3>定位结论汇总 <small class="panel-hint">遥感清单 · 图斑待办 · 存档目录共用同一套定位条件</small></h3>

      <h4 class="sum-title">① 遥感清单（按同源版本谱系）</h4>
      <table class="data-table compact">
        <thead><tr><th>同源键</th><th>数据源</th><th>当前有效版本</th><th>版本数</th><th>缺口结论</th><th>未派图斑</th></tr></thead>
        <tbody>
          <tr v-for="g in summary?.inventory ?? []" :key="g.source_key">
            <td>{{ g.source_key }}</td>
            <td>{{ g.数据源 }}</td>
            <td>
              <template v-if="g.current">{{ g.current.数据编号 }}（{{ g.current.分辨率 }}）</template>
              <span v-else class="badge warn">无审核通过版本</span>
            </td>
            <td>{{ g.version_count }}</td>
            <td>
              <span v-if="g.resolution_gap" class="flag warn">{{ g.gap_reasons.join('；') }}</span>
              <span v-else class="cell-sub">满足 {{ g.scale }}m 解译尺度</span>
            </td>
            <td>{{ g.pending_polygons }}</td>
          </tr>
          <tr v-if="!(summary?.inventory.length)"><td colspan="6" class="empty-state">当前定位条件下无命中影像</td></tr>
        </tbody>
      </table>

      <h4 class="sum-title">② 图斑待办（未派发 / 待复核）</h4>
      <table class="data-table compact">
        <thead><tr><th>图斑</th><th>名称</th><th>底图</th><th>要求分辨率</th><th>最新结论</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="p in summary?.polygon_todo ?? []" :key="p.id">
            <td>{{ p.polygon_no }}</td>
            <td>{{ p.name }}</td>
            <td>{{ p.image_no }}<span v-if="p.image_trace" class="badge trace">旧版</span></td>
            <td>{{ p.required_resolution_m }}m<span v-if="p.resolution_gap" class="badge warn">缺口</span></td>
            <td>v{{ p.conclusion_seq }} {{ p.conclusion || '（尚未解译）' }}</td>
            <td><span class="badge" :class="p.assigned ? 'ok' : 'alert'">{{ p.status }}</span></td>
          </tr>
          <tr v-if="!(summary?.polygon_todo.length)"><td colspan="6" class="empty-state">无待办图斑</td></tr>
        </tbody>
      </table>

      <h4 class="sum-title">③ 存档目录汇总（旧版仅追溯、下线版保留墓碑）</h4>
      <div class="archive-grid">
        <article v-for="vol in summary?.archive ?? []" :key="vol.archive_code" class="archive-card">
          <header>
            <strong>{{ vol.archive_code }}</strong>
            <span class="cell-sub">{{ vol.sources.join('、') }}</span>
          </header>
          <div class="archive-current">当前有效：<b>{{ vol.current_no || '—' }}</b></div>
          <div class="archive-counts">追溯 {{ vol.trace_count }} · 下线 {{ vol.deleted_count }} · 共 {{ vol.entries.length }} 版</div>
          <ul class="archive-list">
            <li v-for="e in vol.entries" :key="e.id" :class="{ tomb: e.tombstoned }">
              <span class="badge" :class="e.is_current ? 'ok' : e.tombstoned ? 'trace' : e.trace_only ? 'trace' : 'review'">{{ e.status }}</span>
              {{ e.数据编号 }} · {{ e.分辨率 }} · {{ e.获取日期 }} · {{ e.quality }}
              <em class="cell-sub">{{ e.review_note }}</em>
            </li>
          </ul>
        </article>
      </div>
    </section>

    <footer class="page-foot">
      <span v-if="message" :class="messageOk ? 'ok-text' : 'error-text'">{{ message }}</span>
      <span v-else>集中派发采用幂等键；冲突时以最新一次解译结论为准，失败时维持此前定位上下文。</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import type {
  DispatchResult, ImageItem, LocateResult, PageResult, PolygonItem, Summary, Workspace,
} from '@/views/remote/types'

const ENDPOINT = '/api/remote'

const workspace = ref<Workspace | null>(null)
const summary = ref<Summary | null>(null)
const listPage = ref<PageResult | null>(null)
const listItems = ref<ImageItem[]>([])

const contextToken = ref<string | null>(null)
const trail = ref<string[]>([])
const lastMatch = reactive({ images: 0, polygons: 0 })
const includeTrace = ref(false)
const loading = ref(false)
const message = ref('')
const messageOk = ref(true)

const selectedImageId = ref<number | null>(null)
const selectedPolygonId = ref<number | null>(null)

const locateForm = reactive({
  keyword: '', source_key: '', quality: '', date_from: '', date_to: '',
  scale: 2, only_gap: false,
})
const dispatchForm = reactive<{ assignee: string; idempotency_key: string; polygon_ids: number[] }>({
  assignee: '', idempotency_key: '', polygon_ids: [],
})
const conclusionDrafts = reactive<Record<number, string>>({})

// ---------------------------------------------------------------- 数据加载

function commonQuery(extra: Record<string, string | number | boolean> = {}) {
  const params = new URLSearchParams()
  if (contextToken.value) params.set('context', contextToken.value)
  if (includeTrace.value) params.set('include_trace', 'true')
  for (const [k, v] of Object.entries(extra)) {
    if (v !== '' && v !== false) params.set(k, String(v))
  }
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

function notify(text: string, ok = true) {
  message.value = text
  messageOk.value = ok
}

async function getJson<T>(url: string): Promise<T | null> {
  const resp = await request(url)
  if (!resp.ok) {
    notify(`接口返回 ${resp.status}：定位上下文未改变`, false)
    return null
  }
  return (await resp.json()) as T
}

async function reloadAll() {
  const [ws, sum] = await Promise.all([
    getJson<Workspace>(`${ENDPOINT}/workspace${commonQuery()}`),
    getJson<Summary>(`${ENDPOINT}/summary${commonQuery()}`),
  ])
  if (ws) workspace.value = ws
  if (sum) summary.value = sum
  await reloadList()
}

async function reloadList() {
  loading.value = true
  try {
    const page = await getJson<PageResult>(`${ENDPOINT}${commonQuery({ size: 5 })}`)
    if (page) {
      listPage.value = page
      listItems.value = page.items
    }
  } finally {
    loading.value = false
  }
}

async function loadNextPage() {
  if (!listPage.value?.next_cursor) return
  loading.value = true
  try {
    // 翻页只带游标：条件内嵌在游标中，删除/替换后仍按原快照继续，不重不漏
    const page = await getJson<PageResult>(`${ENDPOINT}?cursor=${listPage.value.next_cursor}`)
    if (!page) return
    const known = new Set(listItems.value.map((i) => i.id))
    listItems.value.push(...page.items.filter((i) => !known.has(i.id)))
    listPage.value = page
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------- 定位

async function applyLocate(payload: Record<string, unknown>) {
  const url = contextToken.value ? `${ENDPOINT}/locate/${contextToken.value}` : `${ENDPOINT}/locate`
  const resp = await request(url, { method: 'POST', body: JSON.stringify(payload) })
  if (!resp.ok) {
    notify('定位失败，此前定位条件保持不变', false)
    return
  }
  const result = (await resp.json()) as LocateResult
  contextToken.value = result.context
  trail.value = result.trail
  lastMatch.images = result.matched.images
  lastMatch.polygons = result.matched.polygons
  selectedImageId.value = result.focus.images.length === 1 ? result.focus.images[0] : selectedImageId.value
  await reloadAll()
}

function submitLocate() {
  const payload: Record<string, unknown> = { scale: locateForm.scale }
  for (const [k, v] of Object.entries(locateForm)) {
    if (k === 'scale') continue
    if (v !== '' && v !== false && v !== null) payload[k] = v
  }
  void applyLocate(payload)
}

function locateImage(img: ImageItem) {
  selectedImageId.value = img.id
  selectedPolygonId.value = null
  void applyLocate({ image_id: img.id })
}

function locatePolygon(poly: PolygonItem) {
  selectedPolygonId.value = poly.id
  selectedImageId.value = poly.image_id
  void applyLocate({ polygon_id: poly.id })
}

async function resetLocate() {
  contextToken.value = null
  trail.value = []
  selectedImageId.value = null
  selectedPolygonId.value = null
  Object.assign(locateForm, { keyword: '', source_key: '', quality: '', date_from: '', date_to: '', scale: 2, only_gap: false })
  await reloadAll()
  notify('已清空定位链路，回到全量队列')
}

// ---------------------------------------------------------------- 时间轴

const timelineImages = computed(() =>
  [...(workspace.value?.images ?? [])].sort((a, b) => a.acquired_at - b.acquired_at),
)

function timelinePosition(img: ImageItem) {
  const times = timelineImages.value.map((i) => i.acquired_at)
  const min = Math.min(...times, img.acquired_at)
  const max = Math.max(...times, img.acquired_at)
  if (max === min) return 50
  return 4 + ((img.acquired_at - min) / (max - min)) * 90
}

// ---------------------------------------------------------------- 地图

const MAP_SCALE_X = 480 / 960
const MAP_SCALE_Y = 450 / 900
const mapSvg = ref<SVGSVGElement | null>(null)
const dragRect = ref<{ x: number; y: number; w: number; h: number } | null>(null)
const dragStart = ref<{ x: number; y: number } | null>(null)
const dragHint = ref('')

function toPoints(footprint: number[][]) {
  return footprint.map((p) => `${Math.round(p[0] * MAP_SCALE_X)},${Math.round(p[1] * MAP_SCALE_Y)}`).join(' ')
}
function centroid(footprint: number[][]) {
  const x = footprint.reduce((s, p) => s + p[0], 0) / footprint.length
  const y = footprint.reduce((s, p) => s + p[1], 0) / footprint.length
  return { x: Math.round(x * MAP_SCALE_X), y: Math.round(y * MAP_SCALE_Y) }
}

function svgPoint(evt: MouseEvent) {
  const rectBox = mapSvg.value!.getBoundingClientRect()
  return {
    x: ((evt.clientX - rectBox.left) / rectBox.width) * 480,
    y: ((evt.clientY - rectBox.top) / rectBox.height) * 450,
  }
}

function startDrag(evt: MouseEvent) {
  dragStart.value = svgPoint(evt)
  dragRect.value = { x: dragStart.value.x, y: dragStart.value.y, w: 0, h: 0 }
}
function moveDrag(evt: MouseEvent) {
  if (!dragStart.value) return
  const p = svgPoint(evt)
  dragRect.value = {
    x: Math.min(dragStart.value.x, p.x),
    y: Math.min(dragStart.value.y, p.y),
    w: Math.abs(p.x - dragStart.value.x),
    h: Math.abs(p.y - dragStart.value.y),
  }
}
function endDrag() {
  const rect = dragRect.value
  dragStart.value = null
  dragRect.value = null
  if (!rect || rect.w < 8 || rect.h < 8) return
  // 还原成世界坐标后发起地图框选定位，与时间轴条件取交集
  const bbox = [
    Math.round((rect.x / MAP_SCALE_X) / 10) * 10,
    Math.round((rect.y / MAP_SCALE_Y) / 10) * 10,
    Math.round(((rect.x + rect.w) / MAP_SCALE_X) / 10) * 10,
    Math.round(((rect.y + rect.h) / MAP_SCALE_Y) / 10) * 10,
  ]
  dragHint.value = `框选范围 ${bbox.join(',')}`
  void applyLocate({ bbox })
}
function cancelDrag() {
  dragStart.value = null
  dragRect.value = null
}

// ---------------------------------------------------------------- 图斑派发

const todoPolygons = computed(() => summary.value?.polygon_todo ?? [])

const dispatchCandidates = computed(() =>
  todoPolygons.value.filter((p) => dispatchForm.polygon_ids.includes(p.id) && !p.assigned),
)

function newIdempotencyKey() {
  const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '')
  dispatchForm.idempotency_key = `BATCH-${stamp}-${Math.random().toString(36).slice(2, 6)}`
}

async function runDispatch() {
  const candidates = dispatchCandidates.value
  if (!candidates.length) return
  if (!dispatchForm.assignee.trim()) {
    notify('请先填写承办组；定位上下文未改变', false)
    return
  }
  if (!dispatchForm.idempotency_key.trim()) newIdempotencyKey()
  const payload = {
    idempotency_key: dispatchForm.idempotency_key,
    assignee: dispatchForm.assignee,
    items: candidates.map((p) => ({ polygon_id: p.id, seq: p.conclusion_seq, conclusion: p.conclusion })),
  }
  // 携带定位上下文：失败时服务端原样带回 context，页面条件不丢失
  const url = contextToken.value ? `${ENDPOINT}/dispatch/${contextToken.value}` : `${ENDPOINT}/dispatch`
  const resp = await request(url, { method: 'POST', body: JSON.stringify(payload) })
  const result = (await resp.json()) as DispatchResult
  notify(result.message, result.ok)
  if (result.ok && !result.replayed) dispatchForm.polygon_ids = []
  await reloadAll()
}

async function saveConclusion(poly: PolygonItem) {
  const text = (conclusionDrafts[poly.id] || '').trim()
  if (!text) {
    notify('解译结论不能为空', false)
    return
  }
  const resp = await request(`${ENDPOINT}/polygons/${poly.id}/conclusion`, {
    method: 'POST', body: JSON.stringify({ conclusion: text }),
  })
  const body = await resp.json()
  notify(body.message, body.ok)
  if (body.ok) conclusionDrafts[poly.id] = ''
  await reloadAll()
}

// ---------------------------------------------------------------- 影像动作

async function postAction(url: string, body: unknown, okText = '操作已生效，索引快照已更新') {
  const resp = await request(url, { method: 'POST', body: JSON.stringify(body) })
  const result = await resp.json()
  notify(result.message ?? okText, !!result.ok)
  await reloadAll()
}

function review(row: ImageItem, action: '审核通过' | '审核驳回') {
  void postAction(`${ENDPOINT}/${row.id}/actions`, { action, note: action === '审核通过' ? '队列内审核通过' : '队列内审核驳回' })
}
function replaceImage(row: ImageItem) {
  const resolution = window.prompt(`为 ${row.数据编号} 提交替换新版的分辨率`, row.分辨率)
  if (resolution === null) return
  const date = window.prompt('新版本获取日期（YYYY-MM-DD）', new Date().toISOString().slice(0, 10))
  if (date === null) return
  void postAction(`${ENDPOINT}/${row.id}/replace`, { values: { 分辨率: resolution, 获取日期: date } })
}
async function removeImage(row: ImageItem) {
  const resp = await request(`${ENDPOINT}/${row.id}`, { method: 'DELETE' })
  const result = await resp.json()
  notify(result.message, result.ok)
  await reloadAll()
}

function statusClass(row: ImageItem) {
  if (row.is_current) return 'ok'
  if (row.status === '待质检') return 'review'
  return 'trace'
}

function exportRows() {
  window.open(`${ENDPOINT}/export${commonQuery()}`, '_blank')
}

const reviewCount = computed(() => (workspace.value?.images ?? []).filter((i) => i.status === '待质检').length)

onMounted(async () => {
  newIdempotencyKey()
  await reloadAll()
})
</script>

<style scoped>
.queue-page { display: flex; flex-direction: column; gap: 12px; }
.locate-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; background: #eef4ff; border: 1px solid #c6d9ff; border-radius: 8px; padding: 8px 10px; font-size: 12px; }
.locate-label { font-weight: 600; color: #1f4fb0; }
.locate-chip { color: #334155; }
.locate-chip em { color: #1f6feb; font-style: normal; }
.locate-matched { margin-left: auto; color: #1f6feb; font-weight: 600; }
.filter-item select, .filter-item input { padding: 4px 6px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.filter-check { display: flex; align-items: center; gap: 4px; font-size: 13px; color: #334155; }

.link-grid { display: grid; grid-template-columns: 1fr 1fr; grid-template-areas: 'timeline timeline' 'map todo'; gap: 12px; }
.timeline-panel { grid-area: timeline; }
.map-panel { grid-area: map; }
.todo-panel { grid-area: todo; }

.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.panel h3 { margin: 0 0 8px; font-size: 15px; display: flex; align-items: center; gap: 10px; }
.panel-hint { font-weight: 400; color: var(--muted); font-size: 12px; }
.drag-hint { color: #1f6feb; }

.timeline { position: relative; height: 132px; margin: 18px 8px 0; }
.timeline-axis { position: absolute; top: 58px; left: 0; right: 0; height: 2px; background: #cbd5e1; }
.tl-item { position: absolute; top: 0; transform: translateX(-50%); display: flex; flex-direction: column; align-items: center; gap: 2px; background: none; border: none; cursor: pointer; width: 130px; }
.tl-dot { width: 12px; height: 12px; border-radius: 50%; margin-top: 44px; border: 2px solid #fff; box-shadow: 0 0 0 1px #94a3b8; }
.tl-item.current .tl-dot { background: #1f6feb; }
.tl-item.review .tl-dot { background: #d97706; }
.tl-item.trace .tl-dot { background: #fff; border: 2px dashed #94a3b8; box-shadow: none; }
.tl-item.gap .tl-dot { box-shadow: 0 0 0 3px rgba(180, 35, 24, 0.25); }
.tl-item.active .tl-label { color: #1f6feb; font-weight: 700; }
.tl-item.trace .tl-label, .tl-item.trace .tl-meta { color: #94a3b8; }
.tl-label { font-size: 12px; white-space: nowrap; }
.tl-meta { font-size: 11px; color: var(--muted); white-space: nowrap; }

.map-canvas { width: 100%; height: 380px; border: 1px solid var(--border); border-radius: 6px; cursor: crosshair; }
.map-image { stroke-width: 2; cursor: pointer; }
.map-image.current { fill: rgba(31, 111, 235, 0.10); stroke: #1f6feb; }
.map-image.review { fill: rgba(217, 119, 6, 0.12); stroke: #d97706; stroke-dasharray: 6 3; }
.map-image.trace { fill: rgba(100, 116, 139, 0.06); stroke: #94a3b8; stroke-dasharray: 4 3; }
.map-image.gap { stroke: #b42318; stroke-width: 2.5; }
.map-image.active { stroke-width: 4; }
.map-polygon { fill: rgba(217, 119, 6, 0.25); stroke: #b45309; stroke-width: 1.5; cursor: pointer; }
.map-polygon.pending { fill: rgba(217, 119, 6, 0.4); }
.map-polygon.gap { stroke: #b42318; }
.map-polygon.active { stroke-width: 3; stroke: #1f6feb; }
.map-label { font-size: 10px; fill: #1e3a8a; text-anchor: middle; pointer-events: none; }
.map-poly-label { font-size: 9px; fill: #7c2d12; text-anchor: middle; pointer-events: none; font-weight: 700; }
.map-select { fill: rgba(31, 111, 235, 0.12); stroke: #1f6feb; stroke-dasharray: 4 3; }
.legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--muted); margin-top: 6px; }
.lg { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
.lg.current { background: #1f6feb; }
.lg.review { background: #d97706; }
.lg.trace { background: #fff; border: 1px dashed #94a3b8; }
.lg.gap { background: #b42318; }
.lg.pending { background: rgba(217, 119, 6, 0.5); }

.dispatch-bar { display: flex; gap: 6px; margin-bottom: 8px; flex-wrap: wrap; }
.dispatch-bar input { flex: 1; min-width: 120px; padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 12px; }
.todo-list { list-style: none; margin: 0; padding: 0; max-height: 330px; overflow: auto; display: flex; flex-direction: column; gap: 8px; }
.todo-item { display: flex; gap: 8px; border: 1px solid var(--border); border-radius: 6px; padding: 8px; }
.todo-item.active { border-color: #1f6feb; box-shadow: 0 0 0 1px #1f6feb inset; }
.todo-body { flex: 1; cursor: pointer; }
.todo-head { display: flex; gap: 6px; align-items: center; font-size: 13px; flex-wrap: wrap; }
.todo-meta { font-size: 12px; color: var(--muted); margin-top: 2px; }
.todo-conclusion { font-size: 12px; color: #475569; margin-top: 2px; }
.conclusion-row { display: flex; gap: 6px; margin-top: 4px; }
.conclusion-row input { flex: 1; padding: 3px 6px; font-size: 12px; border: 1px solid var(--border); border-radius: 4px; }

.badge { display: inline-block; border-radius: 4px; padding: 1px 6px; font-size: 11px; border: 1px solid transparent; }
.badge.ok { background: #ecfdf3; color: #027a48; border-color: #a6f4c5; }
.badge.alert { background: #fff4ed; color: #b93815; border-color: #fdb022; }
.badge.warn { background: #fef3f2; color: #b42318; border-color: #fda29b; }
.badge.review { background: #fffaeb; color: #b54708; border-color: #fedf89; }
.badge.trace { background: #f2f4f7; color: #667085; border-color: #d0d5dd; }
.flag.warn { display: inline-block; background: #fef3f2; color: #b42318; border: 1px solid #fda29b; border-radius: 4px; padding: 1px 6px; margin: 1px 4px 1px 0; font-size: 11px; }
.cell-sub { display: block; color: var(--muted); font-size: 11px; }
.stat-value.warn { color: #b42318; }
.stat-value.review { color: #b54708; }

tr.trace td { color: #94a3b8; background: #fafbfc; }
tr.active td { background: #f0f6ff; }
.row-actions { white-space: nowrap; }
.link.danger { color: #b42318; }
.pager { display: flex; gap: 8px; align-items: center; margin-top: 8px; }

.sum-title { font-size: 13px; margin: 14px 0 6px; color: #1f2937; }
.data-table.compact th, .data-table.compact td { padding: 6px 8px; font-size: 12px; }
.archive-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; }
.archive-card { border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; }
.archive-card header { display: flex; justify-content: space-between; gap: 8px; }
.archive-current { font-size: 12px; margin-top: 4px; }
.archive-counts { font-size: 12px; color: var(--muted); }
.archive-list { list-style: none; margin: 6px 0 0; padding: 0; font-size: 12px; display: flex; flex-direction: column; gap: 3px; }
.archive-list li.tomb { color: #94a3b8; text-decoration: line-through; }
.archive-list em { display: block; font-style: normal; }
.ok-text { color: #027a48; }

@media (max-width: 1100px) {
  .link-grid { grid-template-columns: 1fr; grid-template-areas: 'timeline' 'map' 'todo'; }
}
</style>
