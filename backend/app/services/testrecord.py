"""联锁试验业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "testrecord"
STATUS_ORDER = ["待试验", "试验中", "试验合格", "存在缺陷"]
ACTION_RULES = {"安排试验": "试验中", "登记合格": "试验合格", "登记缺陷": "存在缺陷"}
NEGATIVE_ACTIONS = []

# 联锁试验统一判定口径：提交、登记合格、登记缺陷三个入口共用这一份，
# 不再各自维护校验规则。试验项目、试验人员缺失时三个入口同样拦截；
# 遗留问题统一允许留空，不进入必填清单。
JUDGE_REQUIRED_FIELDS = ["试验编号", "试验日期", "试验车站", "试验项目", "试验人员"]
JUDGE_ACTIONS = ["登记合格", "登记缺陷"]
# 登记时落库的字段与列表口径一致，避免提交后字段丢失导致后续入口判定分叉。
ENTRY_FIELDS = JUDGE_REQUIRED_FIELDS + ["试验结果", "遗留问题", "试验状态"]


def judge_trial(record: dict[str, Any]) -> list[str]:
    """联锁试验统一判定：返回缺失的必填字段清单，空清单表示判定通过。

    历史记录缺键时按空值处理，只影响判定结论，不要求重写历史数据。
    """
    return [
        field
        for field in JUDGE_REQUIRED_FIELDS
        if not str(record.get(field) or "").strip()
    ]


class TestrecordService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("试验编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = judge_trial(values)
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in ENTRY_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"试验记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于联锁试验可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action in JUDGE_ACTIONS:
            missing = judge_trial(entry)
            if missing:
                return None, f"试验记录缺少必填字段：{'、'.join(missing)}，不能{action}"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"试验记录已{action}"
