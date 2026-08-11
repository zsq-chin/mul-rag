"""本机备份、校验、预检与恢复服务（纯服务层，可单元测试）。

设计约束：
- 本模块不得 import `src` / `server.db_manager`（避免触发 Milvus 初始化）。
  数据库路径、备份目录、配置快照、日志路径均由调用方（路由层）注入。
- 使用 SQLite Backup API 生成一致的 server.db 副本，禁止直接复制 WAL。
- ZIP 归档包含：manifest.json、server.db、config.json（脱敏）、可选日志、
  可选白名单根目录内的知识源文件。绝不包含 .env / 私钥 / 凭据文件。
- 恢复流程：校验 ZIP 条目（Zip Slip）→ 校验 SHA-256 与 manifest 版本 →
  磁盘空间检查 → preview 发放一次性令牌 → 正式恢复必须携带令牌 →
  恢复前自动创建恢复点 → 提取到暂存目录后原子替换 → SQLite 恢复失败时
  回滚到恢复点的数据库副本。
"""

import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from server.models.kb_models import KnowledgeFile
from server.models.operations_model import BackupJob
from server.services.config_service import sanitize_config_snapshot

logger = logging.getLogger("sage.backup")

BACKUP_MANIFEST_VERSION = "1"

# 备份归档中绝不打包的敏感文件
_SENSITIVE_FILENAME = re.compile(
    r"(^|[/\\])\.env|\.pem$|\.key$|\.p12$|\.jks$|credentials?\.|\.git/|node_modules/",
    re.IGNORECASE,
)

# Zip Slip 探测：绝对路径、盘符、上级目录、Windows 反斜杠穿越
_TRAVERSAL_RE = re.compile(r"(^([A-Za-z]:)?[/\\])|\.\.[/\\]|\.\.$")


class BackupError(Exception):
    status_code = 400


class BackupNotFound(BackupError):
    status_code = 404


# preview 发放的一次性确认令牌：{backup_id: token}
_preview_tokens = {}


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _resolve_within(root, arcname):
    """把 zip 条目路径安全地解析到 root 之下，杜绝 Zip Slip 穿越。"""
    if not isinstance(arcname, str) or not arcname:
        raise BackupError("压缩包存在非法条目名", 400)
    # 先文本级拒绝：绝对路径 / 盘符 / ../ 或 ..\ 穿越
    if _TRAVERSAL_RE.search(arcname) or os.path.isabs(arcname):
        raise BackupError("压缩包条目路径越界（Zip Slip）", 400)
    root_abs = os.path.abspath(os.path.normpath(root))
    dest = os.path.abspath(os.path.join(root_abs, os.path.normpath(arcname)))
    if dest != root_abs and not dest.startswith(root_abs + os.sep):
        raise BackupError("压缩包条目路径越界（Zip Slip）", 400)
    return dest


def _safe_extract(zipf, dest_dir):
    """逐条安全解压到 dest_dir（staging）。返回条目名列表。"""
    names = []
    for info in zipf.infolist():
        arcname = info.filename
        if info.is_dir():
            continue
        target = _resolve_within(dest_dir, arcname)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zipf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
        names.append(arcname)
    return names


def _validate_kb_source(raw, root_abs):
    """校验单条 KnowledgeFile.path：拒绝 URL / 软链接 / 越界；缺失文件返回 None。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    # 拒绝 URL（任意 scheme:// 或以 // 开头）
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", s) or s.startswith("//"):
        raise BackupError(f"知识源文件路径非法（URL）：{s[:80]}", 400)
    p = Path(s)
    check = p if p.is_absolute() else Path(root_abs) / p
    if os.path.islink(check):
        raise BackupError(f"知识源文件为软链接，已拒绝：{s[:80]}", 400)
    real = os.path.realpath(check)
    if real != root_abs and not real.startswith(root_abs + os.sep):
        raise BackupError(f"知识源文件路径越界：{s[:80]}", 400)
    if not os.path.isfile(real):
        return None  # 陈旧记录，跳过而非阻断备份
    return real


def _collect_kb_sources(db, kb_root):
    """从 KnowledgeFile.path 读取真实知识源文件清单（P1-4）。

    不再递归整棵工作目录（如 saves/data，会带上 server.db 等非知识文件），
    只收集 knowledge_files 表登记过的源文件，逐个校验位于受控根目录之内。
    返回 [(arcname, full_path), ...]，arcname 形如 knowledge/<相对路径>。
    """
    if not kb_root:
        return []
    root_abs = os.path.abspath(os.path.normpath(str(kb_root)))
    try:
        rows = db.query(KnowledgeFile).all()
    except Exception as exc:
        logger.warning("读取知识源文件清单失败，忽略知识库备份: %s", exc)
        return []
    files = []
    seen = set()
    for row in rows:
        real = _validate_kb_source(getattr(row, "path", None), root_abs)
        if real is None:
            continue
        rel = os.path.relpath(real, root_abs)
        arcname = os.path.join("knowledge", rel).replace("\\", "/")
        if _SENSITIVE_FILENAME.search(arcname) or arcname in seen:
            continue
        seen.add(arcname)
        files.append((arcname, real))
    return files


def _ensure_disk_space(directory, required):
    try:
        usage = shutil.disk_usage(directory)
    except OSError:
        return
    if usage.free < required:
        raise BackupError(
            "磁盘空间不足：需要约 {} 字节，当前可用 {} 字节".format(required, usage.free), 400
        )


def _sha256_of(path):
    h = __import__("hashlib").sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _serialize_backup(row):
    # path 不出现在 API 响应中（仅内部使用）
    return {
        "id": row.id,
        "filename": row.filename,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "manifest_version": row.manifest_version,
        "status": row.status,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        "note": row.note,
    }


def _read_manifest(zip_path):
    try:
        with zipfile.ZipFile(zip_path) as zf:
            if "manifest.json" not in zf.namelist():
                raise BackupError("压缩包缺少 manifest.json", 400)
            with zf.open("manifest.json") as f:
                manifest = json.loads(f.read().decode("utf-8"))
    except zipfile.BadZipFile:
        raise BackupError("压缩包损坏或不是有效的 ZIP 文件", 400)
    if not isinstance(manifest, dict):
        raise BackupError("manifest 格式非法", 400)
    if str(manifest.get("manifest_version")) != BACKUP_MANIFEST_VERSION:
        raise BackupError("manifest 版本不受支持", 400)
    return manifest


def create_backup(
    db: Session,
    backup_dir,
    db_path,
    config_snapshot,
    log_path=None,
    include_logs=True,
    include_kb=False,
    kb_root=None,
    created_by="",
    note=None,
):
    """创建备份归档并写入 BackupJob 记录。返回 BackupJob 行。"""
    os.makedirs(backup_dir, exist_ok=True)
    if not db_path or not os.path.exists(db_path):
        raise BackupError("数据库文件不存在，无法备份", 400)
    db_size = os.path.getsize(db_path)
    _ensure_disk_space(backup_dir, db_size + 2 * 1024 * 1024)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = "backup_{}_{}.zip".format(stamp, secrets.token_hex(3))
    zip_path = os.path.join(backup_dir, zip_name)

    # 先落库再快照：让备份副本包含本条 BackupJob 元数据，
    # 恢复后 backup_jobs 表不会丢失本记录。
    row = BackupJob(
        filename=zip_name,
        path=zip_name,
        size_bytes=0,
        sha256=None,
        manifest_version=BACKUP_MANIFEST_VERSION,
        status="running",
        created_by=(created_by or "")[:100],
        note=(note or "")[:255] or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    staging = tempfile.mkdtemp(prefix="bk_stage_", dir=backup_dir)
    try:
        # 1) 一致性数据库副本：SQLite Backup API（禁止直接复制 WAL）
        staged_db = os.path.join(staging, "server.db")
        src = sqlite3.connect(db_path)
        try:
            dest = sqlite3.connect(staged_db)
            try:
                src.backup(dest)
            finally:
                dest.close()
        finally:
            src.close()

        entries = [{"path": "server.db", "size": os.path.getsize(staged_db)}]

        # 2) 非秘密系统配置（服务层二次脱敏：备份包内绝不落盘密钥）
        safe_snapshot = sanitize_config_snapshot(config_snapshot or {}, drop_internal=True)
        with open(os.path.join(staging, "config.json"), "w", encoding="utf-8") as f:
            json.dump(safe_snapshot, f, ensure_ascii=False, indent=2)
        entries.append({"path": "config.json", "size": os.path.getsize(os.path.join(staging, "config.json"))})

        # 3) 应用日志（可通过参数关闭）
        if include_logs and log_path and os.path.exists(log_path):
            log_target = os.path.join(staging, "logs", "app.log")
            os.makedirs(os.path.dirname(log_target), exist_ok=True)
            shutil.copyfile(log_path, log_target)
            entries.append({"path": "logs/app.log", "size": os.path.getsize(log_target)})

        # 4) 可选知识源文件：真实清单来自 KnowledgeFile.path（P1-4），
        #    逐个校验位于受控根目录内后再入归档
        kb_size = 0
        kb_files = _collect_kb_sources(db, kb_root) if (include_kb and kb_root) else []
        for arcname, full in kb_files:
            target = os.path.join(staging, *arcname.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(full, target)
            sz = os.path.getsize(target)
            entries.append({"path": arcname, "size": sz})
            kb_size += sz

        manifest = {
            "manifest_version": BACKUP_MANIFEST_VERSION,
            "created_at": _now_iso(),
            "created_by": created_by,
            "include_logs": bool(include_logs),
            "include_kb": bool(include_kb),
            "kb_sources": {"count": len(kb_files), "size_bytes": kb_size},
            "entries": entries,
        }
        with open(os.path.join(staging, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        # 5) 打包（控制条目名，无穿越可能）
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for dirpath, _, filenames in os.walk(staging):
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    arc = os.path.relpath(full, staging).replace("\\", "/")
                    zf.write(full, arc)

        zip_sha = _sha256_of(zip_path)
        row.size_bytes = os.path.getsize(zip_path)
        row.sha256 = zip_sha
        row.status = "completed"
        db.commit()
        db.refresh(row)
        logger.info("backup created: %s sha256=%s", zip_name, zip_sha)
        return row
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def list_backups(db: Session, page=1, page_size=20) -> dict:
    total = db.query(BackupJob).count()
    rows = (
        db.query(BackupJob)
        .order_by(BackupJob.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [_serialize_backup(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def get_backup(db: Session, backup_id: int):
    row = db.query(BackupJob).filter(BackupJob.id == backup_id).first()
    return row if row is not None else None


def _resolve_zip_path(db, backup_id, backup_dir):
    row = get_backup(db, backup_id)
    if row is None:
        raise BackupNotFound("备份记录不存在")
    path = os.path.join(backup_dir, row.path)
    if not os.path.exists(path):
        raise BackupError("备份文件已丢失", 404)
    return row, path


def verify_backup(db: Session, backup_id: int, backup_dir: str):
    """校验归档完整性：SHA-256、manifest 版本、每个条目可读。更新 verified_at。"""
    row, path = _resolve_zip_path(db, backup_id, backup_dir)
    actual = _sha256_of(path)
    if actual != row.sha256:
        raise BackupError("备份校验失败：SHA-256 不匹配（文件被篡改或损坏）", 400)
    _read_manifest(path)  # 版本与结构校验；非法即抛
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
    except zipfile.BadZipFile:
        raise BackupError("备份校验失败：压缩包损坏", 400)
    if bad is not None:
        raise BackupError("备份校验失败：压缩包存在损坏条目 {}".format(bad), 400)
    row.status = "completed"
    row.verified_at = datetime.now()
    db.commit()
    db.refresh(row)
    return _serialize_backup(row)


def _target_for(arcname, targets, kb_target_root):
    if arcname in targets:
        return targets[arcname]
    if arcname.startswith("knowledge/") and kb_target_root:
        return os.path.join(kb_target_root, arcname[len("knowledge/"):])
    return None


def preview_restore(db: Session, backup_id: int, backup_dir: str, targets: dict, kb_target_root=None) -> dict:
    """预检：只读解析归档，返回将新增/覆盖/跳过的内容与一次性确认令牌。"""
    row, path = _resolve_zip_path(db, backup_id, backup_dir)
    manifest = _read_manifest(path)
    entries = manifest.get("entries", [])
    added, overwritten, skipped = [], [], []
    with zipfile.ZipFile(path) as zf:
        for entry in entries:
            arcname = entry.get("path")
            target = _target_for(arcname, targets, kb_target_root)
            if target is None:
                skipped.append(arcname)
                continue
            if not os.path.exists(target):
                added.append(arcname)
            elif os.path.getsize(target) != entry.get("size"):
                overwritten.append(arcname)
            else:
                skipped.append(arcname)

    token = secrets.token_urlsafe(16)
    _preview_tokens[backup_id] = token
    return {
        "backup_id": backup_id,
        "token": token,
        "manifest_version": manifest.get("manifest_version"),
        "created_at": manifest.get("created_at"),
        "added": added,
        "overwritten": overwritten,
        "skipped": skipped,
        "total_entries": len(entries),
    }


def _fmt_db_dt(value):
    """把 ORM 读出的 datetime 转成 sqlite3 可安全写入的字符串；None 原样保留。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)


def _backup_row_meta(row) -> dict:
    """捕获 BackupJob 行的全部可恢复字段（供恢复后重新登记恢复点元数据）。"""
    return {
        "id": row.id,
        "filename": row.filename,
        "path": row.path,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "manifest_version": row.manifest_version,
        "status": row.status,
        "created_by": row.created_by,
        "created_at": _fmt_db_dt(row.created_at),
        "verified_at": _fmt_db_dt(row.verified_at),
        "note": row.note,
    }


def _reinsert_restore_point(db_path, meta):
    """把恢复点的备份记录重新写回恢复后的数据库。

    恢复会把 server.db 整体替换为旧备份快照（其中不含"恢复点"这条新记录），
    因此必须在恢复完成后，用独立 sqlite3 连接把恢复点元数据按原 id 插回，
    否则 restore_point_id 无法被列表/详情/校验接口读取。失败视为恢复不完整，
    交给调用方回滚。
    """
    if not db_path or not meta:
        raise BackupError("恢复点元数据缺失，无法登记", 500)
    cols = (
        "id, filename, path, size_bytes, sha256, manifest_version, status, "
        "created_by, created_at, verified_at, note"
    )
    values = (
        meta["id"], meta["filename"], meta["path"], meta["size_bytes"],
        meta["sha256"], meta["manifest_version"], meta["status"],
        meta["created_by"], meta["created_at"], meta["verified_at"], meta["note"],
    )
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO backup_jobs ({}) VALUES (?,?,?,?,?,?,?,?,?,?,?)".format(cols),
                values,
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise BackupError("恢复点登记写入失败：{}".format(exc), 500)


def _rollback_file(kind, entry, failures):
    """把单个文件目标恢复为快照；目标原本不存在则删除恢复过程中新建的目标。

    任何失败都记入 failures 并继续，绝不静默忽略。
    """
    target = entry["target"]
    try:
        if entry.get("absent"):
            if os.path.lexists(target):
                os.remove(target)
            return
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(entry["snap"], target)
    except Exception as exc:
        failures.append("{}回滚失败：{}".format(kind, exc))


def _rollback_tree(kind, entry, failures):
    """把目录目标整棵恢复为快照；目标原本不存在则删除恢复过程新建的目录。

    任何失败都记入 failures 并继续，绝不静默忽略。
    """
    target = entry["target"]
    try:
        if entry.get("absent"):
            if os.path.isdir(target):
                shutil.rmtree(target)
            return
        if os.path.isdir(target):
            shutil.rmtree(target)
        shutil.copytree(entry["snap"], target)
    except Exception as exc:
        failures.append("{}回滚失败：{}".format(kind, exc))


class _RestoreRollback:
    """恢复回滚日志（P1-4）：修改真实目标前先登记可恢复快照，失败时逆序全量回滚。

    - 数据库快照复用恢复点 zip（创建于任何目标被修改之前，字节级一致）。
    - 配置/日志在各自修改前复制到本日志目录；知识目录整棵复制到快照目录。
      目标原本不存在时记录"应删除"，回滚时删除恢复过程新建的目标。
    - rollback() 按逆序（知识目录→日志→配置→数据库）回滚所有已登记目标；
      任一步失败继续回滚其余目标并汇总失败列表，供调用方报告回滚是否完整。
    """

    def __init__(self, workdir, restore_point_zip):
        self.workdir = workdir
        self.restore_point_zip = restore_point_zip
        self._db = None      # {"zip": ..., "target": ...}
        self._config = None  # {"snap": ...} 或 {"absent": True, "target": ...}
        self._log = None
        self._kb = None
        self.failed_step = None  # 记录当前正在应用的目标，供异常时报告

    def snapshot_db(self, db_path):
        # 快照 = 恢复点 zip 内的 server.db（恢复前未被任何目标修改所影响）
        self._db = {"zip": self.restore_point_zip, "target": db_path}

    def _snap_file(self, kind, target):
        if not target:
            return {"absent": True, "target": target}
        if os.path.exists(target):
            snap = os.path.join(self.workdir, "{}.bak".format(kind))
            shutil.copy2(target, snap)
            return {"snap": snap, "target": target}
        return {"absent": True, "target": target}

    def snapshot_config(self, config_target):
        self._config = self._snap_file("config", config_target)

    def snapshot_log(self, log_target):
        self._log = self._snap_file("log", log_target)

    def snapshot_kb(self, kb_root):
        if kb_root and os.path.isdir(kb_root):
            snap = os.path.join(self.workdir, "kb_snapshot")
            shutil.copytree(kb_root, snap)
            self._kb = {"snap": snap, "target": kb_root}
        else:
            self._kb = {"absent": True, "target": kb_root}

    def rollback(self):
        """逆序回滚已登记目标。返回 (complete: bool, failures: list[str])。"""
        failures = []
        if self._kb is not None:
            _rollback_tree("知识目录", self._kb, failures)
        if self._log is not None:
            _rollback_file("日志", self._log, failures)
        if self._config is not None:
            _rollback_file("配置", self._config, failures)
        if self._db is not None:
            try:
                _rollback_db(self._db["zip"], self._db["target"])
            except Exception as exc:
                failures.append("数据库回滚失败：{}".format(exc))
        return (not failures, failures)


def restore_backup(
    db: Session,
    backup_id: int,
    token: str,
    backup_dir: str,
    db_path: str,
    config_target: str,
    log_target: str = None,
    kb_target_root: str = None,
    config_snapshot=None,
    log_path=None,
    kb_root=None,
    created_by="",
) -> dict:
    """正式恢复：校验一次性令牌 → 创建恢复点 → 空间检查 → 安全解压到暂存 →
    原子替换 → SQLite 校验，失败则回滚到恢复点的数据库副本。"""
    row, path = _resolve_zip_path(db, backup_id, backup_dir)

    # 1) 一次性确认令牌
    expected = _preview_tokens.pop(backup_id, None)
    if not token or expected is None or secrets.compare_digest(str(token), str(expected)) is False:
        raise BackupError("确认令牌无效或已使用，请重新执行恢复预检", 400)

    manifest = _read_manifest(path)
    targets = {"server.db": db_path, "config.json": config_target}
    if log_target:
        targets["logs/app.log"] = log_target

    journal = None
    try:
        # 2) 恢复前自动创建恢复点（先取 ID 与路径，后续恢复会改写数据库，ORM 对象会失效）
        restore_point = create_backup(
            db,
            backup_dir,
            db_path,
            config_snapshot or {},
            log_path=log_path,
            include_logs=True,
            include_kb=bool(kb_root),
            kb_root=kb_root,
            created_by=created_by,
            note="restore point before restore #{}".format(backup_id),
        )
        restore_point_id = restore_point.id
        restore_point_zip = os.path.join(backup_dir, restore_point.path)
        # 恢复会覆盖数据库，必须先捕获恢复点记录本身，恢复完成后按原 id 插回
        restore_point_meta = _backup_row_meta(restore_point)

        # 3) 磁盘空间检查（估算解压后大小）
        _ensure_disk_space(backup_dir, os.path.getsize(path) * 2 + 1024 * 1024)

        # 4) 解压到暂存目录（Zip Slip 防护），先不触碰真实文件
        staging = tempfile.mkdtemp(prefix="bk_restore_", dir=backup_dir)
        try:
            with zipfile.ZipFile(path) as zf:
                _safe_extract(zf, staging)

            staged_db = os.path.join(staging, "server.db")
            # 5) 校验解压出的数据库副本（此时仍未修改任何真实目标）
            if not os.path.exists(staged_db):
                raise BackupError("备份缺少 server.db，无法恢复", 400)
            if not _check_sqlite_ok(staged_db):
                raise BackupError("备份中的 server.db 已损坏", 400)

            # 6) 回滚日志：从这一步起修改真实目标。每个目标修改前先登记
            #    可恢复快照，任一步失败都按逆序（知识目录→日志→配置→数据库）
            #    全量回滚，并报告失败步骤与回滚是否完整。
            rollback_dir = tempfile.mkdtemp(prefix="bk_rollback_", dir=backup_dir)
            journal = _RestoreRollback(rollback_dir, restore_point_zip)

            # 6.1) 数据库：通过 SQLite Backup API 写入活动数据库
            journal.failed_step = "数据库"
            journal.snapshot_db(db_path)
            if db_path and os.path.dirname(db_path):
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
            _restore_into(db_path, staged_db)

            # 6.2) 配置
            journal.failed_step = "配置"
            journal.snapshot_config(config_target)
            _apply_staged_file(os.path.join(staging, "config.json"), config_target)

            # 6.3) 日志
            journal.failed_step = "日志"
            journal.snapshot_log(log_target)
            if log_target and os.path.exists(os.path.join(staging, "logs", "app.log")):
                os.makedirs(os.path.dirname(log_target), exist_ok=True)
                shutil.copyfile(os.path.join(staging, "logs", "app.log"), log_target)

            # 6.4) 知识目录
            journal.failed_step = "知识目录"
            journal.snapshot_kb(kb_target_root)
            if kb_target_root:
                _apply_kb_tree(os.path.join(staging, "knowledge"), kb_target_root)

            # 7) 恢复点登记：数据库已被旧快照覆盖，把恢复点记录按原 id 重新写回，
            #    保证 restore_point_id 在恢复后可被列表/详情/校验接口读取（含重启后）。
            journal.failed_step = "恢复点登记"
            _reinsert_restore_point(db_path, restore_point_meta)

            # 8) 完成
            row.status = "completed"
            row.note = "restored at {}".format(_now_iso())
            db.commit()
            db.refresh(row)
            return {"backup_id": backup_id, "restore_point_id": restore_point_id, "status": "completed"}
        except Exception as exc:
            if journal is not None:
                complete, failures = journal.rollback()
                step = journal.failed_step or "未知步骤"
                detail = "已回滚至恢复前状态" if complete else "回滚不完整：{}".format("；".join(failures))
                logger.exception(
                    "restore failed at %s; rollback %s", step,
                    "complete" if complete else "incomplete",
                )
                raise BackupError(
                    "恢复失败（{}）：{}；{}".format(step, exc, detail), 500
                ) from exc
            raise
        finally:
            if journal is not None:
                shutil.rmtree(journal.workdir, ignore_errors=True)
            shutil.rmtree(staging, ignore_errors=True)
    except BackupError:
        raise
    except Exception as e:
        logger.exception("restore failed for backup_id=%s", backup_id)
        raise BackupError("恢复失败：{}".format(e), 500)


def _check_sqlite_ok(db_path):
    """用只读方式打开并跑 PRAGMA integrity_check 快速校验。"""
    if not os.path.exists(db_path):
        return False
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row) and row[0] == "ok"
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _restore_into(target_path, source_path):
    """用 SQLite Backup API 把 source 数据库的内容写入 target 活动数据库。

    替代文件级 os.replace：活动库被 SQLAlchemy 池占用时也能安全写入
    （Windows 上无法替换被打开的文件），且写入发生在目标库的单个事务内。
    """
    src = sqlite3.connect(source_path)
    try:
        dest = sqlite3.connect(target_path)
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()
    if not _check_sqlite_ok(target_path):
        raise BackupError("恢复后的数据库校验失败", 500)


def _rollback_db(restore_point_zip, db_path):
    """从恢复点 zip 恢复数据库；失败抛 BackupError（不允许静默忽略回滚失败）。"""
    if not restore_point_zip or not os.path.exists(restore_point_zip):
        raise BackupError("恢复点备份缺失，无法回滚数据库", 500)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmpf:
            tmp_path = tmpf.name
        with zipfile.ZipFile(restore_point_zip) as zf:
            with zf.open("server.db") as src, open(tmp_path, "wb") as out:
                shutil.copyfileobj(src, out)
        _restore_into(db_path, tmp_path)
        logger.info("database rolled back from restore point")
    except Exception as exc:
        raise BackupError("数据库回滚失败：{}".format(exc), 500) from exc
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _apply_staged_file(staged, target):
    if not staged or not target:
        return
    if not os.path.exists(staged):
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copyfile(staged, target)


def _apply_kb_tree(staged_kb, target_root):
    if not os.path.isdir(staged_kb):
        return
    os.makedirs(target_root, exist_ok=True)
    for dirpath, _, filenames in os.walk(staged_kb):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, staged_kb)
            dest = _resolve_within(target_root, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(full, dest)


def delete_backup(db: Session, backup_id: int, backup_dir: str):
    row, path = _resolve_zip_path(db, backup_id, backup_dir)
    try:
        os.remove(path)
    except OSError:
        pass
    _preview_tokens.pop(backup_id, None)
    db.delete(row)
    db.commit()
    return {"deleted": True, "backup_id": backup_id}
