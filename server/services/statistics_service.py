"""问答统计聚合服务（纯服务层，可注入临时 SQLite 做真实数据行为测试）。

不依赖 `server.db_manager`，避免在单元测试中触发 `src`/Milvus 导入链；
路由层只做 HTTP 包装，行为验证在服务层用真实 ChatRecord 完成。
"""

import json
from collections import Counter

from server.models.chat_model import ChatRecord
from server.models.thread_model import Thread
from server.models.user_model import User
from server.services import feedback_service
from server.services.statistics_aggregation import (
    aggregate_records,
    build_daily_trend,
    top_users,
)


def chat_record_rows(db) -> list[dict]:
    """把 chat_records 表转成聚合函数需要的 dict 列表（按保存时间倒序）。"""
    records = db.query(ChatRecord).order_by(ChatRecord.updatetime.desc()).all()
    return [
        {"content": r.content, "updatetime": r.updatetime, "user_id": r.user_id}
        for r in records
    ]


def build_overview(db, days: int = 14) -> dict:
    """基于 chat_records / thread 的真实问答数据，返回统计面板所需的全部数据。

    与 /statistics/overview 返回体完全一致：既有 totals/daily_trend/agent_distribution/
    hot_questions/top_users/recent_activity 字段，同时包含来自 answer_feedback
    的真实反馈指标。任何单条记录异常（缺失用户 / 异常 JSON）都不影响整体返回。
    """
    rows = chat_record_rows(db)
    agg = aggregate_records(rows)

    threads = db.query(Thread).filter(Thread.status == 1).all()
    agent_counter = Counter((t.agent_id or "未知智能体") for t in threads)

    users = db.query(User).all()
    users_by_id = {u.id: u for u in users}

    # 最近动态：最近保存的对话
    recent_activity = []
    for r in rows[:10]:
        user = users_by_id.get(r["user_id"])
        title = ""
        try:
            conv = json.loads(r["content"]) if r["content"] else {}
            if isinstance(conv, dict):
                title = conv.get("title", "") or ""
        except (ValueError, TypeError):
            title = ""
        recent_activity.append(
            {
                "time": r["updatetime"].strftime("%Y-%m-%d %H:%M") if r["updatetime"] else "",
                "username": user.username if user and user.username else f"用户{r['user_id']}",
                "title": title,
            }
        )

    totals = agg["totals"]
    totals["threads"] = len(threads)
    totals["active_users"] = len({r["user_id"] for r in rows})

    return {
        "totals": totals,
        "daily_trend": build_daily_trend(agg, days=days),
        "agent_distribution": [
            {"name": name, "value": count}
            for name, count in agent_counter.most_common()
        ],
        "hot_questions": agg["hot_questions"],
        "top_users": top_users(agg, users_by_id),
        "recent_activity": recent_activity,
        # 反馈指标：严格来自 answer_feedback 真实表；删除反馈后统计同步变化
        "feedback": feedback_service.summarize(db),
    }
