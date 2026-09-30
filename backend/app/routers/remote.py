"""影像核查队列接口。

围绕时间轴/地图联动定位组织：
- GET  /api/remote                 影像清单（游标分页，兼容此前定位条件）
- POST /api/remote/locate          提交一次定位，生成串联上下文 token
- GET  /api/remote/workspace       时间轴 + 地图联动数据
- GET  /api/remote/summary         遥感清单 / 图斑待办 / 存档目录汇总
- GET  /api/remote/polygons        图斑待办列表
- POST /api/remote/polygons/{id}/conclusion  登记解译结论（自增序号）
- POST /api/remote/dispatch        集中派发（幂等键 + 最新结论优先）
- POST /api/remote                 登记影像（自动并入同源版本谱系）
- POST /api/remote/{id}/actions    质量审核动作
- POST /api/remote/{id}/replace    以旧版为底提交新版本
- DELETE /api/remote/{id}          影像下线（墓碑，游标仍可遍历）
- GET  /api/remote/export          导出当前定位条件下的影像清单
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.schemas import (
    ActionResult,
    EntryPayload,
    RemoteConclusionPayload,
    RemoteDispatchPayload,
    RemoteReviewPayload,
)
from app.services.remote import RemoteService

router = APIRouter(prefix="/api/remote", tags=["遥感解译"])

service = RemoteService()

LIST_FIELDS = ["数据编号", "数据源", "分辨率", "覆盖面积", "获取日期", "解译内容", "解译人员", "数据状态"]


class LocatePayload(BaseModel):
    """定位条件：query 形态的条件 + 直接指定的影像/图斑焦点。"""

    keyword: str | None = None
    status: str | None = None
    source_key: str | None = None
    quality: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    scale: float | None = None
    bbox: list[float] | None = None
    only_gap: bool = False
    image_id: int | None = None
    polygon_id: int | None = None
    focus_images: list[int] | None = None
    focus_polygons: list[int] | None = None


class ReplacePayload(BaseModel):
    """替换影像：仅提供变化字段，其余沿用旧版。"""

    values: dict[str, Any] = {}


def _parse_bbox(bbox_text: str | None) -> list[float] | None:
    if not bbox_text:
        return None
    try:
        values = [float(part.strip()) for part in bbox_text.split(",")]
    except ValueError:
        raise HTTPException(status_code=400, detail="bbox 需为 minx,miny,maxx,maxy 四个数值")
    if len(values) != 4:
        raise HTTPException(status_code=400, detail="bbox 需包含四个数值：minx,miny,maxx,maxy")
    return values


def _parse_int_list(text: str | None) -> list[int] | None:
    if text is None:
        return None
    return [int(part) for part in text.split(",") if part.strip()]


def _list_params(
    keyword: str | None,
    status: str | None,
    source_key: str | None,
    quality: str | None,
    date_from: str | None,
    date_to: str | None,
    scale: float | None,
    bbox: str | None,
    only_gap: bool,
    include_trace: bool,
    focus: str | None,
    page: int,
    size: int,
) -> tuple[dict[str, Any], int, int]:
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    params: dict[str, Any] = {
        "keyword": keyword,
        "status": status,
        "source_key": source_key,
        "quality": quality,
        "date_from": date_from,
        "date_to": date_to,
        "scale": scale,
        "bbox": _parse_bbox(bbox),
        "only_gap": only_gap,
        "include_trace": include_trace,
        "focus_images": _parse_int_list(focus),
    }
    return params, max(page, 1), size


@router.get("")
def list_entries(
    keyword: str | None = Query(default=None, description="按数据编号/数据源/解译内容检索"),
    status: str | None = Query(default=None, description="当前有效、待质检、仅追溯、已删除"),
    source_key: str | None = Query(default=None, description="同源影像键"),
    quality: str | None = Query(default=None, description="审核通过、待审核、审核驳回"),
    date_from: str | None = Query(default=None, description="获取日期起 YYYY-MM-DD"),
    date_to: str | None = Query(default=None, description="获取日期止 YYYY-MM-DD"),
    scale: float = Query(default=2.0, description="目标解译尺度（米），用于识别分辨率缺口"),
    bbox: str | None = Query(default=None, description="地图范围 minx,miny,maxx,maxy"),
    only_gap: bool = Query(default=False, description="只看分辨率缺口影像"),
    include_trace: bool = Query(default=False, description="是否包含仅追溯旧版"),
    focus: str | None = Query(default=None, description="定位焦点影像 id，逗号分隔"),
    context: str | None = Query(default=None, description="此前定位产生的上下文 token"),
    cursor: str | None = Query(default=None, description="上一页返回的浏览游标"),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """影像核查队列：按不可变索引快照游标分页，替换/删除后翻页不重不漏。"""
    params, page, size = _list_params(
        keyword, status, source_key, quality, date_from, date_to, scale,
        bbox, only_gap, include_trace, focus, page, size,
    )
    try:
        return service.query_page(params, context_token=context, cursor_token=cursor, page=page, size=size)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/workspace")
def workspace(
    keyword: str | None = None,
    status: str | None = None,
    source_key: str | None = None,
    quality: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    scale: float = 2.0,
    bbox: str | None = None,
    only_gap: bool = False,
    include_trace: bool = False,
    focus: str | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    """时间轴与地图共用的一份联动数据。"""
    params, _, _ = _list_params(
        keyword, status, source_key, quality, date_from, date_to, scale,
        bbox, only_gap, include_trace, focus, 1, 200,
    )
    return service.workspace(params, context)


@router.get("/summary")
def summary(
    keyword: str | None = None,
    status: str | None = None,
    source_key: str | None = None,
    quality: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    scale: float = 2.0,
    bbox: str | None = None,
    only_gap: bool = False,
    include_trace: bool = True,
    focus: str | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    """定位结论汇总：遥感清单、图斑待办、存档目录共用同一套定位条件。"""
    params, _, _ = _list_params(
        keyword, status, source_key, quality, date_from, date_to, scale,
        bbox, only_gap, include_trace, focus, 1, 200,
    )
    return service.summary(params, context)


@router.get("/polygons")
def list_polygons(
    assigned: bool | None = Query(default=None, description="只看已/未派发图斑"),
    source_key: str | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    """图斑待办：默认跟随定位上下文中的影像范围。"""
    params: dict[str, Any] = {"source_key": source_key}
    if assigned is not None:
        params["assigned"] = assigned
    return service.list_polygons(params, context)


@router.post("/locate")
def locate(payload: LocatePayload) -> dict[str, Any]:
    """提交一次联动定位；与此前条件取交集，返回新的定位上下文 token。"""
    raw = payload.model_dump(exclude_none=True)
    return service.locate(raw, context_token=None)


@router.post("/locate/{context_token}")
def locate_with_context(context_token: str, payload: LocatePayload) -> dict[str, Any]:
    """在已有定位上下文上继续收窄/补充焦点，条件与此前互相关联。"""
    raw = payload.model_dump(exclude_none=True)
    if context_token not in service._contexts:
        raise HTTPException(status_code=404, detail="定位上下文已失效，请重新发起定位")
    return service.locate(raw, context_token=context_token)


@router.post("/dispatch")
def dispatch(payload: RemoteDispatchPayload) -> dict[str, Any]:
    """集中派发图斑：幂等键去重，冲突时保留图斑上最新的解译结论。"""
    return service.dispatch(payload.model_dump(), context_token="")


@router.post("/dispatch/{context_token}")
def dispatch_with_context(context_token: str, payload: RemoteDispatchPayload) -> dict[str, Any]:
    """携带定位上下文派发：失败时原样带回上下文，页面条件不丢失。"""
    return service.dispatch(payload.model_dump(), context_token=context_token)


@router.post("/polygons/{polygon_id}/conclusion", response_model=ActionResult)
def record_conclusion(polygon_id: int, payload: RemoteConclusionPayload) -> ActionResult:
    """登记一版图斑解译结论；结论序号自增，作为派发冲突的裁决依据。"""
    polygon, message = service.record_conclusion(polygon_id, payload.conclusion)
    if polygon is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=polygon)


@router.get("/export")
def export_entries(
    source_key: str | None = None,
    context: str | None = None,
    include_trace: bool = True,
) -> dict[str, Any]:
    """导出当前定位条件下的影像核查清单（含同源版本谱系）。"""
    page = service.query_page(
        {"source_key": source_key, "include_trace": include_trace},
        context_token=context,
        page=1,
        size=10000,
    )
    return {"module": "remote", "total": page["total"], "items": page["items"], "conditions": page["conditions"]}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict[str, Any]:
    """读取单条影像明细（含已下线/仅追溯版本）。"""
    entry = service._find_image(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"遥感数据 {entry_id} 不存在")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条影像；按数据编号版本后缀自动归入同源版本谱系。"""
    try:
        entry, missing = service.register_image(payload.values)
    except ValueError as exc:
        return ActionResult(ok=False, message=str(exc))
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"影像已登记（索引版本 {service.index_version}）", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: RemoteReviewPayload) -> ActionResult:
    """质量审核动作：审核通过晋升当前版本，审核驳回降为仅追溯。"""
    entry, message = service.review_image(entry_id, payload.action, payload.note)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/replace", response_model=ActionResult)
def replace_entry(entry_id: int, payload: ReplacePayload) -> ActionResult:
    """以旧版为底提交替换新版本，进入待质检；旧游标继续可用。"""
    entry, message = service.replace_image(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.delete("/{entry_id}")
def delete_entry(entry_id: int) -> ActionResult:
    """影像下线：立墓碑，存档目录可追溯，已发出的浏览游标不受影响。"""
    entry, message = service.delete_image(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
