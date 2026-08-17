"""SQLite 兼容列迁移：模型新增列时自动 ALTER TABLE 补齐。

背景：`Base.metadata.create_all` 只创建不存在的表，不会给已存在的表加新列。
历史数据库中缺失模型新列（如 config_change_history.description）会导致接口 500。

本模块不依赖 src / db_manager，可独立测试；由 DBManager.create_tables 调用。
幂等：缺什么补什么；不删除历史遗留的多余列；并发补充失败时跳过。
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.sql.schema import MetaData

logger = logging.getLogger("sage.db-column-migration")


def ensure_missing_columns(engine: Engine, metadata: MetaData) -> int:
    """按模型元数据补齐所有已存在表的缺失列，返回补充的列数。"""
    inspector = inspect(engine)
    added = 0
    with engine.begin() as conn:
        # 遍历无序表集合即可（只做 ALTER ADD COLUMN，不依赖表间依赖顺序，
        # 也避免 sorted_tables 对循环外键关系的告警）
        for table in list(metadata.tables.values()):
            if not inspector.has_table(table.name):
                continue
            actual = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in actual:
                    continue
                coltype = column.type.compile(dialect=engine.dialect)
                nullable = "" if column.nullable else " NOT NULL"
                default_clause = ""
                if (
                    column.default is not None
                    and getattr(column.default, "is_scalar", False)
                    and column.default.arg is not None
                ):
                    default_clause = f" DEFAULT {column.default.arg}"
                ddl = (
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" '
                    f"{coltype}{nullable}{default_clause}"
                )
                try:
                    conn.execute(text(ddl))
                    added += 1
                    logger.warning("已为表 %s 补充缺失列: %s (%s)", table.name, column.name, coltype)
                except Exception as exc:  # 其他进程已并发补充时幂等跳过
                    logger.warning("补充列失败（可能已存在）: %s: %s", column.name, exc)
    return added
