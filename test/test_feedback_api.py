"""反馈接口验收测试（服务层 + 临时 SQLite，不依赖 Milvus/docker）。

覆盖：创建、覆盖(upsert)、取消、越权、非法 rating、并发 upsert 不重复、
刷新恢复(读取)、分页、汇总。
"""

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import server.models.feedback_model  # noqa: F401
import server.models.user_model  # noqa: F401
import server.models.chat_model  # noqa: F401
from server.models import Base
from server.models.chat_model import ChatRecord
from server.models.feedback_model import AnswerFeedback
from server.models.user_model import User
from server.services import feedback_service
from server.services.feedback_service import (
    FeedbackError,
    MessageNotFoundError,
    upsert_feedback,
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


def _seed_user_and_conv(session, username="alice", conv_id="conv-1", msg_ids=("m-1", "m-2")):
    user = User(username=username)
    session.add(user)
    session.flush()
    session.add(
        ChatRecord(
            conv_id=conv_id,
            user_id=user.id,
            content=json.dumps(
                {
                    "id": conv_id,
                    "messages": [
                        {"id": mid, "role": "received", "content": f"回答 {i}"}
                        for i, mid in enumerate(msg_ids)
                    ],
                },
                ensure_ascii=False,
            ),
        )
    )
    session.commit()
    return user.id


class FeedbackServiceTests(unittest.TestCase):
    def test_create_and_read(self):
        with _temp_db() as (engine, session):
            uid = _seed_user_and_conv(session)
            feedback, created = upsert_feedback(
                session, uid, "m-1", conversation_id="conv-1", rating="up"
            )
            self.assertTrue(created)
            self.assertEqual(feedback["rating"], "up")
            self.assertEqual(feedback["message_id"], "m-1")
            self.assertEqual(feedback["conversation_id"], "conv-1")

            got = feedback_service.get_user_feedback(session, uid, "m-1")
            self.assertEqual(got["rating"], "up")
            # 刷新后状态恢复：再次读取仍是 up
            self.assertEqual(feedback_service.get_user_feedback(session, uid, "m-1")["rating"], "up")

    def test_upsert_overwrite_no_duplicate(self):
        with _temp_db() as (engine, session):
            uid = _seed_user_and_conv(session)
            upsert_feedback(session, uid, "m-1", rating="up")
            feedback, created = upsert_feedback(
                session, uid, "m-1", rating="down", reason="证据不足"
            )
            self.assertFalse(created)
            self.assertEqual(feedback["rating"], "down")
            self.assertEqual(feedback["reason"], "证据不足")
            rows = (
                session.query(AnswerFeedback)
                .filter_by(user_id=uid, message_id="m-1")
                .all()
            )
            self.assertEqual(len(rows), 1, "重复 upsert 不得产生重复记录")

    def test_concurrent_upsert_single_row(self):
        """连续 upsert（并发场景的串行化近似）后只有一行，且最新评价生效。"""
        with _temp_db() as (engine, session):
            uid = _seed_user_and_conv(session)
            Session = sessionmaker(bind=engine)
            s1, s2 = Session(), Session()
            try:
                upsert_feedback(s1, uid, "m-2", rating="up")
                upsert_feedback(s2, uid, "m-2", rating="down")
                rows = session.query(AnswerFeedback).filter_by(user_id=uid, message_id="m-2").all()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].rating, "down")
            finally:
                s1.close()
                s2.close()

    def test_delete_cancels(self):
        with _temp_db() as (engine, session):
            uid = _seed_user_and_conv(session)
            upsert_feedback(session, uid, "m-1", rating="up")
            self.assertTrue(feedback_service.delete_feedback(session, uid, "m-1"))
            self.assertIsNone(feedback_service.get_user_feedback(session, uid, "m-1"))
            # 再次取消返回 False
            self.assertFalse(feedback_service.delete_feedback(session, uid, "m-1"))

    def test_unauthorized_message_rejected(self):
        """越权：message_id 不在当前用户聊天记录中 → 404 语义错误。"""
        with _temp_db() as (engine, session):
            uid = _seed_user_and_conv(session)
            with self.assertRaises(MessageNotFoundError):
                upsert_feedback(session, uid, "someone-elses-msg", rating="up")
            with self.assertRaises(MessageNotFoundError):
                feedback_service.get_user_feedback(session, uid, "someone-elses-msg")
            with self.assertRaises(MessageNotFoundError):
                feedback_service.delete_feedback(session, uid, "someone-elses-msg")

    def test_user_cannot_rate_other_users_message(self):
        """用户 B 不能给用户 A 的会话消息评价。"""
        with _temp_db() as (engine, session):
            uid_a = _seed_user_and_conv(session, username="alice", conv_id="ca", msg_ids=("ma",))
            uid_b = _seed_user_and_conv(session, username="bob", conv_id="cb", msg_ids=("mb",))
            with self.assertRaises(MessageNotFoundError):
                upsert_feedback(session, uid_b, "ma", rating="up")
            # A 可以评价自己的
            feedback, created = upsert_feedback(session, uid_a, "ma", rating="up")
            self.assertTrue(created)

    def test_invalid_rating_rejected(self):
        with _temp_db() as (engine, session):
            uid = _seed_user_and_conv(session)
            for bad in ("", "like", "maybe", None):
                with self.assertRaises(FeedbackError):
                    upsert_feedback(session, uid, "m-1", rating=bad)

    def test_rating_case_insensitive(self):
        with _temp_db() as (engine, session):
            uid = _seed_user_and_conv(session)
            feedback, created = upsert_feedback(session, uid, "m-1", rating=" UP ")
            self.assertTrue(created)
            self.assertEqual(feedback["rating"], "up")

    def test_list_mine_pagination(self):
        with _temp_db() as (engine, session):
            uid = _seed_user_and_conv(session, msg_ids=("m-a", "m-b", "m-c", "m-d", "m-e"))
            for mid in ("m-a", "m-b", "m-c", "m-d", "m-e"):
                upsert_feedback(session, uid, mid, rating="up")
            page1 = feedback_service.list_mine(session, uid, page=1, page_size=2)
            self.assertEqual(page1["total"], 5)
            self.assertEqual(len(page1["items"]), 2)
            page2 = feedback_service.list_mine(session, uid, page=2, page_size=2)
            self.assertEqual(len(page2["items"]), 2)
            page3 = feedback_service.list_mine(session, uid, page=3, page_size=2)
            self.assertEqual(len(page3["items"]), 1)

    def test_summary_aggregates(self):
        with _temp_db() as (engine, session):
            uid = _seed_user_and_conv(session, msg_ids=("m-1", "m-2", "m-3", "m-4"))
            upsert_feedback(session, uid, "m-1", rating="up")
            upsert_feedback(session, uid, "m-2", rating="down", reason="证据不充分")
            upsert_feedback(session, uid, "m-3", rating="down", reason="证据不充分")
            summary = feedback_service.summarize(session)
            self.assertEqual(summary["total"], 3)
            self.assertEqual(summary["up"], 1)
            self.assertEqual(summary["down"], 2)
            self.assertAlmostEqual(summary["satisfaction_rate"], round(1 / 3, 4))
            self.assertEqual(
                summary["down_reasons"][0],
                {"reason": "证据不充分", "count": 2},
            )
            # 覆盖率 = 反馈条数 / 回答消息数（4 条回答）
            self.assertAlmostEqual(summary["coverage_rate"], round(3 / 4, 4))

    def test_unique_constraint_enforced(self):
        with _temp_db() as (engine, session):
            uid = _seed_user_and_conv(session)
            session.add(AnswerFeedback(user_id=uid, message_id="m-1", rating="up"))
            session.commit()
            session.add(AnswerFeedback(user_id=uid, message_id="m-1", rating="down"))
            with self.assertRaises(Exception):
                session.commit()
            session.rollback()

    def test_summary_reflects_delete(self):
        """删除反馈后统计同步变化。"""
        with _temp_db() as (engine, session):
            uid = _seed_user_and_conv(session, msg_ids=("m-1", "m-2"))
            upsert_feedback(session, uid, "m-1", rating="up")
            upsert_feedback(session, uid, "m-2", rating="down")
            self.assertEqual(feedback_service.summarize(session)["total"], 2)
            feedback_service.delete_feedback(session, uid, "m-1")
            summary = feedback_service.summarize(session)
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["up"], 0)
            self.assertEqual(summary["down"], 1)


class StatisticsOverviewFeedbackTests(unittest.TestCase):
    """/api/statistics/overview 的 feedback 区域（源码级验证，避免引入 Milvus）。"""

    def setUp(self):
        self.source = Path(__file__).resolve().parents[1] / "server" / "services" / "statistics_service.py"
        self.src = self.source.read_text(encoding="utf-8")

    def test_overview_has_feedback_region(self):
        self.assertIn('"feedback": feedback_service.summarize(db)', self.src)

    def test_overview_keeps_old_fields(self):
        # 旧前端字段保持兼容
        for field in (
            '"totals"',
            '"daily_trend"',
            '"agent_distribution"',
            '"hot_questions"',
            '"top_users"',
            '"recent_activity"',
        ):
            self.assertIn(field, self.src, f"overview 缺失旧字段 {field}")


if __name__ == "__main__":
    unittest.main()
