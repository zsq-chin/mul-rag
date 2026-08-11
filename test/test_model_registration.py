"""本地功能新增数据表的结构化测试。

- 临时 SQLite 可创建全部新表；重复初始化幂等。
- 外键开启时，删除用户级联删除其反馈记录。
- 关键唯一约束生效（反馈用户+消息、版本号）。
不依赖 Milvus / docker。直接导入模型模块注册进 Base.metadata。
"""

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

# 导入全部新模型（及 users 表）以注册进 Base.metadata
import server.models.feedback_model  # noqa: F401
import server.models.governance_model  # noqa: F401
import server.models.evaluation_model  # noqa: F401
import server.models.operations_model  # noqa: F401
import server.models.user_model  # noqa: F401
from server.models import Base
from server.models.feedback_model import AnswerFeedback
from server.models.governance_model import KnowledgeDocumentVersion
from server.models.evaluation_model import EvaluationSuite, EvaluationCase
from server.models.user_model import User

REQUIRED_TABLES = {
    "answer_feedback",
    "knowledge_governance",
    "knowledge_document_versions",
    "evaluation_suites",
    "evaluation_cases",
    "config_change_history",
    "backup_jobs",
    "alert_rules",
    "alert_events",
}


@contextmanager
def _temp_db():
    """创建启用外键的临时 SQLite，退出前释放引擎以允许删除目录。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        with engine.connect() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.commit()
        try:
            yield engine
        finally:
            engine.dispose()


def _new_session(engine):
    return sessionmaker(bind=engine)()


class ModelRegistrationTests(unittest.TestCase):
    def test_all_new_tables_created(self):
        with _temp_db() as engine:
            Base.metadata.create_all(engine)
            tables = set(inspect(engine).get_table_names())
            self.assertEqual(REQUIRED_TABLES - tables, set(), "缺失新表")

    def test_create_all_idempotent(self):
        with _temp_db() as engine:
            Base.metadata.create_all(engine)
            Base.metadata.create_all(engine)  # 第二次不报错
            tables = set(inspect(engine).get_table_names())
            self.assertIn("answer_feedback", tables)

    def test_feedback_cascade_delete_user(self):
        with _temp_db() as engine:
            Base.metadata.create_all(engine)
            session = _new_session(engine)
            try:
                user = User(username="feedback-user")
                session.add(user)
                session.flush()
                session.add(AnswerFeedback(user_id=user.id, message_id="m1", rating="up"))
                session.commit()
                self.assertEqual(
                    session.query(AnswerFeedback).filter_by(message_id="m1").count(), 1
                )
                session.delete(user)
                session.commit()
                self.assertEqual(
                    session.query(AnswerFeedback).filter_by(message_id="m1").count(), 0
                )
            finally:
                session.close()

    def test_feedback_unique_per_user_message(self):
        with _temp_db() as engine:
            Base.metadata.create_all(engine)
            session = _new_session(engine)
            try:
                user = User(username="fb-unique")
                session.add(user)
                session.flush()
                session.add(AnswerFeedback(user_id=user.id, message_id="m2", rating="up"))
                session.commit()
                session.add(AnswerFeedback(user_id=user.id, message_id="m2", rating="down"))
                with self.assertRaises(Exception):
                    session.commit()
                session.rollback()
            finally:
                session.close()

    def test_version_unique_per_db_file(self):
        with _temp_db() as engine:
            Base.metadata.create_all(engine)
            session = _new_session(engine)
            try:
                session.add(KnowledgeDocumentVersion(db_id="db1", file_id="f1", version=1))
                session.commit()
                session.add(KnowledgeDocumentVersion(db_id="db1", file_id="f1", version=1))
                with self.assertRaises(Exception):
                    session.commit()
                session.rollback()
            finally:
                session.close()

    def test_evaluation_suite_cascade_cases(self):
        with _temp_db() as engine:
            Base.metadata.create_all(engine)
            session = _new_session(engine)
            try:
                suite = EvaluationSuite(name="s1")
                suite.cases.append(EvaluationCase(question="q1"))
                session.add(suite)
                session.commit()
                case_id = suite.cases[0].id
                self.assertIsNotNone(case_id)
                session.delete(suite)
                session.commit()
                self.assertIsNone(
                    session.query(EvaluationCase).filter_by(id=case_id).first()
                )
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
