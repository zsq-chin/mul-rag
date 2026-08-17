"""回归：列迁移——已有旧 schema 的表在启动时自动补齐模型新增列。"""

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine

from server.db_column_migration import ensure_missing_columns
from server.models import Base
import server.models.operations_model  # noqa: F401
import server.models.knowledge_dictionary_models  # noqa: F401


class ColumnMigrationTest(unittest.TestCase):
    def test_missing_columns_are_added_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'server.db'}")
            # 1) 先建"旧版"表：只有旧列
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    "CREATE TABLE config_change_history ("
                    "id INTEGER PRIMARY KEY, config_key VARCHAR(255), "
                    "before_value TEXT, after_value TEXT, operator VARCHAR(100), "
                    "note VARCHAR(500), created_at DATETIME)"
                )
            # 2) 正常启动流程：create_all + 列补齐
            Base.metadata.create_all(engine)
            added = ensure_missing_columns(engine, Base.metadata)
            self.assertGreaterEqual(added, 4)
            # 3) 验证列已补齐且旧列保留
            with engine.connect() as conn:
                actual = {
                    r[1]
                    for r in conn.exec_driver_sql(
                        'PRAGMA table_info("config_change_history")'
                    ).fetchall()
                }
            for name in ("description", "changes", "before_snapshot", "after_snapshot"):
                self.assertIn(name, actual)
            for name in ("config_key", "before_value", "after_value", "note"):
                self.assertIn(name, actual)
            # 4) 幂等：再次执行不重复添加
            self.assertEqual(ensure_missing_columns(engine, Base.metadata), 0)
            engine.dispose()

    def test_fresh_database_needs_no_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'server.db'}")
            Base.metadata.create_all(engine)
            self.assertEqual(ensure_missing_columns(engine, Base.metadata), 0)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
