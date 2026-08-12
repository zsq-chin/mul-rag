#!/usr/bin/env python3
"""I2.2 SQLite 持久化迁移脚本：saves/data/server.db → 命名卷 /app/db/server.db。

背景：生产 docker-compose.prod.yml 此前用 `volumes: !override` 移除了基础 Compose
新增的 sage_db 命名卷，若 SAGE_DB_PATH=/app/db/server.db 而卷未挂载，数据库会写入
容器临时层，容器重建即丢。本脚本在切换持久化策略前完成一次性迁移。

流程（每步失败即中止，绝不半迁移）：
  1. 备份源库到 {src}.backup-{yyyyMMddHHmmss}；
  2. 校验源库：文件大小、PRAGMA integrity_check、关键表行数；
  3. 用 sqlite3.Connection.backup 做一致性复制到目标（正确处理 WAL 未 checkpoint 数据）；
  4. 校验目标库：integrity_check、关键表行数与源库一致；
  5. 输出迁移报告。

安全约束：
  - 目标已存在且含数据时拒绝覆盖（除非 --force，且仍先备份源库）；
  - 只 stdlib，可在宿主机或容器内运行（python scripts/migrate_sqlite_to_named_volume.py）。

用法：
  python scripts/migrate_sqlite_to_named_volume.py \
      [--src saves/data/server.db] [--dst /app/db/server.db] [--force]
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 容器/Windows 下统一 UTF-8 输出，避免按 ANSI 编码打印中文产生乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 迁移必须校验存在（允许 0 行，但必须存在）的关键表。
KEY_TABLES = (
    "users",
    "chat_records",
    "user_model_credentials",
    "config_change_history",
    "knowledge_governance",
    "knowledge_document_versions",
)


def _replace_file(src_path, dst_path):
    """os.replace 原子替换；Windows 短暂句柄释放延迟时重试。"""
    import time as _time

    last_err = None
    for _attempt in range(30):
        try:
            os.replace(src_path, dst_path)
            return
        except OSError as exc:
            last_err = exc
            _time.sleep(0.1)
    raise last_err


def _table_counts(db_path):
    """返回 {table: row_count}；不存在的表也列出（count=-1）。"""
    counts = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            for table in KEY_TABLES:
                if table in tables:
                    counts[table] = conn.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                else:
                    counts[table] = -1
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise RuntimeError(f"读取 {db_path} 失败: {exc}") from exc
    return counts


def _integrity_ok(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row) and row[0] == "ok"
    finally:
        conn.close()


def _verify(db_path, label, expect_counts=None):
    """校验一个库：大小>0、integrity ok、关键表行数（与 expect_counts 对比）。"""
    if not Path(db_path).exists():
        raise RuntimeError(f"{label} 不存在: {db_path}")
    size = os.path.getsize(db_path)
    if size <= 0:
        raise RuntimeError(f"{label} 大小为 0，拒绝迁移: {db_path}")
    if not _integrity_ok(db_path):
        raise RuntimeError(f"{label} PRAGMA integrity_check 失败: {db_path}")
    counts = _table_counts(db_path)
    for table, count in counts.items():
        if count < 0:
            raise RuntimeError(f"{label} 缺少关键表 {table}: {db_path}")
    if expect_counts is not None:
        for table, expected in expect_counts.items():
            if counts.get(table) != expected:
                raise RuntimeError(
                    f"{label} 关键表 {table} 行数不一致（期望 {expected}，实际 {counts.get(table)}）"
                )
    print(f"[verify] {label}: size={size} bytes")
    for table, count in counts.items():
        print(f"    {table}: {count}")
    return size


def main():
    parser = argparse.ArgumentParser(description="I2.2 SQLite 持久化迁移")
    default_dst = os.environ.get("SAGE_DB_PATH", "/app/db/server.db")
    parser.add_argument("--src", default="saves/data/server.db", help="源 SQLite 路径")
    parser.add_argument("--dst", default=default_dst, help="目标 SQLite 路径（默认取 SAGE_DB_PATH）")
    parser.add_argument("--force", action="store_true", help="目标已有数据时也覆盖（先备份源库）")
    args = parser.parse_args()

    src = Path(args.src).resolve()
    dst = Path(args.dst).resolve()

    if not src.exists():
        print(f"[skip] 源库不存在 {src}，无需迁移（首次部署可直接用命名卷）。")
        return 0
    if src.resolve() == dst.resolve():
        print("[skip] 源与目标相同，无需迁移。")
        return 0

    # 目标已存在且含数据：除非 --force，否则拒绝覆盖。
    if dst.exists():
        if os.path.getsize(dst) > 0 and not args.force:
            print(f"[abort] 目标已存在且含数据 {dst}；拒绝覆盖。如需覆盖请加 --force（源库仍会先备份）。")
            return 2
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)

    # 1) 备份源库
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = src.parent / f"{src.name}.backup-{stamp}"
    shutil.copy2(src, backup_path)
    print(f"[backup] {src} -> {backup_path}")

    # 2) 校验源库
    _verify(str(src), "source")

    # 3) 一致性复制（VACUUM INTO：读事务覆盖 WAL 未 checkpoint 数据，
    #    由 SQLite 自身写目标文件，不残留 Python 句柄——Windows 上 rename 不会被锁）
    tmp_dst = dst.with_name(dst.name + ".migrating")
    if tmp_dst.exists():
        tmp_dst.unlink()
    try:
        with sqlite3.connect(str(src), timeout=30) as src_conn:
            src_conn.execute("VACUUM INTO ?", (str(tmp_dst),))
        # 复制完成后再校验一次目标临时库
        _verify(str(tmp_dst), "target(tmp)", expect_counts=_table_counts(str(src)))
        # 原子切换：os.replace 在 Windows/POSIX 上都会替换既有目标，避免读到半文件
        _replace_file(tmp_dst, dst)
    except Exception:
        try:
            tmp_dst.unlink()
        except OSError:
            pass
        raise

    # 4) 校验最终目标
    _verify(str(dst), "target")
    print(f"[done] 迁移完成: {src} -> {dst}")
    print(f"[done] 备份保留在 {backup_path}（校验后再决定是否删除）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
