"""影像核查队列业务规则。

升级自原“遥感数据列表”：围绕同源影像版本链、时间轴/地图联动定位、
分辨率缺口与未派发图斑组织核查工作。关键约定：

- 同源影像以「质量审核通过」的最新版本为准，旧版只保留追溯；
- 列表使用 (获取日期, 影像id) 复合键的不透明游标分页，删除或替换
  影像后翻页不重复、不漏项，游标内嵌定位条件快照；
- 集中派发走幂等键，结论冲突时以最新一次解译结论为准；
- 未成功（校验失败/派发异常）时不改任何状态，维持此前定位上下文。
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Any

from app.store import store

# 网格单元尺寸（归一化坐标 0~100），分辨率缺口按同源覆盖网格判断。
GRID_SIZE = 50
# 各网格期望达到的最优分辨率（米）；粗于该值即视为缺口。
GAP_THRESHOLD_METERS = 2.0

REVIEW_PASSED = "审核通过"
REVIEW_REJECTED = "审核驳回"
REVIEW_PENDING = "待审核"
PARCEL_PENDING = "待派发"
PARCEL_DISPATCHED = "已派发"
PARCEL_DONE = "已完成"


def _new_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _cells_of(footprint: dict[str, Any] | None) -> set[str]:
    """把覆盖范围（归一化 {x,y,w,h}）折算成网格单元编号集合。"""
    if not footprint:
        return set()
    x = max(float(footprint.get("x", 0)), 0.0)
    y = max(float(footprint.get("y", 0)), 0.0)
    w = max(float(footprint.get("w", 0)), 0.0)
    h = max(float(footprint.get("h", 0)), 0.0)
    cells: set[str] = set()
    for gx in range(int(x // GRID_SIZE), int((x + w) // GRID_SIZE) + 1):
        for gy in range(int(y // GRID_SIZE), int((y + h) // GRID_SIZE) + 1):
            cells.add(f"{gx},{gy}")
    return cells


def _parse_meters(value: Any) -> float | None:
    """分辨率字段形如 '0.5m' / '2米'，取出数值用于缺口比较。"""
    text = str(value or "").strip().lower().replace("米", "m")
    if not text:
        return None
    token = text.replace("m", "").strip()
    try:
        return float(token)
    except ValueError:
        return None


class RemoteQueueService:
    def __init__(self) -> None:
        # 幂等键 -> 首次派发结果（重放时原样返回，不重复派发）。
        self._dispatch_cache: dict[str, dict[str, Any]] = {}
        # 定位上下文定位号 -> 条件快照（定位结论跨页签/汇总互相关联）。
        self._locators: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 版本链：同源影像以审核通过的最新版本为准
    # ------------------------------------------------------------------
    def _images(self) -> list[dict[str, Any]]:
        return store.rows("remote_image")

    def _parcels(self) -> list[dict[str, Any]]:
        return store.rows("remote_parcel")

    def _versions_of(self, series_key: str) -> list[dict[str, Any]]:
        rows = [img for img in self._images() if img["series_key"] == series_key]
        return sorted(rows, key=lambda img: (img["acquired_at"], img["id"]))

    def _effective(self, series_key: str) -> dict[str, Any] | None:
        """当前有效版本：审核通过中取获取日期最新者；都没通过则无有效版本。"""
        passed = [
            img for img in self._versions_of(series_key)
            if img["review_status"] == REVIEW_PASSED
        ]
        return passed[-1] if passed else None

    def _version_label(self, image: dict[str, Any], versions: list[dict[str, Any]]) -> str:
        order = [img["id"] for img in versions]
        return f"v{order.index(image['id']) + 1}"

    def _public_image(self, image: dict[str, Any], versions: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": image["id"],
            "series_key": image["series_key"],
            "version": self._version_label(image, versions),
            "data_code": image["data_code"],
            "source": image["source"],
            "resolution": image["resolution"],
            "acquired_at": image["acquired_at"],
            "footprint": image["footprint"],
            "review_status": image["review_status"],
            "review_note": image.get("review_note", ""),
            "is_effective": self._effective(image["series_key"]) is image,
            "superseded": image.get("superseded", False),
            "archive_path": image["archive_path"],
        }

    # ------------------------------------------------------------------
    # 游标分页：(acquired_at desc, id desc) 复合键，内嵌定位条件快照
    # ------------------------------------------------------------------
    @staticmethod
    def _encode_cursor(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return base64.urlsafe_b64encode(raw.encode("utf-8")).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> dict[str, Any] | None:
        if not cursor:
            return None
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict) or "acquired_at" not in payload or "id" not in payload:
            return None
        return payload

    def _resolve_locator(
        self,
        conditions: dict[str, Any] | None,
        locator_id: str | None,
    ) -> dict[str, Any]:
        """定位条件优先级：已保存的定位上下文 > 本次提交条件 > 空条件。"""
        if locator_id and locator_id in self._locators:
            return dict(self._locators[locator_id])
        return {k: v for k, v in (conditions or {}).items() if v not in (None, "", [])}

    def _match_series(self, series_key: str, cond: dict[str, Any]) -> bool:
        versions = self._versions_of(series_key)
        effective = self._effective(series_key)
        row = effective or (versions[-1] if versions else None)
        if row is None:
            return False
        if cond.get("keyword"):
            haystack = f"{row['data_code']} {row['source']} {series_key}"
            if str(cond["keyword"]) not in haystack:
                return False
        if cond.get("source") and str(cond["source"]) not in str(row["source"]):
            return False
        if cond.get("date_from") and row["acquired_at"] < str(cond["date_from"]):
            return False
        if cond.get("date_to") and row["acquired_at"] > str(cond["date_to"]):
            return False
        if cond.get("cell"):
            if str(cond["cell"]) not in _cells_of(row.get("footprint")):
                return False
        if cond.get("bbox"):
            x0, y0, x1, y1 = (float(v) for v in cond["bbox"])
            fp = row.get("footprint") or {}
            fx, fy, fw, fh = (
                float(fp.get("x", 0)), float(fp.get("y", 0)),
                float(fp.get("w", 0)), float(fp.get("h", 0)),
            )
            if fx >= x1 or fx + fw <= x0 or fy >= y1 or fy + fh <= y0:
                return False
        return True

    def _resolution_gap_cells(self, versions: list[dict[str, Any]]) -> set[str]:
        """同源覆盖网格里，有效版本最优分辨率仍粗于期望阈值的单元。"""
        effective = [img for img in versions if img["review_status"] == REVIEW_PASSED]
        if not effective:
            return set()
        best: dict[str, float] = {}
        for img in effective:
            meters = _parse_meters(img["resolution"])
            if meters is None:
                continue
            for cell in _cells_of(img.get("footprint")):
                best[cell] = min(best.get(cell, meters), meters)
        return {cell for cell, meters in best.items() if meters > GAP_THRESHOLD_METERS}

    def _series_item(self, series_key: str) -> dict[str, Any]:
        versions = self._versions_of(series_key)
        effective = self._effective(series_key)
        row = effective or (versions[-1] if versions else None)
        gap_cells = self._resolution_gap_cells(versions)
        undispatched = [
            p for p in self._parcels()
            if p["series_key"] == series_key and p["status"] == PARCEL_PENDING
        ]
        return {
            "series_key": series_key,
            "effective": self._public_image(row, versions) if row else None,
            "effective_passed": effective is not None,
            "version_count": len(versions),
            "multi_version": len(versions) > 1,
            "resolution_gap": bool(gap_cells),
            "gap_cells": sorted(gap_cells),
            "undispatched_count": len(undispatched),
            "undispatched_parcel_ids": [p["id"] for p in undispatched],
            "history": [self._public_image(img, versions) for img in reversed(versions)],
        }

    def _all_series(self) -> list[str]:
        return sorted({img["series_key"] for img in self._images()})

    def list_queue(
        self,
        *,
        conditions: dict[str, Any] | None = None,
        locator_id: str | None = None,
        flag: str | None = None,
        cursor: str | None = None,
        size: int = 20,
    ) -> dict[str, Any]:
        """影像核查队列：按同源系列聚合，游标保证增删替换后翻页不重不漏。"""
        size = max(1, min(size, 200))
        decoded = self._decode_cursor(cursor)
        if decoded is not None:
            # 游标自带定位条件快照：翻页一律沿用此前的定位上下文。
            cond = dict(decoded.get("conditions", {}))
            # 旗标也是定位条件的一部分，从快照恢复，第二页不靠调用方重传。
            flag = flag or cond.pop("flag", None)
        else:
            cond = self._resolve_locator(conditions, locator_id)

        items = [self._series_item(key) for key in self._all_series()]
        items = [item for item in items if self._match_series(item["series_key"], cond)]
        if flag == "multi_version":
            items = [item for item in items if item["multi_version"]]
        elif flag == "resolution_gap":
            items = [item for item in items if item["resolution_gap"]]
        elif flag == "undispatched":
            items = [item for item in items if item["undispatched_count"] > 0]
        if flag:
            cond = {**cond, "flag": flag}

        # 有效版本取不到时退回最近版本，保证该系列仍可核查。
        def sort_key(item: dict[str, Any]) -> tuple[str, int]:
            image = item["effective"]
            return (
                image["acquired_at"] if image else "0000-00-00",
                image["id"] if image else 0,
            )

        items.sort(key=sort_key, reverse=True)

        if decoded is not None:
            marker = (str(decoded["acquired_at"]), int(decoded["id"]))
            kept: list[dict[str, Any]] = []
            for item in items:
                image = item["effective"]
                key = (image["acquired_at"] if image else "0000-00-00",
                       image["id"] if image else 0)
                # 严格小于游标位置：被删除的游标行自然跳过，不会重放或顶位。
                if key < marker:
                    kept.append(item)
            items = kept

        page = items[:size]
        next_cursor = None
        if page and len(items) > size:
            last = page[-1]["effective"]
            snapshot = {
                "acquired_at": last["acquired_at"] if last else "0000-00-00",
                "id": last["id"] if last else 0,
                "conditions": cond,
            }
            next_cursor = self._encode_cursor(snapshot)
        return {
            "items": page,
            "size": size,
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
            "locator_id": locator_id,
            "conditions": cond,
        }

    # ------------------------------------------------------------------
    # 时间轴 / 地图：同一套定位条件的两个视图
    # ------------------------------------------------------------------
    def timeline(self, *, conditions: dict[str, Any] | None = None,
                 locator_id: str | None = None) -> dict[str, Any]:
        cond = self._resolve_locator(conditions, locator_id)
        buckets: dict[str, list[dict[str, Any]]] = {}
        for key in self._all_series():
            if not self._match_series(key, cond):
                continue
            item = self._series_item(key)
            month = (item["effective"]["acquired_at"] if item["effective"] else "未知")[:7]
            buckets.setdefault(month if month else "未知", []).append(item)
        months = [
            {"month": month, "series_count": len(month_items),
             "items": month_items}
            for month, month_items in sorted(buckets.items(), reverse=True)
        ]
        return {"months": months, "conditions": cond, "locator_id": locator_id}

    def map_view(self, *, conditions: dict[str, Any] | None = None,
                 locator_id: str | None = None) -> dict[str, Any]:
        cond = self._resolve_locator(conditions, locator_id)
        matched = set(self._matched_series(cond))
        features = []
        for key in self._all_series():
            if key not in matched:
                continue
            item = self._series_item(key)
            image = item["effective"]
            if not image:
                continue
            flags = []
            if item["multi_version"]:
                flags.append("multi_version")
            if item["resolution_gap"]:
                flags.append("resolution_gap")
            if item["undispatched_count"]:
                flags.append("undispatched")
            features.append({
                "series_key": key,
                "image_id": image["id"],
                "footprint": image["footprint"],
                "resolution": image["resolution"],
                "flags": flags,
            })
        parcels_geojson = [
            {"id": p["id"], "series_key": p["series_key"], "code": p["code"],
             "footprint": p["footprint"], "status": p["status"]}
            for p in self._parcels()
            if p["series_key"] in matched
        ]
        return {
            "grid_size": GRID_SIZE,
            "gap_threshold_meters": GAP_THRESHOLD_METERS,
            "features": features,
            "parcels": parcels_geojson,
            "conditions": cond,
            "locator_id": locator_id,
        }

    # ------------------------------------------------------------------
    # 图斑待办
    # ------------------------------------------------------------------
    def list_parcels(
        self,
        *,
        status: str | None = None,
        series_key: str | None = None,
        conditions: dict[str, Any] | None = None,
        locator_id: str | None = None,
        cursor_id: int | None = None,
        size: int = 20,
    ) -> dict[str, Any]:
        cond = self._resolve_locator(conditions, locator_id)
        rows = list(self._parcels())
        if status:
            rows = [p for p in rows if p["status"] == status]
        if series_key:
            rows = [p for p in rows if p["series_key"] == series_key]
        # 与定位条件联动：只保留命中定位影像的图斑。
        if cond:
            matched = {key for key in self._all_series() if self._match_series(key, cond)}
            rows = [p for p in rows if p["series_key"] in matched]
        rows.sort(key=lambda p: p["id"])
        if cursor_id is not None:
            rows = [p for p in rows if p["id"] > cursor_id]
        page = rows[:size]
        next_id = page[-1]["id"] if page and len(rows) > size else None
        return {
            "items": [dict(p) for p in page],
            "size": size,
            "next_cursor_id": next_id,
            "has_more": next_id is not None,
            "conditions": cond,
            "locator_id": locator_id,
        }

    # ------------------------------------------------------------------
    # 定位上下文：保存后供清单 / 图斑 / 汇总互相关联
    # ------------------------------------------------------------------
    def save_locator(self, conditions: dict[str, Any]) -> dict[str, Any]:
        locator_id = uuid.uuid4().hex[:12]
        clean = {k: v for k, v in (conditions or {}).items() if v not in (None, "", [])}
        self._locators[locator_id] = clean
        return {"locator_id": locator_id, "conditions": clean}

    # ------------------------------------------------------------------
    # 质量审核 / 新版本登记 / 删除：维护“有效版本”口径
    # ------------------------------------------------------------------
    def review_image(self, image_id: int, result: str, note: str | None) -> tuple[dict[str, Any] | None, str]:
        image = next((img for img in self._images() if img["id"] == image_id), None)
        if image is None:
            return None, f"影像 {image_id} 不存在"
        if result not in (REVIEW_PASSED, REVIEW_REJECTED):
            return None, f"审核结论「{result}」不支持，仅支持{REVIEW_PASSED}/{REVIEW_REJECTED}"
        image["review_status"] = result
        image["review_note"] = note or ""
        # 新通过的版本自动成为同源有效版本，同系列其他通过版本转为追溯。
        if result == REVIEW_PASSED:
            for other in self._versions_of(image["series_key"]):
                if other is not image and other["review_status"] == REVIEW_PASSED:
                    other["superseded"] = True
        versions = self._versions_of(image["series_key"])
        return self._public_image(image, versions), "质量审核已记录"

    def register_version(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        required = ["series_key", "source", "resolution", "acquired_at"]
        missing = [name for name in required if not str(values.get(name) or "").strip()]
        if missing:
            return None, missing
        series_key = str(values["series_key"]).strip()
        versions = self._versions_of(series_key)
        acquired_at = str(values["acquired_at"]).strip()
        if versions and acquired_at <= versions[-1]["acquired_at"]:
            return None, ["获取日期需晚于同源最新版本，旧版请走追溯档"]
        rows = self._images()
        image = {
            "id": _new_id(rows),
            "series_key": series_key,
            "data_code": str(values.get("data_code") or f"{series_key}-IMG{len(versions) + 1:02d}"),
            "source": str(values["source"]).strip(),
            "resolution": str(values["resolution"]).strip(),
            "acquired_at": acquired_at,
            "footprint": values.get("footprint") or (versions[-1]["footprint"] if versions else None),
            "review_status": REVIEW_PENDING,
            "review_note": "",
            "superseded": False,
            "archive_path": f"/archive/remote/{series_key}/{acquired_at[:7]}",
        }
        rows.append(image)
        return self._public_image(image, self._versions_of(series_key)), []

    def delete_image(self, image_id: int) -> tuple[bool, str]:
        rows = self._images()
        image = next((img for img in rows if img["id"] == image_id), None)
        if image is None:
            return False, f"影像 {image_id} 不存在"
        if self._effective(image["series_key"]) is image:
            return False, "有效版本不允许删除；请先审核通过新版本，旧版将自动转入追溯"
        rows.remove(image)
        return True, "影像已删除；同源有效版本不变，既有浏览游标仍可继续翻页"

    # ------------------------------------------------------------------
    # 集中派发：幂等键 + 最新结论冲突合并 + 失败维持上下文
    # ------------------------------------------------------------------
    def dispatch(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        idem_key = str(payload.get("idempotency_key") or "").strip()
        if not idem_key:
            return None, "缺少幂等键 idempotency_key"
        if idem_key in self._dispatch_cache:
            cached = dict(self._dispatch_cache[idem_key])
            cached["replayed"] = True
            return cached, None

        targets = payload.get("parcel_ids") or []
        if not isinstance(targets, list) or not targets:
            return None, "缺少待派发图斑 parcel_ids"
        assignee = str(payload.get("assignee") or "").strip()
        if not assignee:
            return None, "缺少派发对象 assignee"
        parcel_rows = self._parcels()
        missing = [pid for pid in targets if not any(p["id"] == pid for p in parcel_rows)]
        if missing:
            return None, f"图斑不存在：{', '.join(str(pid) for pid in missing)}"

        now = datetime.utcnow().isoformat(timespec="seconds")
        concluded_at = str(payload.get("concluded_at") or now)
        # 先在影子副本里算结论，全部通过再提交，失败时维持原状态。
        shadow = {p["id"]: dict(p) for p in parcel_rows}
        dispatched, conflicts = [], []
        for pid in targets:
            parcel = shadow[pid]
            conflict = None
            if parcel.get("last_conclusion") and str(parcel.get("concluded_at", "")) > concluded_at:
                # 已有更新的解译结论：冲突，以最新一次（已有结论）为准。
                conflict = {
                    "parcel_id": pid,
                    "kept_conclusion": parcel["last_conclusion"],
                    "kept_at": parcel["concluded_at"],
                    "ignored_conclusion": payload.get("conclusion", ""),
                }
                conflicts.append(conflict)
                continue
            if payload.get("conclusion"):
                parcel["last_conclusion"] = str(payload["conclusion"])
                parcel["concluded_at"] = concluded_at
            parcel["status"] = PARCEL_DISPATCHED
            parcel["assignee"] = assignee
            parcel["dispatch_batch"] = idem_key
            parcel["dispatched_at"] = now
            dispatched.append(pid)

        # 提交影子副本。
        for pid in targets:
            if pid in dispatched:
                parcel_rows[next(i for i, p in enumerate(parcel_rows) if p["id"] == pid)] = shadow[pid]

        result = {
            "ok": True,
            "replayed": False,
            "idempotency_key": idem_key,
            "dispatched_parcel_ids": dispatched,
            "conflicts": conflicts,
            "message": f"已派发 {len(dispatched)} 个图斑"
                       + (f"，{len(conflicts)} 个冲突按最新结论保留" if conflicts else ""),
        }
        self._dispatch_cache[idem_key] = dict(result)
        return result, None

    # ------------------------------------------------------------------
    # 汇总页：遥感清单 / 图斑待办 / 存档目录，全部可与定位条件互相关联
    # ------------------------------------------------------------------
    def _matched_series(self, cond: dict[str, Any]) -> list[str]:
        return [key for key in self._all_series() if self._match_series(key, cond)]

    def summary(self, *, conditions: dict[str, Any] | None = None,
                locator_id: str | None = None) -> dict[str, Any]:
        cond = self._resolve_locator(conditions, locator_id)
        keys = self._matched_series(cond)
        items = [self._series_item(key) for key in keys]

        list_groups = [
            {"key": "all", "label": "遥感清单合计", "count": len(items)},
            {"key": "multi_version", "label": "同源多版本",
             "count": sum(1 for i in items if i["multi_version"])},
            {"key": "resolution_gap", "label": "分辨率缺口",
             "count": sum(1 for i in items if i["resolution_gap"])},
            {"key": "undispatched", "label": "含未派发图斑",
             "count": sum(1 for i in items if i["undispatched_count"] > 0)},
            {"key": "passed", "label": "有效版本审核通过",
             "count": sum(1 for i in items if i["effective_passed"])},
        ]

        parcels = [p for p in self._parcels() if p["series_key"] in set(keys)]
        todo_groups = [
            {"key": PARCEL_PENDING, "label": "待派发",
             "count": sum(1 for p in parcels if p["status"] == PARCEL_PENDING)},
            {"key": PARCEL_DISPATCHED, "label": "已派发",
             "count": sum(1 for p in parcels if p["status"] == PARCEL_DISPATCHED)},
            {"key": PARCEL_DONE, "label": "已完成",
             "count": sum(1 for p in parcels if p["status"] == PARCEL_DONE)},
        ]

        archive: dict[str, dict[str, Any]] = {}
        for key in keys:
            versions = self._versions_of(key)
            for image in versions:
                bucket = archive.setdefault(image["archive_path"], {
                    "archive_path": image["archive_path"], "series_keys": set(),
                    "versions": 0, "effective": 0, "trace_only": 0})
                bucket["series_keys"].add(key)
                bucket["versions"] += 1
                if self._effective(key) is image:
                    bucket["effective"] += 1
                else:
                    bucket["trace_only"] += 1
        archive_groups = [
            {"archive_path": path, "series_count": len(b["series_keys"]),
             "versions": b["versions"], "effective": b["effective"],
             "trace_only": b["trace_only"]}
            for path, b in sorted(archive.items())
        ]
        return {
            "locator_id": locator_id,
            "conditions": cond,
            "remote_list": {"groups": list_groups, "items": items},
            "parcel_todo": {"groups": todo_groups},
            "archive_catalog": {"groups": archive_groups},
        }
