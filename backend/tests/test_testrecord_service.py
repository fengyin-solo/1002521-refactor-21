"""联锁试验收拢改造的服务层与接口契约测试。

覆盖：
- 三个入口（提交/安排试验、登记合格、登记缺陷）共用同一份判定；
- 对外接口参数保持改造前形态（POST /{id}/actions，body 为 {values:{action,...}}）；
- 历史试验记录不重写，但能被新判定读出，且与改造前基线结论逐一对上。
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.services.testrecord_rules import JUDGE_VERSION
from tests.legacy_baseline import legacy_final_conclusion


def _seed_testrecords() -> list[dict]:
    """取一份改造前风格的历史试验记录：含终态合格/缺陷与非终态记录。"""

    return [
        {"id": 101, "status": "待试验", "试验编号": "OLD-1", "遗留问题": "历史占位1"},
        {"id": 102, "status": "试验中", "试验编号": "OLD-2", "遗留问题": "历史占位2"},
        {"id": 103, "status": "试验合格", "试验编号": "OLD-3", "遗留问题": "历史占位3"},
        {"id": 104, "status": "存在缺陷", "试验编号": "OLD-4", "遗留问题": ""},
    ]


class ServiceUnifiedSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.store import store

        self.store = store
        self.table = store.rows("testrecord")
        self._backup = [dict(row) for row in self.table]
        self.table.clear()
        self.table.extend(_seed_testrecords())

    def tearDown(self) -> None:
        self.table.clear()
        self.table.extend(self._backup)

    def test_three_entry_methods_share_one_source(self) -> None:
        from app.services.testrecord import TestrecordService

        service = TestrecordService()
        # 历史缺陷记录：三个显式入口读到的结论口径一致，且与历史状态一致。
        _, msg_pass = service.register_pass(104)
        entry, _ = service.register_defect(104)
        self.assertIn("历史结论为缺陷", msg_pass)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["status"], "存在缺陷")

    def test_legacy_record_not_rewritten_on_replay(self) -> None:
        from app.services.testrecord import TestrecordService

        service = TestrecordService()
        before = dict(self._find(103))
        entry, _ = service.register_pass(103)
        after = self._find(103)
        self.assertIsNotNone(entry)
        # 不补写判定版本标记、不改动任何历史业务字段。
        self.assertNotIn("判定版本", after)
        for key, value in before.items():
            self.assertEqual(after.get(key), value, key)

    def test_new_record_flow_uses_unified_rules(self) -> None:
        from app.services.testrecord import TestrecordService

        service = TestrecordService()
        created, missing = service.create_entry(
            {"试验编号": "NEW-1", "试验日期": "2026-09-01", "试验车站": "S1"}
        )
        self.assertEqual(missing, [])
        new_id = created["id"]

        entry, message = service.submit_test(new_id, {})
        self.assertIsNone(entry)
        self.assertIn("试验项目", message)

        entry, _ = service.submit_test(new_id, {"试验项目": "进路联锁", "试验人员": "张三"})
        self.assertEqual(entry["status"], "试验中")

        entry, _ = service.register_pass(new_id, {"遗留问题": ""})
        self.assertEqual(entry["status"], "试验合格")
        self.assertEqual(entry["判定版本"], JUDGE_VERSION)

    def _find(self, entry_id: int) -> dict:
        return next(row for row in self.table if row["id"] == entry_id)


class HttpContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        from app.store import store

        self.table = store.rows("testrecord")
        self._backup = [dict(row) for row in self.table]
        self.table.clear()
        self.table.extend(_seed_testrecords())

    def tearDown(self) -> None:
        self.table.clear()
        self.table.extend(self._backup)

    def test_action_endpoint_shape_unchanged(self) -> None:
        # 接口路径、请求体结构与返回体与改造前一致。
        response = self.client.post(
            "/api/testrecord/103/actions", json={"values": {"action": "登记合格"}}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertSetEqual(set(body.keys()), {"ok", "message", "entry"})
        self.assertTrue(body["ok"])
        self.assertEqual(body["entry"]["status"], "试验合格")

    def test_submit_via_http_blocks_missing_fields(self) -> None:
        created = self.client.post(
            "/api/testrecord",
            json={"values": {"试验编号": "NEW-HTTP", "试验日期": "2026-09-01", "试验车站": "S"}},
        ).json()
        new_id = created["entry"]["id"]
        response = self.client.post(
            f"/api/testrecord/{new_id}/actions", json={"values": {"action": "安排试验"}}
        )
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertIn("试验项目", body["message"])
        self.assertIn("试验人员", body["message"])

    def test_unknown_action_shape_unchanged(self) -> None:
        response = self.client.post(
            "/api/testrecord/101/actions", json={"values": {"action": "非法动作"}}
        )
        self.assertFalse(response.json()["ok"])


class BeforeAfterReconciliationTest(unittest.TestCase):
    """改造前后同一批数据的合格/缺陷判定必须逐一对上。"""

    def setUp(self) -> None:
        from app.store import store

        self.table = store.rows("testrecord")
        self._backup = [dict(row) for row in self.table]
        self.table.clear()
        self.table.extend(_seed_testrecords())

    def tearDown(self) -> None:
        self.table.clear()
        self.table.extend(self._backup)

    def test_finalized_history_matches_legacy_baseline(self) -> None:
        from app.services.testrecord_rules import evaluate_record

        for record in _seed_testrecords():
            baseline = legacy_final_conclusion(record)
            if baseline is None:
                continue  # 非终态记录改造前没有合格/缺陷结论，不参与对账
            for entrance in ("登记合格", "登记缺陷"):
                verdict = evaluate_record(record, entrance=entrance)
                self.assertTrue(
                    verdict.legacy, (record["id"], entrance)
                )
                self.assertEqual(
                    verdict.conclusion,
                    baseline,
                    f"历史记录 {record['id']} 从 {entrance} 判定与改造前不一致",
                )
                # 与改造前动作想要的结论一致时放行，不一致时拦截且不翻案。
                want = "合格" if entrance == "登记合格" else "缺陷"
                self.assertEqual(verdict.accepted, want == baseline)

    def test_seed_data_remains_physically_unchanged(self) -> None:
        # 走一遍所有历史记录的只读判定，数据本体不应被重写。
        from app.services.testrecord_rules import evaluate_record

        snapshot = [dict(row) for row in self.table]
        for record in snapshot:
            for entrance in ("提交", "登记合格", "登记缺陷"):
                evaluate_record(record, entrance=entrance)
        after = [dict(row) for row in self.table]
        self.assertEqual(snapshot, after)


if __name__ == "__main__":
    unittest.main()
