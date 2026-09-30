/** 影像核查队列前端类型，与后端 services/remote.py 返回结构对齐。 */

export interface ImageItem {
  id: number
  status: string
  数据编号: string
  数据源: string
  分辨率: string
  覆盖面积: string
  获取日期: string
  解译内容: string
  解译人员: string
  source_key: string
  version: number
  quality: string
  is_current: boolean
  trace_only: boolean
  tombstoned: boolean
  resolution_m: number
  acquired_at: number
  footprint: number[][]
  archive_code: string
  review_note: string
  version_count: number
  multi_version: boolean
  polygon_total: number
  polygon_pending: number
  polygon_gap: number
  pending_dispatch: boolean
  resolution_gap: boolean
  gap_reasons: string[]
}

export interface PolygonItem {
  id: number
  status: string
  polygon_no: string
  name: string
  image_id: number
  image_no: string
  image_current: boolean
  image_trace: boolean
  required_resolution_m: number
  assigned: boolean
  assignee: string
  conclusion: string
  conclusion_seq: number
  footprint: number[][]
  resolution_gap: boolean
  pending_dispatch: boolean
}

export interface SourceGroup {
  source_key: string
  数据源: string
  archive_code: string
  versions: ImageItem[]
  current: ImageItem | null
  version_count: number
  resolution_gap: boolean
  gap_reasons: string[]
  pending_polygons: number
  scale: number
}

export interface ArchiveVolume {
  archive_code: string
  sources: string[]
  current_no: string | null
  trace_count: number
  deleted_count: number
  entries: Array<{
    id: number
    数据编号: string
    version: number
    quality: string
    status: string
    is_current: boolean
    trace_only: boolean
    tombstoned: boolean
    分辨率: string
    获取日期: string
    review_note: string
  }>
}

export interface PageResult {
  items: ImageItem[]
  total: number
  page: number
  size: number
  next_cursor: string | null
  index_version: number
  derived_version: number
  stale: boolean
  conditions: Record<string, unknown>
}

export interface Workspace {
  conditions: Record<string, unknown>
  context: string | null
  index_version: number
  images: ImageItem[]
  polygons: PolygonItem[]
  sources: SourceGroup[]
  world: { width: number; height: number }
}

export interface Summary {
  conditions: Record<string, unknown>
  context: string | null
  index_version: number
  inventory: SourceGroup[]
  polygon_todo: PolygonItem[]
  archive: ArchiveVolume[]
  counts: {
    sources: number
    todo_polygons: number
    unassigned: number
    resolution_gap_sources: number
    archive_volumes: number
  }
}

export interface LocateResult {
  context: string
  conditions: Record<string, unknown>
  trail: string[]
  matched: { images: number; polygons: number }
  focus: { images: number[]; polygons: number[] }
}

export interface DispatchResult {
  ok: boolean
  message: string
  assigned?: number
  conflicts?: Array<{ polygon_id: number; polygon_no: string; kept_seq: number; kept_conclusion: string; dropped_seq: number }>
  items?: PolygonItem[]
  replayed?: boolean
  context?: string
}
