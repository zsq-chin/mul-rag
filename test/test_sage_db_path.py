"""I2.5：SQLite 路径 SAGE_DB_PATH 覆盖与回退契约（source-level 守卫）。

db_manager 依赖 src.config（环依赖 + Milvus 环境缺失时不可导入），沿用
test_concurrency 对 main.py 的既定 source 断言模式，守护「SAGE_DB_PATH 允许把
SQLite 放到 FUSE bind mount 之外；未设置时保持原行为 saves/data/server.db」：
- 必须读环境变量 SAGE_DB_PATH；
- 必须保留原路径 os.path.join(config.save_dir, "data", "server.db") 作为回退；
- 不得把命名卷路径写死为唯一路径（本机/测试运行仍需走 saves/data）。
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SageDbPathSourceGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "server" / "db_manager.py").read_text(encoding="utf-8")

    def test_db_path_comes_from_env_with_fallback(self):
        self.assertIn('os.environ.get("SAGE_DB_PATH")', self.source)
        self.assertIn('os.path.join(\n            config.save_dir, "data", "server.db"\n        )', self.source)
        self.assertIn("config.save_dir", self.source)

    def test_never_hardcodes_container_path_only(self):
        # 容器内路径只能作为环境值来源，不能写成无条件固定路径
        self.assertNotIn("self.db_path = \"/app/db/server.db\"", self.source)


if __name__ == "__main__":
    unittest.main()
