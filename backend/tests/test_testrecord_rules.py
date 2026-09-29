"""统一判定规则单元测试：一份口径、三个入口、缺失处理一致。"""
from __future__ import annotations

import unittest

from app.services.testrecord_rules import (
    JUDGE_VERSION,
    STATUS_DEFECT,
    STATUS_PASSED,
    STATUS_RUNNING,
    evaluate_record,
    is_blank,
    normalize_record,
)


def new_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "试验项目": "进路联锁试验",
        "试验人员": "张三",
        "遗留问题": "",
        "判定版本": JUDGE_VERSION,
        "status": "试验中",
    }
    record.update(overrides)
    return record


class MissingHandlingTest(unittest.TestCase):
    def test_blank_values_unified(self) -> None:
        for value in (None, "", "   ", "\t"):
            self.assertTrue(is_blank(value), value)
        for value in (0, "x", " x "):
            self.assertFalse(is_blank(value), value)

    def test_missing_project_or_tester_blocks_all_three_entrances(self) -> None:
        # 试验项目、试验人员缺失时的处理三个入口必须一致：都拒绝、都报缺失字段。
        for blank in (None, "", "  "):
            record = new_record(试验项目=blank, 试验人员=blank)  # type: ignore[arg-type]
            for entrance in ("提交", "登记合格", "登记缺陷"):
                verdict = evaluate_record(record, entrance=entrance)
                self.assertFalse(verdict.accepted, (entrance, blank))
                self.assertEqual(verdict.missing, ("试验项目", "试验人员"))

    def test_only_tester_missing_is_reported_consistently(self) -> None:
        record = new_record(试验人员=None)
        for entrance in ("提交", "登记合格", "登记缺陷"):
            verdict = evaluate_record(record, entrance=entrance)
            self.assertFalse(verdict.accepted)
            self.assertEqual(verdict.missing, ("试验人员",))

    def test_submit_requires_project_and_tester(self) -> None:
        self.assertFalse(evaluate_record(new_record(试验项目=None), entrance="提交").accepted)
        verdict = evaluate_record(new_record(), entrance="提交")
        self.assertTrue(verdict.accepted)
        self.assertEqual(verdict.status, STATUS_RUNNING)
        self.assertEqual(verdict.conclusion, "未定")


class UnifiedConclusionTest(unittest.TestCase):
    def test_no_remaining_issue_is_passed(self) -> None:
        for value in ("", "  ", None):
            record = new_record(遗留问题=value)  # type: ignore[arg-type]
            verdict = evaluate_record(record, entrance="登记合格")
            self.assertTrue(verdict.accepted, value)
            self.assertEqual(verdict.conclusion, "合格")
            self.assertEqual(verdict.status, STATUS_PASSED)

    def test_remaining_issue_is_defect_regardless_of_entrance(self) -> None:
        record = new_record(遗留问题="道岔表示不一致")
        from_pass = evaluate_record(record, entrance="登记合格")
        from_defect = evaluate_record(record, entrance="登记缺陷")
        # 同一份记录从两个终态入口读到的结论完全相同。
        self.assertEqual(from_pass.conclusion, "缺陷")
        self.assertEqual(from_defect.conclusion, "缺陷")
        self.assertFalse(from_pass.accepted)  # 想登记合格，但口径判缺陷
        self.assertTrue(from_defect.accepted)
        self.assertEqual(from_defect.status, STATUS_DEFECT)

    def test_defect_requires_remaining_issue(self) -> None:
        verdict = evaluate_record(new_record(遗留问题=""), entrance="登记缺陷")
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.missing, ("遗留问题",))

    def test_unknown_entrance_rejected(self) -> None:
        with self.assertRaises(ValueError):
            evaluate_record(new_record(), entrance="不存在的入口")  # type: ignore[arg-type]


class NormalizeTest(unittest.TestCase):
    def test_legacy_aliases_mapped(self) -> None:
        normalized = normalize_record(
            {"试验项目内容": "A", "试验人员姓名": "B", "遗留问题描述": "C", "遗留问题": "D"}
        )
        self.assertEqual(normalized["试验项目"], "A")
        self.assertEqual(normalized["试验人员"], "B")
        # 统一字段名优先于别名。
        self.assertEqual(normalized["遗留问题"], "D")

    def test_normalize_does_not_mutate_source(self) -> None:
        source = {"试验项目内容": "A"}
        normalize_record(source)
        self.assertEqual(source, {"试验项目内容": "A"})


if __name__ == "__main__":
    unittest.main()
