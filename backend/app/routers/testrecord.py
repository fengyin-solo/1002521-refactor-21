"""联锁试验接口：维护试验记录，覆盖安排试验、登记合格、登记缺陷等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.testrecord import TestrecordService

router = APIRouter(prefix="/api/testrecord", tags=["联锁试验"])

service = TestrecordService()

LIST_FIELDS = ["试验编号", "试验日期", "试验车站", "试验项目", "试验人员", "试验结果", "遗留问题", "试验状态"]
STATUSES = ["待试验", "试验中", "试验合格", "存在缺陷"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按试验编号检索"),
    status: str | None = Query(default=None, description="待试验、试验中、试验合格、存在缺陷"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按试验编号与状态过滤联锁试验列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条试验记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"试验记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条试验记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="试验记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条试验记录执行安排试验（提交）、登记合格、登记缺陷。

    三个动作的合格/缺陷判定共用同一份口径（见 services.testrecord_rules），
    入口字段仍通过既有 payload.values 提交，接口参数不变。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出联锁试验清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "testrecord", "total": total, "items": items}
