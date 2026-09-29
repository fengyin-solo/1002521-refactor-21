"""联锁试验判定口径：提交、登记合格、登记缺陷三个入口共用这一份规则。

设计目标：

- 判定只有一个来源（``evaluate_record``），三个入口都不许再各写一份条件；
- 试验项目、试验人员、遗留问题的缺失判定对所有入口一致（见 ``is_blank``）；
- 判定是纯函数、不写库，因此历史试验记录不需要重写，只需把旧字段读进来归一化；
- 旧记录（没有 ``判定版本`` 标记）在结论所需字段缺失时，按其历史状态回放结论，
  保证改造前后同一批数据的合格/缺陷判定对得上。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

# 当前判定规则的版本号。经过新入口提交/判定的记录会带上该标记；
# 历史记录没有这个标记，读取时走历史回放分支，不做物理重写。
JUDGE_VERSION = "unified-v1"

# 进入试验（提交）与形成结论（合格/缺陷）时都必须齐全的试验信息。
REQUIRED_TEST_FIELDS = ("试验项目", "试验人员")
# 登记缺陷时必须说明的字段；登记合格时允许为空（没有遗留问题才算合格）。
DEFECT_FIELD = "遗留问题"

# 业务状态字面量，路由与 service 都从这里取，避免各处再抄一份。
STATUS_PENDING = "待试验"
STATUS_RUNNING = "试验中"
STATUS_PASSED = "试验合格"
STATUS_DEFECT = "存在缺陷"
STATUS_ORDER = [STATUS_PENDING, STATUS_RUNNING, STATUS_PASSED, STATUS_DEFECT]

# 判定结论 -> 落库状态。三个入口的结论都从同一张表取。
CONCLUSION_STATUS = {
    "合格": STATUS_PASSED,
    "缺陷": STATUS_DEFECT,
}

# 历史状态 -> 旧口径下的结论。旧版没有判定来源，记录落成什么状态就是当时的结论；
# 新判定读历史记录时，用这张表把旧状态还原成结论，保证不重写也能读出来。
_LEGACY_STATUS_CONCLUSION = {
    STATUS_PASSED: "合格",
    STATUS_DEFECT: "缺陷",
}

# 历史字段别名 -> 统一字段名。早期记录若用了略有出入的键名，归一化时一并兼容。
_FIELD_ALIASES = {
    "试验项目内容": "试验项目",
    "试验人员姓名": "试验人员",
    "遗留问题描述": "遗留问题",
}


@dataclass(frozen=True)
class Verdict:
    """一条试验记录在统一口径下的判定结果。

    conclusion 仅取 ``合格`` / ``缺陷`` / ``未定``：
    提交入口只要求试验信息齐全，不形成合格/缺陷结论，因此为 ``未定``。
    missing 为导致无法判定/无法提交的缺失字段（统一中文展示名）。
    """

    accepted: bool
    conclusion: str
    status: str
    missing: tuple[str, ...]
    reason: str
    legacy: bool = False

    @property
    def qualified(self) -> bool:
        return self.accepted and self.conclusion == "合格"

    @property
    def defective(self) -> bool:
        return self.accepted and self.conclusion == "缺陷"


def is_blank(value: Any) -> bool:
    """字段缺失的统一口径：None、空串、纯空白都算缺失。

    三个入口过去对"空"的处理不一致（有的把 None 当已填、有的只拦空串），
    收拢后一律走这里。
    """

    if value is None:
        return True
    return str(value).strip() == ""


def normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """把任意来源（新记录、历史记录、外部提交）的字段归一化成统一键名。

    只读取、不修改原记录，也不补写数据库；缺失字段归一为 None，
    让后续判定对"键不存在"和"值为空"一视同仁。
    """

    normalized: dict[str, Any] = {}
    for raw_key, value in record.items():
        key = _FIELD_ALIASES.get(str(raw_key), str(raw_key))
        normalized[key] = value
    return normalized


def _missing_required(record: Mapping[str, Any]) -> tuple[str, ...]:
    """试验项目与试验人员缺失时的统一判定，三个入口共用。"""

    normalized = normalize_record(record)
    return tuple(field for field in REQUIRED_TEST_FIELDS if is_blank(normalized.get(field)))


def _is_legacy(record: Mapping[str, Any]) -> bool:
    """没有判定版本标记的记录视为历史记录。"""

    return is_blank(record.get("判定版本"))


def _legacy_conclusion(record: Mapping[str, Any]) -> str | None:
    """从历史记录的既有状态还原旧口径结论；没有终态结论时返回 None。"""

    return _LEGACY_STATUS_CONCLUSION.get(str(record.get("status") or ""))


def _verdict_for(
    record: Mapping[str, Any],
    *,
    want: str,
    missing: tuple[str, ...],
    legacy: bool,
) -> Verdict:
    """按目标结论（合格/缺陷）组装判定结果，拒绝原因也只有这一处生成。"""

    normalized = normalize_record(record)
    if missing:
        reason = f"试验信息不完整，缺少：{'、'.join(missing)}；请补齐后再登记"
        return Verdict(False, "未定", str(record.get("status") or STATUS_PENDING), missing, reason, legacy)

    if want == "缺陷" and is_blank(normalized.get(DEFECT_FIELD)):
        reason = "登记缺陷必须填写遗留问题；若无遗留问题应登记合格"
        return Verdict(False, "未定", str(record.get("status") or STATUS_PENDING), (DEFECT_FIELD,), reason, legacy)

    # 统一的合格/缺陷分界：试验信息齐全时，遗留问题为空即合格、非空即缺陷。
    conclusion = "缺陷" if not is_blank(normalized.get(DEFECT_FIELD)) else "合格"
    if want != conclusion:
        if want == "合格":
            reason = "存在遗留问题，不能登记合格；请先处理遗留问题或登记缺陷"
        else:
            reason = "遗留问题为空，按统一口径应判为合格"
        return Verdict(False, conclusion, CONCLUSION_STATUS[conclusion], (), reason, legacy)

    return Verdict(True, conclusion, CONCLUSION_STATUS[conclusion], (), "", legacy)


def evaluate_record(record: Mapping[str, Any], *, entrance: str) -> Verdict:
    """联锁试验三个入口的唯一判定来源。

    entrance 取值：``提交`` / ``登记合格`` / ``登记缺陷``。
    入口只决定"是否允许进入下一步/想要的结论"，不改变合格与缺陷的判定规则：
    同一条记录无论从哪个终态入口看，``conclusion`` 都相同。

    历史记录（无判定版本标记）的结论按其既有终态冻结回放，不依据新字段
    规则重算、不翻案——这保证改造前后同一批数据的合格/缺陷判定对得上；
    新规则只作用于经过新口径判定的记录。历史记录因此无需重写就能被读出来。
    """

    if entrance not in ("提交", "登记合格", "登记缺陷"):
        raise ValueError(f"未知的联锁试验判定入口：{entrance}")

    normalized = normalize_record(record)
    legacy = _is_legacy(normalized)
    missing = _missing_required(normalized)

    if entrance == "提交":
        if missing:
            reason = f"试验信息不完整，缺少：{'、'.join(missing)}；不能提交试验"
            return Verdict(False, "未定", str(normalized.get("status") or STATUS_PENDING), missing, reason, legacy)
        return Verdict(True, "未定", STATUS_RUNNING, (), "", legacy)

    want = "合格" if entrance == "登记合格" else "缺陷"

    # 历史记录：终态结论冻结回放。三个入口读出的结论都以历史状态为准，
    # 不因为字段缺失或遗留问题占位值而翻案。
    if legacy:
        historical = _legacy_conclusion(normalized)
        if historical is not None:
            if want != historical:
                reason = f"该记录历史结论为{historical}，与本次登记{want}不一致；历史结论不重写"
                return Verdict(False, historical, CONCLUSION_STATUS[historical], (), reason, legacy=True)
            return Verdict(True, historical, CONCLUSION_STATUS[historical], (), "", legacy=True)
        # 历史但尚未形成终态结论的记录，继续按统一规则判定，缺失字段一视同仁拦截。

    return _verdict_for(normalized, want=want, missing=missing, legacy=legacy)
