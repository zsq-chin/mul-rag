#!/usr/bin/env python3
"""I2.3 SQLite 持久化验证脚本：容器重建/升级后关键数据是否仍在。

在运行中的 API 容器内执行（或对任意 SQLite 路径执行）：
    docker compose exec api python scripts/verify_sqlite_persistence.py
或指定路径：
    python scripts/verify_sqlite_persistence.py --db /app/db/server.db

校验：users / chat_records / user_model_credentials / config_change_history /
knowledge_governance 五类关键表都必须存在且至少 1 行（治理数据来自已同步文档）。
任一缺失或为空 → 退出码 1 并输出明确报告。只 stdlib。
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

# 容器/Windows 下统一 UTF-8 输出，避免按 ANSI 编码打印中文产生乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# (表, 说明)。治理数据以 knowledge_governance 为代表（来源为已同步文档）。
KEY_TABLES = (
    ("users", "用户"),
    ("chat_records", "聊天"),
    ("user_model_credentials", "模型凭据"),
    ("config_change_history", "配置历史"),
    ("knowledge_governance", "治理数据"),
)


def check(db_path):
    if not Path(db_path).exists():
        return False, f"数据库不存在: {db_path}"
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            if not ok:
                return False, f"PRAGMA integrity_check 失败: {db_path}"
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            report = []
            all_ok = True
            for table, label in KEY_TABLES:
                if table not in tables:
                    report.append(f"  [缺失] {label}({table}): 表不存在")
                    all_ok = False
                    continue
                n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                status = "OK" if n >= 1 else "空"
                report.append(f"  [{status}] {label}({table}): {n} 行")
                if n < 1:
                    all_ok = False
            return all_ok, "\n".join(report)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, f"读取 {db_path} 失败: {exc}"


def main():
    parser = argparse.ArgumentParser(description="I2.3 SQLite 持久化验证")
    default = os.environ.get("SAGE_DB_PATH", "saves/data/server.db")
    parser.add_argument("--db", default=default, help="SQLite 路径（默认取 SAGE_DB_PATH）")
    args = parser.parse_args()
    ok, report = check(args.db)
    print(f"数据库: {args.db}")
    print(report)
    if ok:
        print("[done] 关键数据均存在，持久化验证通过")
        return 0
    print("[fail] 关键数据缺失或为空，持久化验证失败")
    return 1


if __name__ == "__main__":
    sys.exit(main())
