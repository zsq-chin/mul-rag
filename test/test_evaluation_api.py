"""问答测试集验收测试（服务层 + 临时 SQLite，不依赖 Milvus/docker）。

覆盖：测试集/用例 CRUD、分页搜索、级联删除、JSON/CSV 导入校验与回滚、
公式注入防护、5MB/5000 条上限、JSON/CSV 往返一致性。
"""

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import server.models.evaluation_model  # noqa: F401
from server.models import Base
from server.models.evaluation_model import EvaluationCase, EvaluationSuite
from server.services import evaluation_service
from server.services.evaluation_service import (
    EvaluationError,
    SuiteNotFound,
    CaseNotFound,
    import_cases,
    export_cases_json,
    export_cases_csv,
)


@contextmanager
def _temp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        with engine.connect() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.commit()
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            yield engine, session
        finally:
            session.close()
            engine.dispose()


def _make_suite(session, name="石油安全测试集", category="石油"):
    return evaluation_service.create_suite(session, name, "描述", category, creator="admin")


def _make_case(session, suite_id, question="如何防止原油泄漏？"):
    return evaluation_service.create_case(
        session,
        suite_id,
        {
            "question": question,
            "answer": "定期巡检管道并安装泄漏报警装置。",
            "key_points": ["巡检", "报警装置"],
            "kb_id": "kb_test",
            "category": "安全",
            "difficulty": "medium",
            "enabled": True,
            "note": "示例",
        },
    )


class EvaluationSuiteTests(unittest.TestCase):
    def test_suite_crud(self):
        with _temp_db() as (engine, session):
            created = _make_suite(session)
            self.assertEqual(created["name"], "石油安全测试集")
            self.assertEqual(created["category"], "石油")
            self.assertEqual(created["case_count"], 0)
            # get
            got = evaluation_service.get_suite(session, created["id"])
            self.assertEqual(got["id"], created["id"])
            # list
            data = evaluation_service.list_suites(session)
            self.assertEqual(data["total"], 1)
            self.assertEqual(len(data["items"]), 1)
            # search
            data = evaluation_service.list_suites(session, keyword="安全")
            self.assertEqual(data["total"], 1)
            data = evaluation_service.list_suites(session, keyword="不存在")
            self.assertEqual(data["total"], 0)
            # update
            upd = evaluation_service.update_suite(session, created["id"], {"name": "新名称"})
            self.assertEqual(upd["name"], "新名称")
            # delete
            self.assertTrue(evaluation_service.delete_suite(session, created["id"]))
            with self.assertRaises(SuiteNotFound):
                evaluation_service.get_suite(session, created["id"])

    def test_suite_validation(self):
        with _temp_db() as (engine, session):
            with self.assertRaises(EvaluationError):
                evaluation_service.create_suite(session, "   ")
            suite = _make_suite(session)
            with self.assertRaises(EvaluationError):
                evaluation_service.update_suite(session, suite["id"], {"name": ""})
            with self.assertRaises(SuiteNotFound):
                evaluation_service.get_suite(session, 9999)

    def test_case_crud_and_pagination(self):
        with _temp_db() as (engine, session):
            suite = _make_suite(session)
            for i in range(5):
                _make_case(session, suite["id"], question=f"问题{i}")
            data = evaluation_service.list_cases(session, suite["id"], page=1, page_size=2)
            self.assertEqual(data["total"], 5)
            self.assertEqual(len(data["items"]), 2)
            data = evaluation_service.list_cases(session, suite["id"], keyword="问题3")
            self.assertEqual(data["total"], 1)
            # update
            case_id = data["items"][0]["id"]
            upd = evaluation_service.update_case(
                session, suite["id"], case_id, {"difficulty": "hard", "enabled": False}
            )
            self.assertEqual(upd["difficulty"], "hard")
            self.assertIs(upd["enabled"], False)
            # delete
            self.assertTrue(evaluation_service.delete_case(session, suite["id"], case_id))
            with self.assertRaises(CaseNotFound):
                evaluation_service.update_case(session, suite["id"], case_id, {"note": "x"})
            # 越权：另一个 suite 下查不到该 case
            suite2 = _make_suite(session, "另一套")
            with self.assertRaises(CaseNotFound):
                evaluation_service.delete_case(session, suite2["id"], case_id)

    def test_case_null_clears_optional_fields(self):
        """P2-2 验收：显式 null 清空答案/要点/备注/分类，重新加载后旧值不保留。"""
        with _temp_db() as (engine, session):
            suite = _make_suite(session)
            case = _make_case(session, suite["id"])
            # 明确清空（等价于 PATCH 发送 null）
            evaluation_service.update_case(
                session,
                suite["id"],
                case["id"],
                {"answer": None, "key_points": None, "note": None, "category": None},
            )
            reloaded = evaluation_service.list_cases(session, suite["id"])["items"][0]
            self.assertIsNone(reloaded["answer"])
            self.assertEqual(reloaded["key_points"], [])
            self.assertIsNone(reloaded["note"])
            self.assertIsNone(reloaded["category"])
            # 未提交的字段不受影响
            self.assertEqual(reloaded["question"], "如何防止原油泄漏？")
            self.assertEqual(reloaded["difficulty"], "medium")
            self.assertEqual(reloaded["kb_id"], "kb_test")

    def test_suite_null_clears_description_and_category(self):
        """P2-2 验收：suite 描述/分类可被 null 清空并重新加载。"""
        with _temp_db() as (engine, session):
            suite = _make_suite(session, "安全测试")
            evaluation_service.update_suite(
                session, suite["id"], {"description": None, "category": None}
            )
            reloaded = evaluation_service.list_suites(session)["items"][0]
            self.assertIsNone(reloaded["description"])
            self.assertIsNone(reloaded["category"])
            self.assertEqual(reloaded["name"], "安全测试")

    def test_case_validation(self):
        with _temp_db() as (engine, session):
            suite = _make_suite(session)
            with self.assertRaises(EvaluationError):
                evaluation_service.create_case(session, suite["id"], {"question": ""})
            with self.assertRaises(EvaluationError):
                evaluation_service.create_case(
                    session, suite["id"], {"question": "q", "difficulty": "impossible"}
                )
            with self.assertRaises(EvaluationError):
                evaluation_service.create_case(
                    session, suite["id"], {"question": "q", "key_points": "not-a-list"}
                )
            with self.assertRaises(SuiteNotFound):
                evaluation_service.create_case(session, 9999, {"question": "q"})

    def test_delete_suite_cascades_cases(self):
        with _temp_db() as (engine, session):
            suite = _make_suite(session)
            _make_case(session, suite["id"])
            _make_case(session, suite["id"])
            self.assertEqual(
                session.query(EvaluationCase).filter_by(suite_id=suite["id"]).count(), 2
            )
            evaluation_service.delete_suite(session, suite["id"])
            self.assertEqual(
                session.query(EvaluationCase).filter_by(suite_id=suite["id"]).count(), 0
            )


class EvaluationImportExportTests(unittest.TestCase):
    def test_import_json_success(self):
        with _temp_db() as (engine, session):
            suite = _make_suite(session)
            content = json.dumps(
                [
                    {"question": "q1", "answer": "a1", "difficulty": "easy"},
                    {"question": "q2", "answer": "a2", "key_points": ["k1", "k2"]},
                ]
            ).encode("utf-8")
            result = import_cases(session, suite["id"], content, "json")
            self.assertEqual(result["imported"], 2)
            self.assertEqual(result["row_errors"], [])
            self.assertEqual(
                session.query(EvaluationCase).filter_by(suite_id=suite["id"]).count(), 2
            )

    def test_import_rolls_back_on_any_row_error(self):
        with _temp_db() as (engine, session):
            suite = _make_suite(session)
            content = json.dumps(
                [
                    {"question": "ok1", "answer": "a1"},
                    {"question": "   ", "answer": "a2"},  # 第 2 行非法
                    {"question": "ok3", "answer": "a3"},
                ]
            ).encode("utf-8")
            result = import_cases(session, suite["id"], content, "json")
            self.assertEqual(result["imported"], 0)
            self.assertEqual(len(result["row_errors"]), 1)
            self.assertEqual(result["row_errors"][0]["row"], 2)
            # 全部回滚：一行都没写入
            self.assertEqual(
                session.query(EvaluationCase).filter_by(suite_id=suite["id"]).count(), 0
            )

    def test_import_json_wrong_difficulty_rejected_with_row(self):
        with _temp_db() as (engine, session):
            suite = _make_suite(session)
            content = json.dumps([{"question": "q", "difficulty": "expert"}]).encode("utf-8")
            result = import_cases(session, suite["id"], content, "json")
            self.assertEqual(result["imported"], 0)
            self.assertEqual(result["row_errors"][0]["row"], 1)

    def test_import_csv_with_header(self):
        with _temp_db() as (engine, session):
            suite = _make_suite(session)
            csv_text = (
                "question,answer,key_points,kb_id,category,difficulty,enabled,note\n"
                "q1,a1,\"[\"\"k1\"\", \"\"k2\"\"]\",kb_x,安全,easy,1,备注1\n"
                "q2,a2,,kb_y,标准,hard,0,\n"
            )
            result = import_cases(session, suite["id"], csv_text.encode("utf-8"), "csv")
            self.assertEqual(result["imported"], 2)
            data = evaluation_service.list_cases(session, suite["id"], page_size=100)
            by_question = {c["question"]: c for c in data["items"]}
            self.assertEqual(by_question["q1"]["key_points"], ["k1", "k2"])
            self.assertIs(by_question["q2"]["enabled"], False)

    def test_import_strips_formula_prefix(self):
        with _temp_db() as (engine, session):
            suite = _make_suite(session)
            csv_text = (
                "question,answer,key_points,kb_id,category,difficulty,enabled,note\n"
                "'=HYPERLINK(\"http://evil\"),a1,,,安全,easy,1,note\n"
            )
            result = import_cases(session, suite["id"], csv_text.encode("utf-8"), "csv")
            self.assertEqual(result["imported"], 1)
            data = evaluation_service.list_cases(session, suite["id"], page_size=100)
            self.assertTrue(data["items"][0]["question"].startswith("=HYPERLINK"))

    def test_import_size_and_row_limits(self):
        with _temp_db() as (engine, session):
            suite = _make_suite(session)
            rows = [{"question": f"q{i}"} for i in range(evaluation_service.MAX_IMPORT_ROWS + 1)]
            content = json.dumps(rows).encode("utf-8")
            with self.assertRaises(EvaluationError):
                import_cases(session, suite["id"], content, "json")
            # 5MB 上限
            big = b" " * (evaluation_service.MAX_IMPORT_BYTES + 1)
            with self.assertRaises(EvaluationError):
                import_cases(session, suite["id"], big, "json")
            # 非法编码
            with self.assertRaises(EvaluationError):
                import_cases(session, suite["id"], b"\xff\xfe\x00garbage", "json")

    def test_import_bad_format_and_empty(self):
        with _temp_db() as (engine, session):
            suite = _make_suite(session)
            with self.assertRaises(EvaluationError):
                import_cases(session, suite["id"], b"{}", "xlsx")
            with self.assertRaises(EvaluationError):
                import_cases(session, suite["id"], b"", "json")
            # 非数组 JSON
            with self.assertRaises(EvaluationError):
                import_cases(session, suite["id"], b'{"a": 1}', "json")

    def test_export_json_round_trip(self):
        with _temp_db() as (engine, session):
            suite = _make_suite(session)
            _make_case(session, suite["id"])
            _make_case(session, suite["id"], question="第二问")
            exported = export_cases_json(session, suite["id"])
            self.assertEqual(exported["total"], 2)
            # 清空后重新导入，内容一致
            for case in session.query(EvaluationCase).filter_by(suite_id=suite["id"]).all():
                session.delete(case)
            session.commit()
            content = json.dumps(exported["items"]).encode("utf-8")
            result = import_cases(session, suite["id"], content, "json")
            self.assertEqual(result["imported"], 2)
            again = export_cases_json(session, suite["id"])
            self.assertEqual(
                sorted((c["question"], c["answer"]) for c in again["items"]),
                sorted((c["question"], c["answer"]) for c in exported["items"]),
            )

    def test_export_csv_formula_protection_and_round_trip(self):
        with _temp_db() as (engine, session):
            suite = _make_suite(session)
            evaluation_service.create_case(
                session, suite["id"], {"question": "=1+2", "answer": "a", "note": "@cmd"}
            )
            csv_text = export_cases_csv(session, suite["id"])
            self.assertIn("'=1+2", csv_text)
            self.assertIn("'@cmd", csv_text)
            # CSV 往返：导出 → 清空 → 导入 → 内容还原
            for case in session.query(EvaluationCase).filter_by(suite_id=suite["id"]).all():
                session.delete(case)
            session.commit()
            result = import_cases(session, suite["id"], csv_text.encode("utf-8"), "csv")
            self.assertEqual(result["imported"], 1)
            data = evaluation_service.list_cases(session, suite["id"], page_size=100)
            self.assertEqual(data["items"][0]["question"], "=1+2")
            self.assertEqual(data["items"][0]["note"], "@cmd")


class EvaluationRouterSourceTests(unittest.TestCase):
    """router 源码级验证。"""

    def setUp(self):
        self.src = (
            Path(__file__).resolve().parents[1]
            / "server"
            / "routers"
            / "evaluation_router.py"
        ).read_text(encoding="utf-8")

    def test_all_endpoints_are_superadmin_only(self):
        # 11 个既有端点 + execute = 12 个 Depends 出现 + 1 处 import
        self.assertEqual(self.src.count("get_superadmin_user"), 13)

    def test_import_uses_upload_file_and_audit(self):
        self.assertIn("UploadFile", self.src)
        self.assertIn("file: UploadFile = File(...)", self.src)
        self.assertIn('"evaluation.import"', self.src)

    def test_export_supports_json_and_csv(self):
        self.assertIn('format: str = Query("json", pattern="^(json|csv)$")', self.src)
        self.assertIn("export_cases_csv", self.src)

    def test_patch_uses_exclude_unset_to_allow_clearing(self):
        """P2-2：两个 PATCH 处理器（suite/case）必须用 exclude_unset 区分清空。"""
        self.assertEqual(self.src.count("model_dump(exclude_unset=True)"), 2)

    def test_no_remote_multimodal_access(self):
        self.assertNotIn("multimodal", self.src.lower())
        self.assertNotIn("http_client", self.src.lower())


if __name__ == "__main__":
    unittest.main()
