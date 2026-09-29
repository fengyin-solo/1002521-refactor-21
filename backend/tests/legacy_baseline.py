"""改造前联锁试验判定口径的快照（对照基准）。

收拢改造前，三个入口的校验各写一份且已经分叉：

- 提交（安排试验）入口：只做动作名与状态序列校验，试验项目/试验人员
  缺失也能提交，遗留问题是否为空完全不参与判定；
- 登记合格入口：直接把状态置为"试验合格"，不校验任何字段；
- 登记缺陷入口：直接把状态置为"存在缺陷"，也不要求遗留问题非空。

因此对改造前已经落库的历史记录，"合格/缺陷结论"就是它当时落库的
status（试验合格 / 存在缺陷）。这里把该口径冻结成纯函数，作为
"改造前后同一批数据判定要对得上"的对账基线，防止收拢后的新判定
翻历史旧账。新规则的分叉、增强只应作用于改造后经过新口径的记录。
"""
from __future__ import annotations

from typing import Any, Mapping

LEGACY_PASSED = "试验合格"
LEGACY_DEFECT = "存在缺陷"

_STATUS_TO_CONCLUSION = {
    LEGACY_PASSED: "合格",
    LEGACY_DEFECT: "缺陷",
}
_CONCLUSION_TO_STATUS = {value: key for key, value in _STATUS_TO_CONCLUSION.items()}


def legacy_final_conclusion(record: Mapping[str, Any]) -> str | None:
    """改造前口径：终态记录的结论就是其落库状态；非终态返回 None。"""

    return _STATUS_TO_CONCLUSION.get(str(record.get("status") or ""))


def legacy_status_for_conclusion(conclusion: str) -> str:
    """改造前口径：合格/缺陷动作直接落成的目标状态。"""

    return _CONCLUSION_TO_STATUS[conclusion]
