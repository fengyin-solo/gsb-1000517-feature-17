/** 影像核查队列接口类型。 */

export interface Footprint {
  x: number
  y: number
  w: number
  h: number
}

export type QueueFlag = 'multi_version' | 'resolution_gap' | 'undispatched'

export interface LocatorConditions {
  keyword?: string
  source?: string
  date_from?: string
  date_to?: string
  cell?: string
  flag?: QueueFlag
}

export interface ImageVersion {
  id: number
  series_key: string
  version: string
  data_code: string
  source: string
  resolution: string
  acquired_at: string
  footprint: Footprint | null
  review_status: '审核通过' | '审核驳回' | '待审核'
  review_note: string
  is_effective: boolean
  superseded: boolean
  archive_path: string
}

export interface QueueSeries {
  series_key: string
  effective: ImageVersion | null
  effective_passed: boolean
  version_count: number
  multi_version: boolean
  resolution_gap: boolean
  gap_cells: string[]
  undispatched_count: number
  undispatched_parcel_ids: number[]
  history: ImageVersion[]
}

export interface QueuePage {
  items: QueueSeries[]
  size: number
  next_cursor: string | null
  has_more: boolean
  locator_id: string | null
  conditions: LocatorConditions
}

export interface TimelineMonth {
  month: string
  series_count: number
  items: QueueSeries[]
}

export interface MapFeature {
  series_key: string
  image_id: number
  footprint: Footprint
  resolution: string
  flags: QueueFlag[]
}

export interface ParcelFeature {
  id: number
  series_key: string
  code: string
  footprint: Footprint
  status: string
}

export interface MapData {
  grid_size: number
  gap_threshold_meters: number
  features: MapFeature[]
  parcels: ParcelFeature[]
  conditions: LocatorConditions
  locator_id: string | null
}

export interface Parcel {
  id: number
  code: string
  series_key: string
  footprint: Footprint
  status: '待派发' | '已派发' | '已完成'
  assignee: string | null
  feature: string
  last_conclusion: string | null
  concluded_at: string | null
  dispatch_batch: string | null
  dispatched_at: string | null
}

export interface ParcelPage {
  items: Parcel[]
  size: number
  next_cursor_id: number | null
  has_more: boolean
  conditions: LocatorConditions
  locator_id: string | null
}

export interface DispatchResult {
  ok: boolean
  replayed: boolean
  idempotency_key: string
  dispatched_parcel_ids: number[]
  conflicts: {
    parcel_id: number
    kept_conclusion: string
    kept_at: string
    ignored_conclusion: string
  }[]
  message: string
}

export interface SummaryGroup {
  key?: string
  label?: string
  count?: number
  archive_path?: string
  series_count?: number
  versions?: number
  effective?: number
  trace_only?: number
}

export interface Summary {
  locator_id: string | null
  conditions: LocatorConditions
  remote_list: { groups: SummaryGroup[]; items: QueueSeries[] }
  parcel_todo: { groups: SummaryGroup[] }
  archive_catalog: { groups: SummaryGroup[] }
}
