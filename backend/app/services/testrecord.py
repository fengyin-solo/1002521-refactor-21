"""联锁试验业务规则：状态流转、字段校验与筛选口径都收在这里。

提交（安排试验）、登记合格、登记缺陷三个入口的判定统一委托给
``app.services.testrecord_rules``，本模块只负责存取与状态落库，
不再各自维护一份分叉的校验条件。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.testrecord_rules import (
    JUDGE_VERSION,
    REQUIRED_TEST_FIELDS,
    STATUS_ORDER,
    evaluate_record,
    normalize_record,
)

MODULE = "testrecord"
REQUIRED_FIELDS = ["试验编号", "试验日期", "试验车站"]
# 建档时可登记的试验明细字段；这些字段是否齐全由统一判定在三个入口里校验。
OPTIONAL_FIELDS = [*REQUIRED_TEST_FIELDS, "试验结果", "遗留问题", "试验状态"]
# 三个入口动作各自映射到唯一判定来源里的同名入口。
ACTION_ENTRANCES = {"安排试验": "提交", "登记合格": "登记合格", "登记缺陷": "登记缺陷"}
NEGATIVE_ACTIONS = []


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
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 一并保存建档时带来的试验明细；明细是否齐全由三个入口的统一判定校验。
        for field in OPTIONAL_FIELDS:
            if field in values:
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 新口径下建档的记录直接带判定版本标记；只有改造前的历史（种子）记录没有它。
        entry["判定版本"] = JUDGE_VERSION
        rows.append(entry)
        return entry, []

    # -- 三个业务入口：各自只取动作名，判定条件全部来自 evaluate_record ---------

    def submit_test(self, entry_id: int, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        """提交（安排试验）：试验项目与试验人员齐全才能进入试验中。"""
        return self._apply_verdict(entry_id, "安排试验", "提交", values)

    def register_pass(self, entry_id: int, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        """登记合格：走统一判定，合格结论由唯一判定来源给出。"""
        return self._apply_verdict(entry_id, "登记合格", "登记合格", values)

    def register_defect(self, entry_id: int, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        """登记缺陷：走统一判定，缺陷结论与必填的遗留问题都由唯一判定来源给出。"""
        return self._apply_verdict(entry_id, "登记缺陷", "登记缺陷", values)

    def run_action(self, entry_id: int, action: str, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        """动作分派入口：保持路由原有调用方式不变，内部分派到三个显式入口。"""
        dispatch = {
            "安排试验": self.submit_test,
            "登记合格": self.register_pass,
            "登记缺陷": self.register_defect,
        }
        handler = dispatch.get(action)
        if handler is None:
            return None, f"动作「{action}」不属于联锁试验可执行范围"
        return handler(entry_id, values)

    # -- 内部共用落库流程 -----------------------------------------------------

    def _apply_verdict(
        self,
        entry_id: int,
        action: str,
        entrance: str,
        values: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"试验记录 {entry_id} 不存在或已归档"

        # 本次入口补充上来的字段（如登记缺陷时填的遗留问题）与存量记录合并判定；
        # 先在归一化视图上判定，通过后才写回，拒绝时不改动记录。
        candidate = dict(entry)
        if values:
            for field in OPTIONAL_FIELDS:
                if field in values:
                    candidate[field] = values[field]
        candidate = normalize_record(candidate)

        verdict = evaluate_record(candidate, entrance=entrance)
        if not verdict.accepted:
            return None, verdict.reason

        target = verdict.status
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        if verdict.legacy:
            # 历史记录只按冻结结论回放状态，不补写字段、不打新版本标记，
            # 保证历史试验记录不被重写，下次仍按历史口径读出来。
            entry["status"] = target
        else:
            # 提交/登记入口补充上来的试验信息随判定通过一并落库，
            # 后续入口才能读到同一份试验项目、试验人员与遗留问题。
            for field in OPTIONAL_FIELDS:
                if field in candidate:
                    entry[field] = candidate[field]
            entry["status"] = target
            # 经过新口径判定的记录才打版本标记；历史记录没有该标记、不做补写重算。
            entry.setdefault("判定版本", JUDGE_VERSION)
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"试验记录已{action}"
