"""本机系统监控服务（纯服务层，可单元测试）。

设计约束：
- 本模块不得 import `src` / `server.db_manager`（避免触发 Milvus 等初始化）。
  数据库路径、备份目录、各依赖的连接参数均由路由层注入。
- 依赖检查项相互独立：单项失败只标记该项状态，不拖垮整个接口。
- 每个检查项有独立超时；Milvus / Neo4j 采用惰性导入客户端库 + 可注入
  probe 函数，测试可注入伪 probe 覆盖“成功 / 超时 / 拒绝连接 / 不可用”四类场景。
- GPU 不存在（无 NVIDIA 驱动或 nvidia-smi 不可用）时返回 `unavailable`，绝不抛 500。
- 远端多模态单独上报（I3.3）：`healthy` / `degraded` / `down` / `unavailable`（未配置）。
  远端离线只影响本项状态，绝不进入本地聚合、绝不拖垮普通聊天流量。

状态值约定（本地项）：`ok` / `failed` / `timeout` / `unavailable`；
远端多模态项：`healthy` / `degraded` / `down` / `unavailable`。
聚合状态：本地项全部 `ok` 时为 `ok`，否则为 `degraded`（接口仍返回 200）。
远端多模态状态不计入本地聚合。
"""

import os
import shutil
import sqlite3
import subprocess
from sqlalchemy.orm import Session

from server.models.operations_model import BackupJob, AlertEvent

# 网络/进程探针的错误消息命中这些关键词时，归类为“超时”而非“失败”
_TIMEOUT_HINTS = ("timeout", "timed out", "deadline", "超时")


def _classify_error(exc):
    msg = str(exc).lower()
    return "timeout" if any(hint in msg for hint in _TIMEOUT_HINTS) else "failed"


def _overall(checks):
    # 只有 “failed / timeout” 才算降级；GPU 不存在、暂无备份/告警等
    # “unavailable” 属于信息性状态，不算本机系统故障。
    if any(c.get("status") in ("failed", "timeout") for c in checks.values()):
        return "degraded"
    return "ok"


def check_sqlite(db_path, timeout=2.0):
    """SQLite 可读、可写状态与文件大小。写探针在事务内建表→删表→回滚，不污染数据。"""
    result = {"status": "failed", "size_bytes": None, "read_ok": False, "write_ok": False}
    if not db_path or not os.path.exists(db_path):
        result["detail"] = "数据库文件不存在"
        return result
    try:
        result["size_bytes"] = os.path.getsize(db_path)
    except OSError:
        pass
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=timeout)
        conn.execute("PRAGMA busy_timeout={}".format(int(timeout * 1000)))
        conn.execute("SELECT 1").fetchone()
        result["read_ok"] = True
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE _monitor_write_probe_(id INTEGER)")
        conn.execute("DROP TABLE _monitor_write_probe_")
        conn.execute("ROLLBACK")
        result["write_ok"] = True
        result["status"] = "ok"
    except sqlite3.OperationalError as exc:
        result["detail"] = "只读或数据库锁定: {}".format(exc)[:200]
    except Exception as exc:  # noqa: BLE001
        result["detail"] = str(exc)[:200]
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return result


def check_disk(path):
    """磁盘总量 / 已用 / 剩余。"""
    try:
        usage = shutil.disk_usage(path)
        total, used, free = usage.total, usage.used, usage.free
        used_percent = round(used / total * 100, 1) if total else 0.0
        return {
            "status": "ok",
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "used_percent": used_percent,
        }
    except OSError as exc:
        return {"status": "failed", "detail": str(exc)[:200]}


def check_backup_dir(path):
    """备份目录可写状态（写入临时探针文件后删除）。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_probe")
        with open(probe, "wb") as f:
            f.write(b"ok")
        os.remove(probe)
        return {"status": "ok", "writable": True}
    except OSError as exc:
        return {"status": "failed", "writable": False, "detail": str(exc)[:200]}


def check_gpu(timeout=3.0):
    """GPU 探测：nvidia-smi 查询利用率与显存。不存在时返回 unavailable。"""
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {"status": "unavailable", "available": False, "detail": "未检测到 NVIDIA 驱动"}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "available": False, "detail": "nvidia-smi 执行超时"}
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "nvidia-smi 执行失败").strip()[:200]
        return {"status": "unavailable", "available": False, "detail": tail}
    lines = [ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return {"status": "unavailable", "available": False}
    parts = [p.strip() for p in lines[0].split(",")]
    try:
        util = int(parts[0])
        vram_used = int(parts[1])
        vram_total = int(parts[2])
    except (ValueError, IndexError):
        return {"status": "failed", "available": True, "detail": "无法解析 nvidia-smi 输出"}
    return {
        "status": "ok",
        "available": True,
        "utilization_percent": util,
        "vram_used_mb": vram_used,
        "vram_total_mb": vram_total,
        "vram_used_percent": round(vram_used / vram_total * 100, 1) if vram_total else 0.0,
    }


def _default_milvus_probe(uri, timeout=3.0):
    from pymilvus import MilvusClient

    client = MilvusClient(uri=uri, timeout=timeout)
    client.list_collections()
    return True


def check_milvus(uri, timeout=3.0, probe=None):
    """Milvus 连接检查。probe 可注入，默认惰性导入 pymilvus 客户端。"""
    probe = probe or _default_milvus_probe
    try:
        probe(uri, timeout)
        return {"status": "ok", "detail": "connected"}
    except Exception as exc:  # noqa: BLE001
        status = _classify_error(exc)
        return {"status": status, "detail": str(exc)[:200]}


def _default_neo4j_probe(uri, username, password, timeout=3.0):
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(uri, auth=(username, password), connection_timeout=timeout)
    try:
        driver.verify_connectivity()
    finally:
        driver.close()
    return True


def check_neo4j(uri, username, password, timeout=3.0, probe=None):
    """Neo4j 连接检查。用户名/密码未配置时直接返回不可用（不视为本机故障）。"""
    if not username or not password:
        return {"status": "unavailable", "detail": "未配置 NEO4J_USERNAME / NEO4J_PASSWORD"}
    probe = probe or _default_neo4j_probe
    try:
        probe(uri, username, password, timeout)
        return {"status": "ok", "detail": "connected"}
    except Exception as exc:  # noqa: BLE001
        status = _classify_error(exc)
        return {"status": status, "detail": str(exc)[:200]}


def _default_multimodal_probe(base_url, timeout=5.0):
    """远端多模态 /health 探针：真实 I/O 超时（I3.3）。

    返回 (reachable, status_code, payload)；连接失败/超时抛异常（由调用方归为 down）。
    惰性导入 requests，避免监控模块顶层依赖网络库。
    """
    import requests

    url = f"{str(base_url).rstrip('/')}/health"
    response = requests.get(url, timeout=timeout)
    payload = {}
    if "application/json" in response.headers.get("content-type", ""):
        try:
            payload = response.json()
        except ValueError:
            payload = {}
    return True, response.status_code, payload


def check_multimodal(timeout=5.0, probe=None, base_url=None):
    """远端多模态依赖状态（I3.3）。

    - 未配置目标（MULTIMODAL_ENABLED=false 或未注入 Base URL）→ `unavailable`，
      不发起网络请求，也不视为本机故障；
    - 可达且 /health 返回 ok:true → `healthy`；
    - 可达但 HTTP >= 400 或 /health 未返回 ok → `degraded`；
    - 连接拒绝 / 超时 / 异常 → `down`。

    远端故障只影响本项状态，绝不使普通聊天退出流量。
    probe 可注入：probe(base_url, timeout) -> (reachable, status_code, payload)。
    """
    if base_url is None:
        try:
            from server.utils import multimodal_remote

            base_url = multimodal_remote.get_multimodal_api_base()
        except Exception:  # noqa: BLE001 —— 配置非法按未启用处理
            base_url = None
    if not base_url:
        return {"status": "unavailable", "detail": "未配置远端多模态 Base URL（多模态未启用）"}

    probe = probe or _default_multimodal_probe
    try:
        reachable, status_code, payload = probe(base_url, timeout)
    except Exception as exc:  # noqa: BLE001
        return {"status": "down", "detail": str(exc)[:200]}

    if not reachable:
        return {"status": "down", "detail": "远端多模态不可达"}
    if status_code is not None and status_code >= 400:
        return {"status": "degraded", "detail": f"远端多模态 /health HTTP {status_code}"}
    ok = payload.get("ok") if isinstance(payload, dict) else None
    if ok is not True:
        return {"status": "degraded", "detail": "远端多模态 /health 未返回 ok:true"}
    return {"status": "healthy", "detail": "远端多模态 /health ok"}


def check_multimodal_observability(now=None, window_seconds=None, timeout=5.0, probe=None, base_url=None, probe_result=None):
    """J.6 多模态运行健康汇总：探针可达性 + 熔断状态 + 指标窗口。

    供依赖页展示与告警规则评估；probe 可注入，离线可单测。
    ``probe_result`` 可复用 dependencies() 已执行一次的探针结果，避免重复网络请求。
    返回 dict 同时含 ``status``（healthy/degraded/down/unavailable）与
    multimodal_ops.degraded_summary() 的窗口指标（错误率/p95/超时/图片字节/池耗尽/
    查询扩展预算耗尽）。
    """
    from server.utils import multimodal_ops

    if probe_result is None:
        probe_result = check_multimodal(timeout=timeout, probe=probe, base_url=base_url)
    summary = multimodal_ops.degraded_summary(now=now, window_seconds=window_seconds)
    summary["reachability"] = probe_result["status"]
    summary["reachability_detail"] = probe_result["detail"]

    if probe_result["status"] == "unavailable":
        summary["status"] = "unavailable"
        summary["detail"] = probe_result["detail"]
        return summary
    if probe_result["status"] == "down":
        summary["status"] = "down"
        summary["detail"] = probe_result["detail"]
        return summary
    if summary["breaker_state"] in ("open", "half_open"):
        summary["status"] = "degraded"
        summary["detail"] = f"熔断状态 {summary['breaker_state']}，多模态请求已自动降级"
        return summary
    if probe_result["status"] == "degraded":
        summary["status"] = "degraded"
        summary["detail"] = probe_result["detail"]
        return summary
    summary["status"] = "healthy"
    summary["detail"] = "远端多模态 /health ok，熔断关闭"
    return summary


def last_backup(db):
    """最近一次备份结果。"""
    row = db.query(BackupJob).order_by(BackupJob.id.desc()).first()
    if row is None:
        return {"status": "unavailable", "detail": "尚无备份"}
    return {
        "status": "ok" if row.status == "completed" else "failed",
        "backup_id": row.id,
        "filename": row.filename,
        "size_bytes": row.size_bytes,
        "job_status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def last_alert(db):
    """最近一次告警结果。"""
    row = db.query(AlertEvent).order_by(AlertEvent.id.desc()).first()
    if row is None:
        return {"status": "unavailable", "detail": "尚无告警"}
    settled = row.status in ("resolved", "acknowledged")
    return {
        "status": "ok" if settled else "firing",
        "alert_id": row.id,
        "rule_id": row.rule_id,
        "event_type": row.event_type,
        "severity": row.severity,
        "alert_status": row.status,
        "message": (row.message or "")[:200],
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def health(db, ctx):
    """轻量本机健康检查：API 进程 + SQLite + 磁盘 + 备份目录可写。"""
    checks = {
        "api": {"status": "ok", "detail": "服务运行中"},
        "sqlite": check_sqlite(ctx.get("db_path")),
        "disk": check_disk(ctx.get("save_dir")),
        "backup_dir": check_backup_dir(ctx.get("backup_dir")),
    }
    return {"status": _overall(checks), "checks": checks}


def metrics(db, ctx, gpu_timeout=3.0):
    """本机指标：SQLite 大小、磁盘、GPU、最近备份、最近告警。"""
    checks = {
        "api": {"status": "ok", "detail": "服务运行中"},
        "sqlite": check_sqlite(ctx.get("db_path")),
        "disk": check_disk(ctx.get("save_dir")),
        "gpu": check_gpu(timeout=gpu_timeout),
        "last_backup": last_backup(db),
        "last_alert": last_alert(db),
    }
    return {"status": _overall(checks), "metrics": checks}


def dependencies(db, ctx, timeout=3.0, milvus_probe=None, neo4j_probe=None, gpu_timeout=3.0, multimodal_probe=None):
    """全量依赖检查：每一项独立超时、独立状态，单依赖失败不影响其它项。

    I3.3：远端多模态单独上报 healthy/degraded/down，不计入本地聚合
    （远端离线不视为本机系统故障，不使普通聊天退出流量）。
    """
    checks = {
        "api": {"status": "ok", "detail": "服务运行中"},
        "sqlite": check_sqlite(ctx.get("db_path")),
        "disk": check_disk(ctx.get("save_dir")),
        "backup_dir": check_backup_dir(ctx.get("backup_dir")),
        "milvus": check_milvus(ctx.get("milvus_uri"), timeout=timeout, probe=milvus_probe),
        "neo4j": check_neo4j(
            ctx.get("neo4j_uri"),
            ctx.get("neo4j_username"),
            ctx.get("neo4j_password"),
            timeout=timeout,
            probe=neo4j_probe,
        ),
        "gpu": check_gpu(timeout=gpu_timeout),
        "multimodal": check_multimodal(timeout=timeout, probe=multimodal_probe),
        "multimodal_observability": None,  # J.6：下方基于同一次探针填充
        "last_backup": last_backup(db),
        "last_alert": last_alert(db),
    }
    checks["multimodal_observability"] = check_multimodal_observability(
        timeout=timeout, probe=multimodal_probe, probe_result=checks["multimodal"],
    )
    # I3.3/J.6：远端多模态（含可观测性汇总）不计入本地聚合，远端离线不算本机故障
    local_checks = {
        key: value for key, value in checks.items() if not key.startswith("multimodal")
    }
    return {"status": _overall(local_checks), "dependencies": checks}
