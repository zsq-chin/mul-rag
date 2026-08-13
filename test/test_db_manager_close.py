"""§9.1.4：应用 shutdown 必须释放全部 SQLite 连接（db_manager.close 行为测试）。

db_manager 依赖 src.config（环依赖 + 本机可能缺 Milvus/模型栈），沿用
test_sage_db_path 的轻量 src 桩方式加载；被测点不是桩而是真实 DBManager：
- 打开并关闭会话后，引擎连接池应有连接（证明会话真实建立了连接）；
- close() 后连接池归零（证明 dispose 释放了全部连接）；
- close() 后引擎仍可惰性重连执行查询（证明 dispose 不破坏引擎）；
- close() 幂等，可重复调用不抛异常。

全程使用临时 SAGE_DB_PATH，绝不触碰 saves/data/server.db。
"""

import os
import re
import sys
import tempfile
import types
import unittest

from sqlalchemy import text


def _install_src_shim(save_dir: str) -> None:
    """让 server.db_manager 可在无完整模型栈的主机导入。"""
    if "src" in sys.modules:
        sys.modules.pop("src")
        for key in [k for k in sys.modules if k.startswith("src.")]:
            sys.modules.pop(key)
    src = types.ModuleType("src")
    src.config = types.SimpleNamespace(save_dir=save_dir)
    cfg_mod = types.ModuleType("src.config")
    cfg_mod.save_dir = save_dir
    cfg_mod.get = lambda key, default=None: default
    sys.modules["src"] = src
    sys.modules["src.config"] = cfg_mod

    class _Logger:
        def info(self, *args, **kwargs): pass
        def error(self, *args, **kwargs): pass
        def warning(self, *args, **kwargs): pass
        def debug(self, *args, **kwargs): pass

    utils_mod = types.ModuleType("src.utils")
    utils_mod.logger = _Logger()
    sys.modules["src.utils"] = utils_mod


def _idle_connections(pool) -> int:
    """解析 QueuePool.status() 的 'Connections in pool' 数值（当前空闲可用连接）。"""
    match = re.search(r"Connections in pool: (\d+)", pool.status())
    return int(match.group(1)) if match else -1


class DbManagerCloseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "srv.db")
        _install_src_shim(self.tmp)
        self._old_db_path = os.environ.get("SAGE_DB_PATH")
        os.environ["SAGE_DB_PATH"] = self.db_path
        # 先于任何会话建立前导入：模块级 db_manager 也会指向临时库
        import server.db_manager as db_manager_mod
        self.inst = db_manager_mod.DBManager()

    def tearDown(self):
        if self._old_db_path is None:
            os.environ.pop("SAGE_DB_PATH", None)
        else:
            os.environ["SAGE_DB_PATH"] = self._old_db_path

    def test_close_releases_all_idle_connections(self):
        # 打开并关闭一个会话，让连接回到连接池（idle 应 > 0）
        session = self.inst.get_session()
        session.execute(text("SELECT 1"))
        session.close()
        self.assertGreater(
            _idle_connections(self.inst.engine.pool), 0,
            "会话建连并关闭后，连接池应有空闲连接",
        )
        self.inst.close()
        self.assertEqual(
            _idle_connections(self.inst.engine.pool), 0,
            "close() 后全部 SQLite 连接应释放（空闲池归零）",
        )
        self.assertEqual(
            self.inst.engine.pool.checkedout(), 0,
            "close() 后不应有未归还的连接",
        )

    def test_engine_stays_usable_after_close(self):
        # dispose 只是释放连接，引擎仍可惰性重连（不破坏运行时）
        session = self.inst.get_session()
        session.execute(text("SELECT 1"))
        before = id(session.connection().connection)
        session.close()
        self.inst.close()
        session2 = self.inst.get_session()
        after = id(session2.connection().connection)
        row = session2.execute(text("SELECT 1")).scalar()
        session2.close()
        self.assertEqual(row, 1)
        self.assertNotEqual(
            before, after,
            "close() 后应创建全新的 SQLite 连接（旧连接已释放而非复用）",
        )
        self.inst.close()

    def test_close_is_idempotent(self):
        self.inst.close()
        # 再次调用不抛异常
        self.inst.close()
        self.inst.close()


if __name__ == "__main__":
    unittest.main()
