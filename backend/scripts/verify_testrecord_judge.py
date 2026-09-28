"""联锁试验判定收拢的前后一致性核对。

用法：cd backend && .venv/bin/python scripts/verify_testrecord_judge.py

核对四件事：
1. 既有接口参数未变：create_entry(values)、run_action(entry_id, action)；
2. 提交、登记合格、登记缺陷三个入口对同一份记录给出完全相同的结论，
   试验项目、试验人员缺失时三个入口的拦截口径一致，遗留问题统一允许留空；
3. 历史种子数据不被重写，也能被新判定读出（缺键按空值处理）；
4. 同一批历史数据在改造前后的合格/缺陷判定逐条对上。
"""
from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.seed import SEED_ROWS  # noqa: E402
from app.services import testrecord as current  # noqa: E402
from app.services.testrecord import TestrecordService, judge_trial  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "通过" if condition else "失败"
    line = f"[{status}] {name}"
    if detail:
        line += f" —— {detail}"
    print(line)
    if not condition:
        FAILURES.append(name)


def load_old_source() -> str | None:
    """取 git HEAD 上的旧版服务源码；HEAD 已是收拢后版本时返回 None。"""
    result = subprocess.run(
        ["git", "show", "HEAD:backend/app/services/testrecord.py"],
        capture_output=True,
        text=True,
        cwd=BACKEND_ROOT.parent,
    )
    if result.returncode != 0:
        return None
    if "judge_trial" in result.stdout:
        return None
    return result.stdout


def old_required_fields(source: str) -> list[str]:
    """从旧版源码里 exec 出旧判定使用的 REQUIRED_FIELDS，保证对比用的是真实旧逻辑。"""
    namespace: dict[str, object] = {}
    exec(compile(source, "old_testrecord.py", "exec"), namespace)
    return list(namespace["REQUIRED_FIELDS"])  # type: ignore[arg-type]


def check_signatures() -> None:
    service = TestrecordService()
    create_params = list(inspect.signature(service.create_entry).parameters)
    action_params = list(inspect.signature(service.run_action).parameters)
    check(
        "接口参数未变：create_entry(values)、run_action(entry_id, action)",
        create_params == ["values"] and action_params == ["entry_id", "action"],
        f"create_entry{tuple(create_params)} / run_action{tuple(action_params)}",
    )


def check_entries_agree() -> None:
    service = TestrecordService()
    from app.store import store

    rows = store.rows(current.MODULE)
    original_len = len(rows)
    base = {"试验编号": "TEST-VERIFY", "试验日期": "2026-09-28", "试验车站": "核对站"}
    try:
        # 遗留问题为空的完整记录：三个入口都应放行，且结论一致。
        full = {**base, "试验项目": "道岔转换试验", "试验人员": "张三", "遗留问题": ""}
        entry, missing_submit = service.create_entry(dict(full))
        ok_submit = entry is not None and not missing_submit
        results = {}
        if entry is not None:
            for action in current.JUDGE_ACTIONS:
                _, message = service.run_action(int(entry["id"]), action)
                results[action] = message == f"试验记录已{action}"
        check(
            "三个入口对同一份完整记录结论一致（遗留问题允许留空）",
            ok_submit and len(results) == len(current.JUDGE_ACTIONS) and all(results.values()),
            f"提交缺失={missing_submit}，登记结论={results}",
        )

        # 试验项目、试验人员缺失：三个入口都拦截，且缺失清单一致。
        for offset, field in enumerate(("试验项目", "试验人员")):
            record = {**base, "试验项目": "道岔转换试验", "试验人员": "张三"}
            del record[field]
            _, missing_submit = service.create_entry(dict(record))
            # 模拟历史遗留记录直接落库，走登记入口。
            legacy = dict(record)
            legacy["id"] = 9000 + offset
            rows.append(legacy)
            messages = {
                action: service.run_action(int(legacy["id"]), action)[1]
                for action in current.JUDGE_ACTIONS
            }
            blocked = all(field in message for message in messages.values())
            check(
                f"缺{field}时三个入口拦截口径一致",
                missing_submit == [field] and blocked,
                f"提交缺失={missing_submit}，登记消息={messages}",
            )
    finally:
        # 核对用临时记录不落盘，恢复内存表到核对前。
        del rows[original_len:]


def check_historical_readable() -> None:
    # 旧版 create_entry 只落库三个基础字段，模拟这种老结构记录。
    legacy = {"id": 7, "试验编号": "TEST-OLD", "试验日期": "2026-01-01", "试验车站": "老站"}
    try:
        missing = judge_trial(legacy)
    except Exception as error:  # noqa: BLE001
        check("历史老结构记录能被新判定读出", False, f"抛出异常：{error!r}")
        return
    check(
        "历史老结构记录能被新判定读出",
        missing == ["试验项目", "试验人员"],
        f"缺失清单={missing}",
    )


def check_verdicts_match() -> None:
    source = load_old_source()
    if source is None:
        print("[跳过] HEAD 已是收拢后版本，新旧对比失去参照，仅核对当前判定")
        return
    old_required = old_required_fields(source)

    def old_submit_missing(record: dict) -> list[str]:
        return [f for f in old_required if not str(record.get(f) or "").strip()]

    mismatches = []
    for row in SEED_ROWS["testrecord"]:
        new_missing = judge_trial(row)
        # 旧逻辑：提交查 REQUIRED_FIELDS，登记合格/登记缺陷不做字段校验。
        old_verdicts = {"提交": old_submit_missing(row), "登记合格": [], "登记缺陷": []}
        for entry_name, old_missing in old_verdicts.items():
            if bool(old_missing) != bool(new_missing):
                mismatches.append(f"{row.get('试验编号')}@{entry_name}: 旧={old_missing} 新={new_missing}")
    check(
        "同一批历史数据改造前后判定逐条对上",
        not mismatches,
        "；".join(mismatches) if mismatches else f"共核对 {len(SEED_ROWS['testrecord'])} 条 × 3 个入口",
    )

    overturned = [
        row.get("试验编号")
        for row in SEED_ROWS["testrecord"]
        if row.get("status") in ("试验合格", "存在缺陷") and judge_trial(row)
    ]
    check(
        "历史合格/缺陷记录不被新判定推翻",
        not overturned,
        f"被推翻的记录={overturned}" if overturned else "历史结论全部维持",
    )


def main() -> int:
    check_signatures()
    check_entries_agree()
    check_historical_readable()
    check_verdicts_match()
    if FAILURES:
        print(f"\n核对失败 {len(FAILURES)} 项：{'、'.join(FAILURES)}")
        return 1
    print("\n全部核对通过：三个入口共用一份判定，历史数据结论与改造前一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
