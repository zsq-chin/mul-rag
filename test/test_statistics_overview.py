"""问答统计总览行为测试（真实 ChatRecord/Thread 数据 + 临时 SQLite）。

回归覆盖 P1-2：旧实现在 statistics_router 里 `users_by_id.get(r.user_id)` 对
dict 行取属性抛 AttributeError，导致存在聊天记录时 /api/statistics/overview 直接 500。
现在聚合逻辑全部在 statistics_service.build_overview 中，行数据一律按 dict 取值，
任何脏数据（缺用户 / 异常 JSON / 空库）都不应抛异常，且返回体同时包含旧统计字段
与 feedback 反馈指标。
"""

import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import server.models.chat_model  # noqa: F401
import server.models.feedback_model  # noqa: F401
import server.models.thread_model  # noqa: F401
import server.models.user_model  # noqa: F401
from server.models import Base
from server.models.chat_model import ChatRecord
from server.models.thread_model import Thread
from server.models.user_model import User
from server.services import statistics_service


@contextmanager
def _temp_db():
    """临时 SQLite，关闭外键强制以便构造「用户已删除」的孤儿记录。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            yield session
        finally:
            session.close()
            engine.dispose()


def _conv_json(title, messages=None):
    return json.dumps(
        {
            "id": "conv-1",
            "title": title,
            "messages": messages
            or [
                {"role": "sent", "content": "什么是水力压裂？"},
                {"role": "received", "content": "水力压裂是一种……"},
            ],
        },
        ensure_ascii=False,
    )


_NO_TIME = object()


def _seed_chat(session, *, user_id=None, username="alice", title="什么是水力压裂？",
               content=None, updatetime=_NO_TIME):
    """插入一条 ChatRecord；user_id 缺省时新建用户。返回 (user_id)。"""
    if user_id is None:
        user = User(username=username)
        session.add(user)
        session.flush()
        user_id = user.id
    if updatetime is _NO_TIME:
        updatetime = datetime(2026, 8, 1, 12, 0)
    session.add(
        ChatRecord(
            conv_id="conv-1",
            user_id=user_id,
            content=content if content is not None else _conv_json(title),
            updatetime=updatetime,
        )
    )
    return user_id


class StatisticsOverviewBehaviorTests(unittest.TestCase):
    """真实数据下 build_overview 的行为（P1-2 回归）。"""

    def test_valid_records_builds_full_overview(self):
        with _temp_db() as session:
            _seed_chat(session)
            session.add(Thread(id="t-1", user_id="1", agent_id="agent-a"))
            session.commit()

            data = statistics_service.build_overview(session, days=14)

            # 旧统计字段 + feedback 反馈指标全部在返回体中
            for field in (
                "totals", "daily_trend", "agent_distribution",
                "hot_questions", "top_users", "recent_activity", "feedback",
            ):
                self.assertIn(field, data, f"overview 缺失字段 {field}")
            self.assertEqual(data["totals"]["conversations"], 1)
            self.assertEqual(data["totals"]["threads"], 1)
            self.assertEqual(data["totals"]["active_users"], 1)
            self.assertEqual(data["totals"]["questions"], 1)
            self.assertEqual(data["agent_distribution"][0]["name"], "agent-a")
            self.assertEqual(data["recent_activity"][0]["username"], "alice")
            self.assertEqual(data["recent_activity"][0]["title"], "什么是水力压裂？")
            # 空反馈表 → 汇总字段齐全且为 0
            self.assertEqual(data["feedback"]["total"], 0)

    def test_empty_database_returns_zeroed_overview(self):
        with _temp_db() as session:
            session.commit()
            data = statistics_service.build_overview(session, days=14)
            self.assertEqual(data["totals"]["conversations"], 0)
            self.assertEqual(data["totals"]["questions"], 0)
            self.assertEqual(data["recent_activity"], [])
            self.assertEqual(data["agent_distribution"], [])

    def test_missing_user_record_falls_back_without_500(self):
        """用户记录缺失（孤儿 chat_record）时回退「用户{id}」，不抛异常。"""
        with _temp_db() as session:
            _seed_chat(session, user_id=999, username=None)
            session.commit()

            data = statistics_service.build_overview(session, days=14)
            self.assertEqual(data["recent_activity"][0]["username"], "用户999")
            # top_users 同样回退到用户{id}，而不是崩溃
            user_rows = data["top_users"]
            self.assertTrue(any(r["user_id"] == 999 and r["username"] == "用户999" for r in user_rows))

    def test_malformed_json_content_does_not_crash(self):
        with _temp_db() as session:
            uid = _seed_chat(session, title="异常JSON", content="{not json, [[[")
            _seed_chat(session, user_id=uid, title="空内容", content="")
            session.commit()

            data = statistics_service.build_overview(session, days=14)
            self.assertEqual(data["totals"]["conversations"], 2)
            self.assertEqual(data["recent_activity"][0]["title"], "")
            self.assertEqual(data["hot_questions"], [])

    def test_none_updatetime_is_tolerated(self):
        """updatetime 为空时 recent_activity 时间回退为空字符串，不抛异常。"""
        with _temp_db() as session:
            _seed_chat(session, updatetime=None)
            session.commit()

            data = statistics_service.build_overview(session, days=14)
            self.assertEqual(data["recent_activity"][0]["time"], "")
            self.assertEqual(data["totals"]["conversations"], 1)


class StatisticsOverviewSourceTests(unittest.TestCase):
    """源码级回归：路由只做委托，聚合逻辑不再在路由里直接操作 dict 行。"""

    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.service = (root / "server" / "services" / "statistics_service.py").read_text(encoding="utf-8")
        self.router = (root / "server" / "routers" / "statistics_router.py").read_text(encoding="utf-8")

    def test_overview_delegates_to_service(self):
        self.assertIn("statistics_service.build_overview(db, days=days)", self.router)

    def test_buggy_attribute_access_removed(self):
        # P1-2 根因：对 dict 行取属性
        self.assertNotIn("users_by_id.get(r.user_id)", self.router)
        self.assertNotIn("user = users_by_id.get(r.user_id)", self.service)

    def test_row_values_read_as_dict(self):
        self.assertIn('r["user_id"]', self.service)
        self.assertIn('r["content"]', self.service)

    def test_router_no_longer_inlines_row_loop(self):
        # 路由里不再内联最近动态循环（该逻辑已下沉到服务层）
        self.assertNotIn('for r in rows[:10]', self.router)


if __name__ == "__main__":
    unittest.main()
