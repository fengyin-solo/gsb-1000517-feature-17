"""影像核查队列业务规则。

设计要点：
- 影像以 source_key 归为同源，同一条源只允许一个“当前有效”版本，候选必须是质量审核
  通过的版本中版本号最高的一版；旧版与驳回版只保留追溯（trace_only），不做物理删除。
- 索引按 (获取时间倒序, id 倒序) 稳定排序，每次增删改产生一个不可变版本快照（seq）。
  浏览游标内嵌快照 seq 与过滤条件签名：即使在翻页期间发生替换/删除，后续页仍从旧快照
  取 id 列表，再用最新行内容序列化，保证不重复、不漏项。
- locate 产生的定位条件以 context token 串联，新定位与此前条件取交集，清单/图斑/存档
  三处汇总都复用同一套条件。
- 集中派发携带幂等键：同键重放只回放首次结果；图斑已存在更新的解译结论时保留最新结论；
  整体校验不通过时不写入任何状态，调用方此前的定位上下文原样返回。
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.store import store

IMAGE_MODULE = "remote"
POLYGON_MODULE = "remote_polygon"

QUALITY_PENDING = "待审核"
QUALITY_PASSED = "审核通过"
QUALITY_REJECTED = "审核驳回"

STATUS_CURRENT = "当前有效"
STATUS_REVIEW = "待质检"
STATUS_TRACE = "仅追溯"
STATUS_DELETED = "已删除"

DEFAULT_SCALE = 2.0
MAX_PAGE_SIZE = 200

# 参与过滤/快照签名的条件键；分页参数不在其列。
CONDITION_KEYS = (
    "keyword", "status", "source_key", "quality",
    "date_from", "date_to", "scale", "bbox",
    "only_gap", "include_trace", "focus_images", "focus_polygons",
)
STATIC_FIELDS = (
    "id", "status", "pending", "abnormal", "数据编号", "数据源", "分辨率", "覆盖面积",
    "获取日期", "解译内容", "解译人员", "数据状态", "source_key", "version", "quality",
    "is_current", "trace_only", "tombstoned", "resolution_m", "acquired_at",
    "footprint", "archive_code", "review_note",
)


def _to_ts(date_text: Any) -> int:
    """把 'YYYY-MM-DD' 收成 UTC 零点时间戳；无法解析时退到当前时间之前，避免排序报错。"""
    try:
        return int(datetime.strptime(str(date_text), "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return 0


def _parse_resolution(text: Any) -> float | None:
    """'0.8m' / '2米' 这类分辨率文案统一抽出数值（米）。"""
    digits = ""
    for ch in str(text or ""):
        if ch.isdigit() or ch == ".":
            digits += ch
        elif digits:
            break
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def _bbox_intersects(footprint: list[list[float]] | None, bbox: list[float]) -> bool:
    if not footprint:
        return False
    xs = [point[0] for point in footprint]
    ys = [point[1] for point in footprint]
    return not (max(xs) < bbox[0] or min(xs) > bbox[2] or max(ys) < bbox[1] or min(ys) > bbox[3])


class RemoteService:
    def __init__(self) -> None:
        self._index_seq = 0
        # 派生态（图斑/分辨率缺口）版本：只随图斑结论、派发这类真实写入推进，
        # 索引重建（含初始化）不推进，避免与快照版本错位
        self._derived_seq = 0
        # seq -> 有序的不可变行副本（不含已下线影像），翻页期间始终可读
        self._bases: dict[int, list[dict[str, Any]]] = {}
        # "索引seq:派生seq:条件签名" -> 该快照下命中的有序 id 列表
        self._filtered: dict[str, list[int]] = {}
        self._contexts: dict[str, dict[str, Any]] = {}
        self._dispatch_keys: dict[str, dict[str, Any]] = {}
        # 初始化只建索引快照，不推进派生版本
        self._bump(index=True, bump_derived=False)

    # ------------------------------------------------------------------ 索引与快照

    @property
    def index_version(self) -> int:
        return self._index_seq

    def _bump(self, *, index: bool = False, bump_derived: bool = True) -> None:
        """提交写入：索引变更推进快照 seq；派生态变更推进派生 seq，二者互不干扰。"""
        if index:
            self._index_seq += 1
            snapshot: list[dict[str, Any]] = []
            for row in self._images():
                if row.get("tombstoned"):
                    continue
                snapshot.append({field: _copy_value(row.get(field)) for field in STATIC_FIELDS})
            snapshot.sort(key=lambda row: (int(row.get("acquired_at") or 0), int(row.get("id") or 0)), reverse=True)
            self._bases[self._index_seq] = snapshot
        if bump_derived:
            self._derived_seq += 1

    def _rebuild_index(self) -> int:
        """增删改后提交一个新的不可变快照；行对象之后的原地修改不影响历史快照。"""
        self._bump(index=True)
        return self._index_seq

    def _images(self) -> list[dict[str, Any]]:
        return store.rows(IMAGE_MODULE)

    def _polygons(self) -> list[dict[str, Any]]:
        return store.rows(POLYGON_MODULE)

    def _snapshot(self, seq: int) -> tuple[list[dict[str, Any]], bool]:
        """取指定 seq 的快照；过期 seq 已被裁剪时退回当前快照并标记 stale。"""
        base = self._bases.get(seq)
        if base is not None:
            return base, False
        return self._bases[self._index_seq], True

    def _polygon_stats(self) -> dict[int, dict[str, float]]:
        stats: dict[int, dict[str, float]] = {}
        for polygon in self._polygons():
            image_id = int(polygon.get("image_id") or 0)
            bucket = stats.setdefault(image_id, {"total": 0, "pending": 0, "gap": 0})
            bucket["total"] += 1
            if not polygon.get("assigned"):
                bucket["pending"] += 1
            image = self._find_image(image_id)
            image_resolution = float(image.get("resolution_m") or 0.0) if image else 0.0
            required = float(polygon.get("required_resolution_m") or 0.0)
            if image_resolution and required and required < image_resolution:
                bucket["gap"] += 1
        return stats

    def _enrich(self, row: dict[str, Any], conditions: dict[str, Any], stats: dict[int, dict[str, float]]) -> dict[str, Any]:
        """挂接派生标记：同源版本数、分辨率缺口、未派单图斑。派生态实时计算，不进快照。"""
        image = dict(row)
        image_id = int(image.get("id") or 0)
        siblings = [r for r in self._images() if r.get("source_key") == image.get("source_key") and not r.get("tombstoned")]
        bucket = stats.get(image_id, {"total": 0, "pending": 0, "gap": 0})
        scale = float(conditions.get("scale") or DEFAULT_SCALE)
        reasons: list[str] = []
        resolution_m = image.get("resolution_m")
        if image.get("is_current") and isinstance(resolution_m, (int, float)) and resolution_m > scale:
            reasons.append(f"当前有效影像分辨率 {resolution_m:g}m，粗于解译尺度 {scale:g}m")
        if bucket["gap"]:
            reasons.append(f"{int(bucket['gap'])} 个图斑要求更优分辨率，当前影像无法支撑")
        image["version_count"] = len(siblings)
        image["multi_version"] = len(siblings) > 1
        image["polygon_total"] = int(bucket["total"])
        image["polygon_pending"] = int(bucket["pending"])
        image["polygon_gap"] = int(bucket["gap"])
        image["pending_dispatch"] = bucket["pending"] > 0
        image["resolution_gap"] = bool(reasons)
        image["gap_reasons"] = reasons
        return image

    # ------------------------------------------------------------------ 条件与游标

    @staticmethod
    def _clean_conditions(raw: dict[str, Any]) -> dict[str, Any]:
        conditions: dict[str, Any] = {}
        for key in CONDITION_KEYS:
            value = raw.get(key)
            if value in (None, "", [], False):
                continue
            if key in ("only_gap", "include_trace"):
                conditions[key] = bool(value)
            elif key in ("focus_images", "focus_polygons"):
                conditions[key] = sorted({int(v) for v in value})
            elif key == "scale":
                conditions[key] = float(value)
            elif key == "bbox":
                # HTTP 经 Pydantic 会得到 float，统一成 int 坐标，避免条件签名因 int/float 分裂
                conditions[key] = [int(v) if float(v).is_integer() else float(v) for v in value]
            else:
                conditions[key] = value
        return conditions

    @staticmethod
    def _signature(conditions: dict[str, Any]) -> str:
        return json.dumps(conditions, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def encode_cursor(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def decode_cursor(token: str) -> dict[str, Any] | None:
        try:
            padded = token + "=" * (-len(token) % 4)
            return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        except Exception:
            return None

    def _resolve_context(self, context_token: str | None) -> dict[str, Any]:
        if not context_token:
            return {}
        saved = self._contexts.get(context_token)
        return dict(saved) if saved else {}

    def _conditions_from_params(self, params: dict[str, Any], context_token: str | None) -> dict[str, Any]:
        """query 参数覆盖 context 中保存的定位条件；分页参数与布尔默认值不覆盖。

        路由给 only_gap/include_trace 的默认值是 False，若直接覆盖会把上下文里此前
        定位的 True 冲掉，因此假值一律视为“本次未显式提供”，保留此前定位条件。
        """
        conditions = self._resolve_context(context_token)
        for key in CONDITION_KEYS:
            if key not in params:
                continue
            value = params[key]
            if value in (None, ""):
                continue
            if key in ("only_gap", "include_trace") and not value:
                continue
            conditions[key] = value
        return self._clean_conditions(conditions)

    def _match_row(self, row: dict[str, Any], conditions: dict[str, Any]) -> bool:
        keyword = conditions.get("keyword")
        if keyword:
            haystack = " ".join(str(row.get(k, "")) for k in ("数据编号", "数据源", "解译内容", "archive_code"))
            if keyword not in haystack:
                return False
        if conditions.get("status") and row.get("status") != conditions["status"]:
            return False
        if conditions.get("source_key") and row.get("source_key") != conditions["source_key"]:
            return False
        if conditions.get("quality") and row.get("quality") != conditions["quality"]:
            return False
        date_text = str(row.get("获取日期") or "")
        if conditions.get("date_from") and date_text < str(conditions["date_from"]):
            return False
        if conditions.get("date_to") and date_text > str(conditions["date_to"]):
            return False
        if not conditions.get("include_trace") and row.get("trace_only"):
            return False
        bbox = conditions.get("bbox")
        if bbox and not _bbox_intersects(row.get("footprint"), bbox):
            return False
        focus_images = conditions.get("focus_images")
        if focus_images and int(row.get("id") or 0) not in focus_images:
            return False
        if conditions.get("only_gap"):
            # gap 是派生态，基于静态字段快速预判，精确结果在 enrich 后再次过滤
            scale = float(conditions.get("scale") or DEFAULT_SCALE)
            resolution_m = row.get("resolution_m")
            current_gap = self._polygon_stats().get(int(row.get("id") or 0), {}).get("gap", 0)
            coarse = bool(row.get("is_current") and isinstance(resolution_m, (int, float)) and resolution_m > scale)
            if not coarse and not current_gap:
                return False
        return True

    def _filtered_ids(self, conditions: dict[str, Any], seq: int, derived_seq: int) -> list[int]:
        base, _stale = self._snapshot(seq)
        cache_key = f"{seq}:{derived_seq}:{self._signature(conditions)}"
        cached = self._filtered.get(cache_key)
        if cached is not None:
            return cached
        ids = [int(row["id"]) for row in base if self._match_row(row, conditions)]
        self._filtered[cache_key] = ids
        return ids

    def query_page(
        self,
        params: dict[str, Any],
        *,
        context_token: str | None = None,
        cursor_token: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> dict[str, Any]:
        conditions = self._conditions_from_params(params, context_token)
        stats = self._polygon_stats()

        if cursor_token:
            decoded = self.decode_cursor(cursor_token)
            if not decoded:
                raise ValueError("浏览游标已损坏，请从第一页重新进入")
            # 游标内嵌的条件签名优先：保证沿着此前的定位条件继续翻页
            embedded = decoded.get("conditions") or {}
            if embedded and self._signature(embedded) != self._signature(conditions):
                conditions = self._clean_conditions(embedded)
            seq = int(decoded.get("seq", self._index_seq))
            derived_seq = int(decoded.get("derived_seq", self._derived_seq))
            after_id = decoded.get("after_id")
        else:
            seq = self._index_seq
            derived_seq = self._derived_seq
            after_id = None

        ids = self._filtered_ids(conditions, seq, derived_seq)
        if after_id is not None and after_id in ids:
            start = ids.index(int(after_id)) + 1
        elif cursor_token:
            start = 0  # 游标指向的记录已不在快照命中集（条件变化）时从头开始，不重不漏靠 id 去重保证
        else:
            start = max(page - 1, 0) * size

        page_ids = ids[start:start + size]
        items = []
        for image_id in page_ids:
            row = self._find_image(image_id)
            if row is None:
                continue
            items.append(self._enrich(row, conditions, stats))

        next_cursor = None
        if start + size < len(ids) and page_ids:
            next_cursor = self.encode_cursor({
                "seq": seq,
                "derived_seq": derived_seq,
                "after_id": page_ids[-1],
                "conditions": conditions,
            })
        _base, stale = self._snapshot(seq)
        return {
            "items": items,
            "total": len(ids),
            "page": page,
            "size": size,
            "next_cursor": next_cursor,
            "index_version": seq,
            "derived_version": derived_seq,
            "snapshot": True,
            "stale": stale,
            "conditions": conditions,
            "context": context_token,
        }

    # ------------------------------------------------------------------ 同源版本治理

    def _find_image(self, image_id: int) -> dict[str, Any] | None:
        return store.find(IMAGE_MODULE, image_id)

    def _recompute_current(self, source_key: str) -> None:
        """审核通过的版本中（版本号、id）最高者为当前有效，其余通过版降为仅追溯。

        同源图斑统一挂靠到当前有效版本：核查队列只对审核通过的版本派发，旧版仅追溯。
        """
        rows = [r for r in self._images() if r.get("source_key") == source_key and not r.get("tombstoned")]
        approved = [r for r in rows if r.get("quality") == QUALITY_PASSED]
        current = max(approved, key=lambda r: (int(r.get("version") or 0), int(r.get("id", 0))), default=None)
        current_id = int(current["id"]) if current is not None else 0
        for row in rows:
            if int(row.get("id", 0)) == current_id:
                row["is_current"] = True
                row["trace_only"] = False
                row["status"] = STATUS_CURRENT
                row["数据状态"] = STATUS_CURRENT
            elif row.get("quality") == QUALITY_PASSED:
                row["is_current"] = False
                row["trace_only"] = True
                if row.get("status") != STATUS_DELETED:
                    row["status"] = STATUS_TRACE
                    row["数据状态"] = STATUS_TRACE
        if current is not None:
            for polygon in self._polygons():
                owner = self._find_image(int(polygon.get("image_id") or 0))
                if owner is not None and owner.get("source_key") == source_key and int(polygon["image_id"]) != current_id:
                    polygon["image_id"] = current_id
                    polygon["image_no"] = current.get("数据编号")

    def register_image(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        required = ["数据编号", "数据源", "分辨率", "获取日期"]
        missing = [field for field in required if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = self._images()
        source_key = str(values.get("source_key") or "").strip() or _derive_source_key(str(values["数据编号"]))
        if any(r.get("数据编号") == values["数据编号"] and not r.get("tombstoned") for r in rows):
            raise ValueError(f"数据编号 {values['数据编号']} 已存在，不能重复登记")
        same_source = [r for r in rows if r.get("source_key") == source_key]
        inherited_archive = next((str(r.get("archive_code")) for r in same_source if r.get("archive_code")), "")
        quality = str(values.get("quality") or QUALITY_PENDING).strip()
        entry = {"id": max((int(r.get("id", 0)) for r in rows), default=0) + 1}
        entry.update(_pick_display_fields(values))
        entry.update({
            "source_key": source_key,
            "version": int(values.get("version") or (max((int(r.get("version") or 0) for r in same_source), default=-1) + 1)),
            "quality": quality,
            "is_current": False,
            "trace_only": False,
            "tombstoned": False,
            "resolution_m": values.get("resolution_m") or _parse_resolution(values.get("分辨率")),
            "acquired_at": values.get("acquired_at") or _to_ts(values.get("获取日期")),
            "footprint": values.get("footprint") or [],
            "archive_code": str(values.get("archive_code") or inherited_archive or f"ARC-{source_key}"),
            "review_note": str(values.get("review_note")
                               or ("质量审核通过，登记即作为当前有效版本" if quality == QUALITY_PASSED else "新登记，待质量审核")),
        })
        entry["status"] = STATUS_CURRENT if quality == QUALITY_PASSED else STATUS_REVIEW
        entry["数据状态"] = entry["status"]
        entry["pending"] = entry["status"] != STATUS_CURRENT
        entry["abnormal"] = False
        rows.append(entry)
        if quality == QUALITY_PASSED:
            self._recompute_current(source_key)
        self._rebuild_index()
        return entry, []

    def replace_image(self, image_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """以旧版为底提交一个新版本进入待质检；通过前旧版继续作为当前有效版本。"""
        old = self._find_image(image_id)
        if old is None:
            return None, f"遥感数据 {image_id} 不存在或已下线"
        rows = self._images()
        same_source = [r for r in rows if r.get("source_key") == old.get("source_key")]
        today = datetime.now(timezone.utc).date().isoformat()
        new_values = {
            "数据编号": f"{old['source_key']}-V{max((int(r.get('version') or 0) for r in same_source), default=0) + 1}",
            "数据源": values.get("数据源") or old.get("数据源"),
            "分辨率": values.get("分辨率") or old.get("分辨率"),
            "覆盖面积": values.get("覆盖面积") or old.get("覆盖面积"),
            "获取日期": values.get("获取日期") or today,
            "解译内容": values.get("解译内容") or old.get("解译内容"),
            "解译人员": values.get("解译人员") or old.get("解译人员"),
            "source_key": old["source_key"],
            "archive_code": old.get("archive_code"),
            "footprint": values.get("footprint") or old.get("footprint"),
            "review_note": str(values.get("review_note") or f"替换 {old.get('数据编号')} 的新版本，待质量审核"),
        }
        entry, missing = self.register_image(new_values)
        if entry is None:
            return None, f"替换版本登记失败，缺少字段：{'、'.join(missing)}"
        return entry, f"已基于 {old.get('数据编号')} 生成待质检新版本 {entry['数据编号']}，审核通过前仍沿用旧版"

    def review_image(self, image_id: int, action: str, note: str | None = None) -> tuple[dict[str, Any] | None, str]:
        row = self._find_image(image_id)
        if row is None:
            return None, f"遥感数据 {image_id} 不存在或已下线"
        if row.get("quality") != QUALITY_PENDING:
            return None, f"数据编号 {row.get('数据编号')} 已完成质量审核（{row.get('quality')}），不能重复审核"
        if action == "审核通过":
            row["quality"] = QUALITY_PASSED
            row["review_note"] = note or "质量审核通过"
            self._recompute_current(str(row["source_key"]))
            message = f"{row['数据编号']} 审核通过，已成为同源当前有效版本，旧版转为仅追溯"
        elif action == "审核驳回":
            row["quality"] = QUALITY_REJECTED
            row["trace_only"] = True
            row["is_current"] = False
            row["status"] = STATUS_TRACE
            row["数据状态"] = STATUS_TRACE
            row["pending"] = False
            row["abnormal"] = True
            row["review_note"] = note or "质量审核驳回，仅保留追溯"
            message = f"{row['数据编号']} 审核驳回，只保留追溯，不进入核查队列"
        else:
            return None, f"动作「{action}」不属于质量审核范围"
        self._rebuild_index()
        return row, message

    def delete_image(self, image_id: int) -> tuple[dict[str, Any] | None, str]:
        """下线影像：立墓碑而不是物理删除，存档目录仍可追溯。"""
        row = self._find_image(image_id)
        if row is None:
            return None, f"遥感数据 {image_id} 不存在或已下线"
        was_current = bool(row.get("is_current"))
        row["tombstoned"] = True
        row["is_current"] = False
        row["trace_only"] = True
        row["status"] = STATUS_DELETED
        row["数据状态"] = STATUS_DELETED
        row["pending"] = False
        row["review_note"] = f"已从核查队列下线：{row.get('review_note') or ''}"
        if was_current:
            self._recompute_current(str(row["source_key"]))
        self._rebuild_index()
        return row, f"{row['数据编号']} 已下线并立墓碑，历史游标仍可遍历，存档目录保留追溯条目"

    # ------------------------------------------------------------------ 定位联动

    def locate(self, params: dict[str, Any], context_token: str | None) -> dict[str, Any]:
        """提交一次定位：与此前条件取交集，焦点影像/图斑取并集，返回串联后的上下文。"""
        base = self._resolve_context(context_token)
        merged = dict(base)
        trail: list[str] = list(base.get("trail", [])) if base else []
        for key in CONDITION_KEYS:
            value = params.get(key)
            if value in (None, "", []):
                continue
            if key in ("focus_images", "focus_polygons"):
                merged[key] = sorted({*(merged.get(key) or []), *[int(v) for v in value]})
            else:
                merged[key] = value

        if params.get("polygon_id"):
            merged.setdefault("focus_polygons", [])
            merged["focus_polygons"] = sorted({*merged["focus_polygons"], int(params["polygon_id"])})
        if params.get("image_id"):
            merged.setdefault("focus_images", [])
            merged["focus_images"] = sorted({*merged["focus_images"], int(params["image_id"])})

        # 图斑焦点扩展到所属影像，保证地图/时间轴与清单能互相定位
        for polygon in self._polygons():
            if int(polygon.get("id") or 0) in (merged.get("focus_polygons") or []):
                merged.setdefault("focus_images", [])
                merged["focus_images"] = sorted({*merged["focus_images"], int(polygon.get("image_id") or 0)})

        conditions = self._clean_conditions(merged)
        conditions_for_store = {k: v for k, v in conditions.items() if k != "trail"}
        ids = self._filtered_ids(conditions_for_store, self._index_seq, self._derived_seq)
        # 图斑必须跟随定位命中的影像范围；显式焦点图斑再收窄
        polygon_ids = [
            int(p["id"]) for p in self._polygons()
            if int(p.get("image_id") or 0) in ids
        ]
        if conditions.get("focus_polygons"):
            polygon_ids = [pid for pid in polygon_ids if pid in conditions["focus_polygons"]]

        step = len(trail) + 1
        trail.append(_describe_locate(conditions, step))
        token = uuid.uuid4().hex
        stored = dict(conditions_for_store)
        stored["trail"] = trail
        self._contexts[token] = stored
        return {
            "context": token,
            "conditions": conditions_for_store,
            "trail": trail,
            "matched": {"images": len(ids), "polygons": len(polygon_ids)},
            "focus": {"images": conditions_for_store.get("focus_images", []), "polygons": polygon_ids},
        }

    def workspace(self, params: dict[str, Any], context_token: str | None) -> dict[str, Any]:
        """供时间轴与地图联动的一份数据：影像足迹 + 图斑 + 同源分组 + 缺口标记。"""
        conditions = self._conditions_from_params(params, context_token)
        stats = self._polygon_stats()
        ids = self._filtered_ids(conditions, self._index_seq, self._derived_seq)
        images = [self._enrich(self._find_image(image_id), conditions, stats) for image_id in ids if self._find_image(image_id)]
        image_ids = {int(image["id"]) for image in images}
        polygons = [self._serialize_polygon(p) for p in self._polygons() if int(p.get("image_id") or 0) in image_ids]
        sources = self._source_groups(conditions)
        return {
            "conditions": conditions,
            "context": context_token,
            "index_version": self._index_seq,
            "images": images,
            "polygons": polygons,
            "sources": sources,
            "world": {"width": 960, "height": 900},
        }

    # ------------------------------------------------------------------ 图斑与派发

    def list_polygons(self, params: dict[str, Any], context_token: str | None) -> dict[str, Any]:
        conditions = self._conditions_from_params(params, context_token)
        image_ids = set(self._filtered_ids(conditions, self._index_seq, self._derived_seq))
        rows = []
        for polygon in self._polygons():
            if image_ids and int(polygon.get("image_id") or 0) not in image_ids:
                continue
            if params.get("assigned") is True and not polygon.get("assigned"):
                continue
            if params.get("assigned") is False and polygon.get("assigned"):
                continue
            rows.append(self._serialize_polygon(polygon))
        return {"items": rows, "total": len(rows), "conditions": conditions, "context": context_token}

    def _serialize_polygon(self, polygon: dict[str, Any]) -> dict[str, Any]:
        item = dict(polygon)
        image = self._find_image(int(polygon.get("image_id") or 0))
        item["image_no"] = polygon.get("image_no") or (image or {}).get("数据编号")
        item["image_current"] = bool(image and image.get("is_current"))
        item["image_trace"] = bool(image and (image.get("trace_only") or image.get("tombstoned")))
        image_resolution = float((image or {}).get("resolution_m") or 0.0)
        required = float(polygon.get("required_resolution_m") or 0.0)
        item["resolution_gap"] = bool(image_resolution and required and required < image_resolution)
        item["pending_dispatch"] = not polygon.get("assigned")
        return item

    def record_conclusion(self, polygon_id: int, text: str) -> tuple[dict[str, Any] | None, str]:
        polygon = store.find(POLYGON_MODULE, polygon_id)
        if polygon is None:
            return None, f"图斑 {polygon_id} 不存在"
        if not text.strip():
            return None, "解译结论不能为空"
        polygon["conclusion"] = text.strip()
        polygon["conclusion_seq"] = int(polygon.get("conclusion_seq") or 0) + 1
        polygon["conclusion_at"] = int(time.time())
        polygon["status"] = "待复核"
        polygon["pending"] = True
        self._bump()
        return polygon, f"图斑 {polygon.get('polygon_no')} 已登记第 {polygon['conclusion_seq']} 版解译结论"

    def dispatch(self, values: dict[str, Any], context_token: str | None) -> dict[str, Any]:
        key = str(values.get("idempotency_key") or "").strip()
        if not key:
            return {"ok": False, "message": "集中派发必须携带幂等键", "context": context_token, "replayed": False}
        if key in self._dispatch_keys:
            stored = dict(self._dispatch_keys[key])
            stored["replayed"] = True
            stored["message"] = f"幂等键 {key} 已执行过，直接回放首次派发结果"
            stored["context"] = context_token
            return stored

        assignee = str(values.get("assignee") or "").strip()
        items = values.get("items") or []
        if not assignee:
            return {"ok": False, "message": "派发失败：缺少承办组", "context": context_token, "replayed": False}
        if not items:
            return {"ok": False, "message": "派发失败：未选择任何图斑", "context": context_token, "replayed": False}

        prepared: list[tuple[dict[str, Any], str, int]] = []
        for item in items:
            polygon_id = int(item.get("polygon_id") or item.get("id") or 0)
            polygon = store.find(POLYGON_MODULE, polygon_id)
            if polygon is None:
                # 硬错误：整批中止，不写任何状态，幂等键不占用，定位上下文原样带回
                return {"ok": False, "message": f"派发失败：图斑 {polygon_id} 不存在，已维持此前定位上下文", "context": context_token, "replayed": False}
            incoming_seq = int(item.get("seq") or 0)
            prepared.append((polygon, str(item.get("conclusion") or ""), incoming_seq))

        conflicts: list[dict[str, Any]] = []
        applied: list[dict[str, Any]] = []
        for polygon, conclusion, incoming_seq in prepared:
            existing_seq = int(polygon.get("conclusion_seq") or 0)
            if existing_seq > incoming_seq and polygon.get("conclusion"):
                # 图斑上已有更新的解译结论：以最新结论为准，跳过该图斑
                conflicts.append({
                    "polygon_id": int(polygon["id"]),
                    "polygon_no": polygon.get("polygon_no"),
                    "kept_seq": existing_seq,
                    "kept_conclusion": polygon.get("conclusion"),
                    "dropped_seq": incoming_seq,
                })
                continue
            polygon["assigned"] = True
            polygon["assignee"] = assignee
            polygon["dispatch_seq"] = int(polygon.get("dispatch_seq") or 0) + 1
            if conclusion:
                polygon["conclusion"] = conclusion
                polygon["conclusion_seq"] = max(existing_seq, incoming_seq) or (existing_seq + 1)
                polygon["conclusion_at"] = int(time.time())
            polygon["status"] = "已派发"
            polygon["pending"] = False
            applied.append(self._serialize_polygon(polygon))

        result = {
            "ok": True,
            "message": f"已派发 {len(applied)} 个图斑至{assignee}"
                       + (f"；{len(conflicts)} 个图斑存在更新的解译结论，已按最新结论保留" if conflicts else ""),
            "assigned": len(applied),
            "conflicts": conflicts,
            "items": applied,
            "idempotency_key": key,
            "replayed": False,
            "context": context_token,
        }
        self._dispatch_keys[key] = {k: v for k, v in result.items() if k != "context"}
        self._bump()
        return result

    # ------------------------------------------------------------------ 三处汇总

    def _source_groups(self, conditions: dict[str, Any]) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        scale = float(conditions.get("scale") or DEFAULT_SCALE)
        stats = self._polygon_stats()
        for row in self._images():
            if row.get("tombstoned"):
                continue
            group = groups.setdefault(str(row["source_key"]), {
                "source_key": row["source_key"], "数据源": row.get("数据源"),
                "versions": [], "archive_code": row.get("archive_code"),
            })
            group["versions"].append(self._enrich(row, conditions, stats))
        result = []
        for group in groups.values():
            versions = sorted(group["versions"], key=lambda r: int(r.get("version") or 0), reverse=True)
            current = next((r for r in versions if r.get("is_current")), None)
            pending_polygons = sum(int(r.get("polygon_pending") or 0) for r in versions)
            result.append({
                **group,
                "versions": versions,
                "current": current,
                "version_count": len(versions),
                "resolution_gap": bool(current and current.get("resolution_gap")),
                "gap_reasons": current.get("gap_reasons", []) if current else ["同源尚无审核通过版本，解译尺度无法保障"],
                "pending_polygons": pending_polygons,
                "scale": scale,
            })
        return sorted(result, key=lambda g: g["source_key"])

    def summary(self, params: dict[str, Any], context_token: str | None) -> dict[str, Any]:
        conditions = self._conditions_from_params(params, context_token)

        # 定位命中的当前队列影像：清单与图斑跟随它；存档在此基础上额外保留同源旧版与墓碑
        matched_images = set(self._filtered_ids(conditions, self._index_seq, self._derived_seq))
        matched_rows = [r for r in self._images() if int(r.get("id") or 0) in matched_images]
        matched_sources = {str(r.get("source_key")) for r in matched_rows}

        # 遥感清单：按同源影像汇总（命中影像的整条同源谱系都展示，旧版标注仅追溯）
        inventory = [g for g in self._source_groups(conditions) if g["source_key"] in matched_sources]

        # 图斑待办：未派发 + 待复核，标注分辨率缺口与所属影像是否当前版本
        todo = []
        for polygon in self._polygons():
            if matched_images and int(polygon.get("image_id") or 0) not in matched_images:
                continue
            serialized = self._serialize_polygon(polygon)
            if serialized["pending_dispatch"] or polygon.get("status") == "待复核":
                todo.append(serialized)

        # 存档目录：含已下线墓碑版本，按存档号汇总版本谱系
        archive: dict[str, dict[str, Any]] = {}
        for row in self._images():
            code = str(row.get("archive_code") or f"ARC-{row.get('source_key')}")
            archive.setdefault(code, {"archive_code": code, "sources": set(), "entries": []})
            archive[code]["sources"].add(str(row.get("source_key")))
            archive[code]["entries"].append({
                "id": row.get("id"),
                "数据编号": row.get("数据编号"),
                "version": row.get("version"),
                "quality": row.get("quality"),
                "status": row.get("status"),
                "is_current": row.get("is_current"),
                "trace_only": row.get("trace_only"),
                "tombstoned": row.get("tombstoned"),
                "分辨率": row.get("分辨率"),
                "获取日期": row.get("获取日期"),
                "review_note": row.get("review_note"),
            })
        archive_rows = []
        for code, group in archive.items():
            entries = sorted(group["entries"], key=lambda e: int(e.get("version") or 0), reverse=True)
            archive_rows.append({
                "archive_code": code,
                "sources": sorted(group["sources"]),
                "current_no": next((e["数据编号"] for e in entries if e.get("is_current")), None),
                "trace_count": sum(1 for e in entries if e.get("trace_only") and not e.get("tombstoned")),
                "deleted_count": sum(1 for e in entries if e.get("tombstoned")),
                "entries": entries,
            })
        # 存档跟随定位命中的同源范围；命中源的旧版/墓碑版本仍完整保留
        if matched_sources:
            archive_rows = [r for r in archive_rows if set(r["sources"]) & matched_sources]

        return {
            "conditions": conditions,
            "context": context_token,
            "index_version": self._index_seq,
            "inventory": inventory,
            "polygon_todo": todo,
            "archive": sorted(archive_rows, key=lambda r: r["archive_code"]),
            "counts": {
                "sources": len(inventory),
                "todo_polygons": len(todo),
                "unassigned": sum(1 for p in todo if p.get("pending_dispatch")),
                "resolution_gap_sources": sum(1 for g in inventory if g.get("resolution_gap")),
                "archive_volumes": len(archive_rows),
            },
        }


def _copy_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.loads(json.dumps(value, ensure_ascii=False))
    return value


def _derive_source_key(image_no: str) -> str:
    """RS-NW-A-V3 -> RS-NW-A；没有版本后缀时整体作为同源键。"""
    head, sep, tail = image_no.rpartition("-V")
    return head if sep and tail.isdigit() else image_no


def _pick_display_fields(values: dict[str, Any]) -> dict[str, Any]:
    return {
        field: values.get(field, "")
        for field in ("数据编号", "数据源", "分辨率", "覆盖面积", "获取日期", "解译内容", "解译人员")
    }


def _describe_locate(conditions: dict[str, Any], step: int) -> str:
    parts = []
    if conditions.get("source_key"):
        parts.append(f"同源影像 {conditions['source_key']}")
    if conditions.get("quality"):
        parts.append(f"质量={conditions['quality']}")
    if conditions.get("status"):
        parts.append(f"状态={conditions['status']}")
    if conditions.get("date_from") or conditions.get("date_to"):
        parts.append(f"时间 {conditions.get('date_from', '…')}~{conditions.get('date_to', '…')}")
    if conditions.get("bbox"):
        parts.append("地图框选范围")
    if conditions.get("only_gap"):
        parts.append("仅看分辨率缺口")
    if conditions.get("focus_images") or conditions.get("focus_polygons"):
        parts.append(f"焦点影像 {len(conditions.get('focus_images') or [])} 个/图斑 {len(conditions.get('focus_polygons') or [])} 个")
    if not parts:
        parts.append("全量队列")
    return f"第 {step} 次定位：" + "、".join(parts)
