"""问答统计聚合纯函数测试（不依赖数据库）。"""

import json
import unittest
from datetime import date, datetime

from server.services.statistics_aggregation import (
    aggregate_records,
    build_daily_trend,
    iter_conv_messages,
    normalize_question,
    top_users,
)


def _conv(messages, history=None, title="对话"):
    return json.dumps(
        {"id": "c1", "title": title, "history": history or [], "messages": messages},
        ensure_ascii=False,
    )


def _record(messages, history=None, user_id=1, when=None):
    return {
        "content": _conv(messages, history),
        "updatetime": when or datetime(2026, 8, 1, 10, 0, 0),
        "user_id": user_id,
    }


class NormalizeTests(unittest.TestCase):
    def test_collapses_whitespace(self):
        self.assertEqual(normalize_question(" 新疆  的石油\n储量 "), "新疆 的石油 储量")

    def test_non_string_returns_empty(self):
        self.assertEqual(normalize_question(None), "")
        self.assertEqual(normalize_question(123), "")


class IterConvMessagesTests(unittest.TestCase):
    def test_parses_json_string_messages(self):
        msgs = list(iter_conv_messages(_conv([{"role": "sent", "content": "你好"}, {"role": "received", "content": "你好"}], [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧答案"}])))
        # messages 优先，history 不应混入
        self.assertEqual(msgs, [("sent", "你好"), ("received", "你好")])

    def test_falls_back_to_history_when_messages_empty(self):
        msgs = list(iter_conv_messages(_conv([], [{"role": "user", "content": "历史问题"}])))
        self.assertEqual(msgs, [("user", "历史问题")])

    def test_accepts_parsed_dict(self):
        msgs = list(iter_conv_messages({"messages": [{"role": "sent", "content": "x"}]}))
        self.assertEqual(msgs, [("sent", "x")])

    def test_ignores_bad_input(self):
        self.assertEqual(list(iter_conv_messages("not json")), [])
        self.assertEqual(list(iter_conv_messages([1, 2])), [])
        self.assertEqual(list(iter_conv_messages({"messages": [{"role": "sent"}]})), [])


class AggregateRecordsTests(unittest.TestCase):
    def test_counts_questions_answers_conversations(self):
        records = [
            _record(
                [{"role": "sent", "content": "水力压裂的泵入速率是多少？"}, {"role": "received", "content": "泵入速率通常在 10-15 bbl/min。"}]
            ),
            _record([{"role": "sent", "content": "压裂液配方怎么选？"}]),
        ]
        agg = aggregate_records(records)
        self.assertEqual(agg["totals"]["questions"], 2)
        self.assertEqual(agg["totals"]["answers"], 1)
        self.assertEqual(agg["totals"]["conversations"], 2)
        self.assertEqual(agg["totals"]["users"], 1)

    def test_hot_questions_aggregated_and_normalized(self):
        records = [
            _record([{"role": "sent", "content": "水力压裂  的最优  泵入速率是多少？"}]),
            _record([{"role": "sent", "content": "水力压裂 的最优 泵入速率是多少？"}]),
            _record([{"role": "sent", "content": "压裂液配方选择的关键因素"}]),
        ]
        agg = aggregate_records(records)
        top = agg["hot_questions"]
        self.assertEqual(top[0]["question"], "水力压裂 的最优 泵入速率是多少？")
        self.assertEqual(top[0]["count"], 2)
        self.assertEqual(len(top), 2)

    def test_filters_short_noise(self):
        agg = aggregate_records([_record([{"role": "sent", "content": "好的"}])])
        self.assertEqual(agg["hot_questions"], [])
        self.assertEqual(agg["totals"]["questions"], 1)

    def test_per_user_tracking(self):
        records = [
            _record([{"role": "sent", "content": "问题A的定义是什么"}], user_id=1),
            _record([{"role": "sent", "content": "问题B的原理是什么"}, {"role": "received", "content": "答"}], user_id=1),
            _record([{"role": "sent", "content": "问题C的参数是什么"}], user_id=2),
        ]
        agg = aggregate_records(records)
        self.assertEqual(agg["per_user"][1]["records"], 2)
        self.assertEqual(agg["per_user"][1]["questions"], 2)
        self.assertEqual(agg["per_user"][2]["records"], 1)
        self.assertEqual(agg["per_user"][2]["questions"], 1)
        self.assertEqual(agg["totals"]["users"], 2)

    def test_date_attribution(self):
        records = [
            _record([{"role": "sent", "content": "问题"}, {"role": "received", "content": "答"}], when=datetime(2026, 8, 1, 9, 0)),
            _record([{"role": "sent", "content": "问题2"}], when=datetime(2026, 8, 2, 9, 0)),
        ]
        agg = aggregate_records(records)
        self.assertEqual(agg["questions_by_date"]["2026-08-01"], 1)
        self.assertEqual(agg["answers_by_date"]["2026-08-01"], 1)
        self.assertEqual(agg["conversations_by_date"]["2026-08-02"], 1)


class BuildDailyTrendTests(unittest.TestCase):
    def test_fills_missing_days_with_zero(self):
        agg = {
            "questions_by_date": {"2026-08-01": 5},
            "answers_by_date": {},
            "conversations_by_date": {"2026-08-01": 1},
        }
        trend = build_daily_trend(agg, days=3, today=date(2026, 8, 3))
        self.assertEqual(len(trend), 3)
        self.assertEqual(trend[0]["date"], "2026-08-01")
        self.assertEqual(trend[0]["questions"], 5)
        self.assertEqual(trend[1]["questions"], 0)
        self.assertEqual(trend[2]["date"], "2026-08-03")


class TopUsersTests(unittest.TestCase):
    def test_merges_username_and_sorts(self):
        agg = {
            "per_user": {
                1: {"records": 3, "questions": 5},
                2: {"records": 9, "questions": 2},
                3: {"records": 1, "questions": 1},
            }
        }
        users_by_id = {
            1: type("U", (), {"username": "张三"})(),
            2: type("U", (), {"username": "李四"})(),
        }
        rows = top_users(agg, users_by_id)
        # 按 questions 倒序：张三(5) > 李四(2) > 用户3(1)
        self.assertEqual([r["username"] for r in rows], ["张三", "李四", "用户3"])
        self.assertEqual(rows[0]["questions"], 5)


if __name__ == "__main__":
    unittest.main()
