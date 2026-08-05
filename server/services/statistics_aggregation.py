"""问答统计聚合逻辑（纯函数，不依赖数据库，便于单元测试）。

数据来源是 chat_records 表，每条记录 content 为 JSON 字符串，结构为对话对象：
    {"id": "...", "title": "...", "history": [{"role": "user"|"assistant", "content": "..."}],
     "messages": [{"role": "sent"|"received"|"assistant", "content": "..."}]}

本模块只做「解析 + 计数 + 排序」，统计接口在 statistics_router 里把数据库行喂进来。
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Iterator

# 用户提问/助手回答对应的 role（覆盖 messages 与 history 两种结构）
USER_ROLES = {"sent", "user", "human"}
ASSISTANT_ROLES = {"received", "assistant", "ai"}

# 过滤掉过短/过长的噪声问题（如「好的」「谢谢」等）
MIN_QUESTION_LEN = 4
MAX_QUESTION_LEN = 120

DEFAULT_DAYS = 14
MAX_HOT_QUESTIONS = 10
MAX_TOP_USERS = 10

# 早期 seed 进 questions 表的演示数据标题，sync-questions 时会清理（无讨论/求助关联时）
MOCK_SEED_TITLES = (
    "水力压裂的最优泵入速率是多少？",
    "压裂液配方选择的关键因素",
    "支撑剂粒度对压裂效果的影响",
    "压裂污水处理与回收技术",
    "压裂诱导裂缝方向控制",
    "多段塞式压裂设计方案",
    "压裂参数优化与产能预测",
    "页岩气压裂工艺技术对比",
)


def normalize_question(text: Any) -> str:
    """归一化问题文本：折叠空白后去首尾空白，供统计去重使用。"""
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip()


def iter_conv_messages(content: Any) -> Iterator[tuple[str, str]]:
    """从对话记录 content 中迭代出 (role, content) 对，容忍各种脏数据。

    content 可以是已解析的 dict，也可以是 JSON 字符串。
    优先解析 messages；若缺失或为空，回退到 history。
    """
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (ValueError, TypeError):
            return
    if not isinstance(content, dict):
        return

    msgs = content.get("messages")
    if not isinstance(msgs, list) or not msgs:
        msgs = content.get("history") or []
    if not isinstance(msgs, list):
        return

    for msg in msgs:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "")).lower()
        text = msg.get("content")
        if not text:
            continue
        yield role, text


def aggregate_records(records: Iterable[dict]) -> dict:
    """聚合一组对话记录（list[dict]，含 content/updatetime/user_id）。

    返回:
    {
      "totals": {"questions": int, "answers": int, "conversations": int, "users": int},
      "questions_by_date": {date_str: int},
      "answers_by_date": {date_str: int},
      "conversations_by_date": {date_str: int},
      "hot_counter": Counter,          # 归一化问题 -> 提问次数
      "hot_questions": [{"question": str, "count": int}, ...]（按次数倒序）
      "per_user": {user_id: {"records": int, "questions": int}},
    }
    """
    records = list(records)

    q_counter: Counter = Counter()
    q_by_date: defaultdict[str, int] = defaultdict(int)
    a_by_date: defaultdict[str, int] = defaultdict(int)
    conv_by_date: defaultdict[str, int] = defaultdict(int)
    per_user: defaultdict[Any, dict] = defaultdict(lambda: {"records": 0, "questions": 0})

    for rec in records:
        if not isinstance(rec, dict):
            continue
        content = rec.get("content")
        ts = rec.get("updatetime")
        uid = rec.get("user_id")
        date_str = ts.strftime("%Y-%m-%d") if isinstance(ts, datetime) else None

        if date_str:
            conv_by_date[date_str] += 1
        if uid is not None:
            per_user[uid]["records"] += 1

        for role, text in iter_conv_messages(content):
            if date_str:
                if role in USER_ROLES:
                    q_by_date[date_str] += 1
                elif role in ASSISTANT_ROLES:
                    a_by_date[date_str] += 1
            if role in USER_ROLES:
                norm = normalize_question(text)
                if MIN_QUESTION_LEN <= len(norm) <= MAX_QUESTION_LEN:
                    q_counter[norm] += 1
                    if uid is not None:
                        per_user[uid]["questions"] += 1

    return {
        "totals": {
            "questions": sum(q_by_date.values()),
            "answers": sum(a_by_date.values()),
            "conversations": len(records),
            "users": len([u for u in per_user if per_user[u]["records"] > 0]),
        },
        "questions_by_date": dict(q_by_date),
        "answers_by_date": dict(a_by_date),
        "conversations_by_date": dict(conv_by_date),
        "hot_counter": q_counter,
        "hot_questions": [
            {"question": q, "count": c} for q, c in q_counter.most_common(MAX_HOT_QUESTIONS)
        ],
        "per_user": {k: dict(v) for k, v in per_user.items()},
    }


def build_daily_trend(
    agg: dict,
    days: int = DEFAULT_DAYS,
    today: date | None = None,
) -> list[dict]:
    """把按日期的计数补成最近 days 天的有序序列，缺失日期补 0。"""
    today = today or date.today()
    start = today - timedelta(days=days - 1)
    rows = []
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        rows.append(
            {
                "date": d,
                "questions": agg["questions_by_date"].get(d, 0),
                "answers": agg["answers_by_date"].get(d, 0),
                "conversations": agg["conversations_by_date"].get(d, 0),
            }
        )
    return rows


def top_users(agg: dict, users_by_id: dict, limit: int = MAX_TOP_USERS) -> list[dict]:
    """把 per_user 统计与用户表信息合并，返回提问/会话最多的用户排行。"""
    rows = []
    for uid, stat in agg["per_user"].items():
        user = users_by_id.get(uid)
        username = user.username if user is not None else f"用户{uid}"
        if user is not None and getattr(user, "username", None):
            username = user.username
        rows.append(
            {
                "user_id": uid,
                "username": username or f"用户{uid}",
                "records": stat["records"],
                "questions": stat["questions"],
            }
        )
    rows.sort(key=lambda r: (r["questions"], r["records"]), reverse=True)
    return rows[:limit]
