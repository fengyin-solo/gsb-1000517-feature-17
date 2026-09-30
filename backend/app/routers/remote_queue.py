"""影像核查队列接口。

在原「遥感解译」列表（/api/remote）之外提供升级视图：时间轴/地图联动
定位、同源版本追溯、图斑待办派发与汇总互相关联。列表走不透明游标，
派发走幂等键；业务判断全部在 RemoteQueueService 中。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.remote_queue import (
    PARCEL_PENDING,
    REVIEW_PASSED,
    REVIEW_PENDING,
    REVIEW_REJECTED,
    RemoteQueueService,
)

router = APIRouter(prefix="/api/remote-queue", tags=["影像核查队列"])

service = RemoteQueueService()


class LocatorPayload(BaseModel):
    """时间轴/地图联动定位条件；与游标内嵌条件同一套口径。"""

    keyword: str | None = None
    source: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    cell: str | None = Field(default=None, description="地图网格单元编号，如 1,0")
    bbox: list[float] | None = Field(default=None, description="地图视窗 [x0,y0,x1,y1]，归一化 0~100")


class ReviewPayload(BaseModel):
    result: str = Field(description=f"{REVIEW_PASSED} 或 {REVIEW_REJECTED}")
    note: str | None = None


class VersionPayload(BaseModel):
    series_key: str
    data_code: str | None = None
    source: str
    resolution: str
    acquired_at: str
    footprint: dict[str, Any] | None = None


class DispatchPayload(BaseModel):
    idempotency_key: str = Field(description="集中派发幂等键，重试复用同一键")
    parcel_ids: list[int]
    assignee: str
    conclusion: str | None = Field(default=None, description="本次解译结论；冲突时与已有结论比新旧")
    concluded_at: str | None = Field(default=None, description="结论时间，缺省取服务端当前时间")


def _locator_kwargs(payload: LocatorPayload | None, locator_id: str | None) -> dict[str, Any]:
    conditions = payload.model_dump(exclude_none=True) if payload is not None else None
    return {"conditions": conditions, "locator_id": locator_id}


@router.get("/queue")
def list_queue(
    keyword: str | None = None,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    cell: str | None = None,
    bbox: str | None = Query(default=None, description="逗号分隔 x0,y0,x1,y1"),
    flag: str | None = Query(default=None, description="multi_version / resolution_gap / undispatched"),
    locator_id: str | None = None,
    cursor: str | None = None,
    size: int = 20,
) -> dict[str, Any]:
    """影像核查队列：同源系列聚合成一行，游标翻页不重不漏、保留定位条件。"""
    conditions = {
        "keyword": keyword, "source": source, "date_from": date_from,
        "date_to": date_to, "cell": cell,
    }
    if bbox:
        try:
            parsed = [float(part) for part in bbox.split(",")]
            if len(parsed) != 4:
                raise ValueError
        except ValueError:
            raise HTTPException(status_code=400, detail="bbox 需为 x0,y0,x1,y1 四个数字")
        conditions["bbox"] = parsed
    if flag not in (None, "multi_version", "resolution_gap", "undispatched"):
        raise HTTPException(status_code=400, detail="flag 仅支持 multi_version / resolution_gap / undispatched")
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条")
    return service.list_queue(
        conditions={k: v for k, v in conditions.items() if v not in (None, "")},
        locator_id=locator_id, flag=flag, cursor=cursor, size=size,
    )


@router.get("/timeline")
def timeline(
    keyword: str | None = None, source: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
    cell: str | None = None, locator_id: str | None = None,
) -> dict[str, Any]:
    """时间轴视图：按获取月份聚合命中的同源系列。"""
    return service.timeline(conditions={
        "keyword": keyword, "source": source,
        "date_from": date_from, "date_to": date_to, "cell": cell,
    }, locator_id=locator_id)


@router.get("/map")
def map_view(
    keyword: str | None = None, source: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
    cell: str | None = None, locator_id: str | None = None,
) -> dict[str, Any]:
    """地图视图：返回有效影像覆盖范围与图斑位置，标记三类核查旗标。"""
    return service.map_view(conditions={
        "keyword": keyword, "source": source,
        "date_from": date_from, "date_to": date_to, "cell": cell,
    }, locator_id=locator_id)


@router.post("/locator")
def save_locator(payload: LocatorPayload) -> dict[str, Any]:
    """固化一次定位条件，清单/图斑/汇总凭 locator_id 互相关联。"""
    return service.save_locator(payload.model_dump(exclude_none=True))


@router.get("/parcels")
def list_parcels(
    status: str | None = Query(default=None, description="待派发 / 已派发 / 已完成"),
    series_key: str | None = None,
    locator_id: str | None = None,
    cursor_id: int | None = None,
    size: int = 20,
) -> dict[str, Any]:
    """图斑待办：可单独过滤，也可凭定位号沿用定位上下文。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条")
    return service.list_parcels(
        status=status, series_key=series_key, locator_id=locator_id,
        cursor_id=cursor_id, size=size,
    )


@router.post("/dispatch")
def dispatch(payload: DispatchPayload) -> dict[str, Any]:
    """集中派发：幂等键重放原样返回；结论冲突以最新一次解译结论为准。

    校验失败（缺键/图斑不存在等）返回 400 且不改任何状态，调用方可维持
    此前的定位上下文重试。
    """
    result, error = service.dispatch(payload.model_dump())
    if error:
        raise HTTPException(status_code=400, detail=error)
    return result


@router.post("/images/{image_id}/review")
def review_image(image_id: int, payload: ReviewPayload) -> dict[str, Any]:
    """登记质量审核结论；审核通过的新版本自动成为同源有效版本。"""
    image, message = service.review_image(image_id, payload.result, payload.note)
    if image is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "image": image}


@router.post("/images/versions")
def register_version(payload: VersionPayload) -> dict[str, Any]:
    """登记同源新版本（默认待审核）；旧有效版本保留追溯。"""
    image, missing = service.register_version(payload.model_dump())
    if missing:
        raise HTTPException(status_code=400, detail=f"缺少必填字段：{'、'.join(missing)}")
    return {"ok": True, "message": "新版本已登记，等待质量审核", "image": image}


@router.delete("/images/{image_id}")
def delete_image(image_id: int) -> dict[str, Any]:
    """删除影像：仅允许删除非有效版本；有效版本需以新版本替换。"""
    ok, message = service.delete_image(image_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message}


@router.get("/summary")
def summary(locator_id: str | None = None) -> dict[str, Any]:
    """汇总页：遥感清单、图斑待办、存档目录在同一定位条件下的统计。"""
    return service.summary(locator_id=locator_id)
