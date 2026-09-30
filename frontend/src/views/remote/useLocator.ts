import { reactive, watch } from 'vue'

import type { LocatorConditions } from './types'

/**
 * 定位上下文：时间轴/地图/清单/图斑/汇总共享同一份条件。
 * 落在 sessionStorage，切页签、派发失败、翻页后都保持此前定位条件。
 */
const STORAGE_KEY = 'remote-queue-locator'

interface PersistedState {
  conditions: LocatorConditions
  locatorId: string | null
  queueCursor: string | null
  parcelCursorId: number | null
}

function load(): PersistedState {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (raw) {
      return { queueCursor: null, parcelCursorId: null, ...JSON.parse(raw) }
    }
  } catch {
    /* 损坏的本地缓存按空状态处理 */
  }
  return { conditions: {}, locatorId: null, queueCursor: null, parcelCursorId: null }
}

const state = reactive<PersistedState>(load())

watch(
  state,
  (value) => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value))
    } catch {
      /* 隐私模式等场景忽略持久化失败 */
    }
  },
  { deep: true },
)

function cleanConditions(conditions: LocatorConditions): LocatorConditions {
  return Object.fromEntries(
    Object.entries(conditions).filter(([, value]) => value !== '' && value != null),
  )
}

export function useLocator() {
  function setConditions(conditions: LocatorConditions, locatorId: string | null = null) {
    state.conditions = cleanConditions(conditions)
    state.locatorId = locatorId
    // 条件变更后旧游标失效，从首页重新开始。
    state.queueCursor = null
    state.parcelCursorId = null
  }

  function patchConditions(patch: LocatorConditions) {
    setConditions({ ...state.conditions, ...patch })
  }

  function reset() {
    setConditions({}, null)
  }

  return {
    state,
    setConditions,
    patchConditions,
    reset,
  }
}
