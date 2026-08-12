"""本机邮件告警服务（纯服务层，可单元测试）。

设计约束：
- 本模块不得 import `src` / `server.db_manager`（避免触发 Milvus 初始化）。
  数据库会话、运行上下文（save_dir / db_path / milvus_uri / neo4j_*）由路由层注入。
- SMTP 配置一律来自环境变量（SMTP_HOST / SMTP_PORT / SMTP_USERNAME /
  SMTP_PASSWORD / SMTP_FROM / SMTP_USE_TLS），不落数据库、不出现在 API 响应、
  不写入日志。SMTP 未配置时测试邮件返回 503，但监控主流程仍正常。
- 规则评估：触发、去重（冷却期内不重复发事件）、恢复（firing→resolved）。
  状态从 alert_events 表推导，重启不丢失，也便于验收测试。
- 后台检查循环 alert_loop 可取消：关闭时立即停止调度新轮次；
  round_timeout 只是观察上限，线程的真实退出由各探测项自己的 I/O 超时保证
  （P1-5：不混用注入时钟与系统时钟；不把 wait_for 描述成能终止整轮）。
"""

import asyncio
import logging
import os
import smtplib
import threading
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from server.models.operations_model import AlertRule, AlertEvent, BackupJob
from server.services import monitoring_service

logger = logging.getLogger("sage.alert")

# 首批规则类型（value → 中文名）
RULE_TYPES = {
    "disk_space": "磁盘剩余比例",
    "sqlite_check": "SQLite 检查失败",
    "milvus": "Milvus 不可用",
    "neo4j": "Neo4j 不可用",
    "gpu_mem": "GPU 显存使用率",
    "backup_fail": "备份连续失败",
    # J.6：多模态运行健康（探针可达性 + 熔断 + 指标窗口）
    "mm_down": "多模态远端不可达/熔断",
    "mm_error_rate": "多模态窗口错误率",
    "mm_p95": "多模态 p95 延迟",
    "mm_timeout": "多模态窗口超时次数",
    "mm_image_bytes": "多模态窗口图片字节量",
    "mm_pool": "多模态并发池耗尽",
    "mm_budget": "多模态查询扩展预算耗尽",
}

# J.6：多模态运行健康告警类型集合（统一走 check_multimodal_observability 评估）
MM_RULE_TYPES = frozenset(
    {
        "mm_down",
        "mm_error_rate",
        "mm_p95",
        "mm_timeout",
        "mm_image_bytes",
        "mm_pool",
        "mm_budget",
    }
)


class AlertError(Exception):
    status_code = 400


class AlertNotFound(AlertError):
    status_code = 404


class SMTPNotConfigured(AlertError):
    status_code = 503


class SMTPDeliveryError(AlertError):
    status_code = 502


def _serialize_rule(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "rule_type": row.rule_type,
        "rule_type_label": RULE_TYPES.get(row.rule_type, row.rule_type),
        "enabled": bool(row.enabled),
        "threshold": row.threshold,
        "cooldown_seconds": row.cooldown_seconds,
        "notify_email": row.notify_email,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _serialize_event(row) -> dict:
    return {
        "id": row.id,
        "rule_id": row.rule_id,
        "event_type": row.event_type,
        "severity": row.severity,
        "status": row.status,
        "message": row.message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "acknowledged_at": row.acknowledged_at.isoformat() if row.acknowledged_at else None,
    }


# ---------------------------------------------------------------------------
# 规则 CRUD
# ---------------------------------------------------------------------------


def create_rule(
    db: Session,
    name,
    rule_type,
    enabled=True,
    threshold=None,
    cooldown_seconds=3600,
    notify_email=None,
    created_by="",
):
    if rule_type not in RULE_TYPES:
        raise AlertError("不支持的告警规则类型：{}".format(rule_type))
    if not name or not str(name).strip():
        raise AlertError("规则名称不能为空")
    try:
        cooldown = max(0, int(cooldown_seconds) if cooldown_seconds is not None else 3600)
    except (TypeError, ValueError):
        raise AlertError("冷却时间必须是整数秒")
    row = AlertRule(
        name=str(name).strip()[:255],
        rule_type=rule_type,
        enabled=1 if enabled else 0,
        threshold=(str(threshold)[:50] if threshold is not None else None) or None,
        cooldown_seconds=cooldown,
        notify_email=(str(notify_email).strip()[:255] if notify_email else None) or None,
        created_by=(created_by or "")[:100],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_rules(db: Session) -> dict:
    rows = db.query(AlertRule).order_by(AlertRule.id.asc()).all()
    return {"items": [_serialize_rule(r) for r in rows], "total": len(rows)}


def update_rule(db: Session, rule_id: int, **fields) -> AlertRule:
    row = db.query(AlertRule).filter(AlertRule.id == rule_id).first()
    if row is None:
        raise AlertNotFound("告警规则不存在")
    if "rule_type" in fields and fields["rule_type"] not in RULE_TYPES:
        raise AlertError("不支持的告警规则类型：{}".format(fields["rule_type"]))
    if "name" in fields and (not fields["name"] or not str(fields["name"]).strip()):
        raise AlertError("规则名称不能为空")
    if "cooldown_seconds" in fields:
        try:
            val = fields["cooldown_seconds"]
            fields["cooldown_seconds"] = max(0, int(val) if val is not None else 3600)
        except (TypeError, ValueError):
            raise AlertError("冷却时间必须是整数秒")
    if "enabled" in fields:
        row.enabled = 1 if fields.pop("enabled") else 0
    if "threshold" in fields:
        row.threshold = (str(fields.pop("threshold"))[:50] if fields["threshold"] is not None else None) or None
    if "notify_email" in fields:
        email = fields.pop("notify_email")
        row.notify_email = (str(email).strip()[:255] if email else None) or None
    for key, value in fields.items():
        if hasattr(row, key) and key not in ("id", "created_at", "updated_at"):
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def delete_rule(db: Session, rule_id: int):
    row = db.query(AlertRule).filter(AlertRule.id == rule_id).first()
    if row is None:
        raise AlertNotFound("告警规则不存在")
    db.query(AlertEvent).filter(AlertEvent.rule_id == rule_id).update({"rule_id": None})
    db.delete(row)
    db.commit()
    return {"deleted": True, "rule_id": rule_id}


# ---------------------------------------------------------------------------
# 告警事件
# ---------------------------------------------------------------------------


def list_events(db: Session, page=1, page_size=20, status="", severity=""):
    query = db.query(AlertEvent)
    if status:
        query = query.filter(AlertEvent.status == status)
    if severity:
        query = query.filter(AlertEvent.severity == severity)
    total = query.count()
    rows = (
        query.order_by(AlertEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [_serialize_event(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def acknowledge_event(db: Session, event_id: int) -> AlertEvent:
    row = db.query(AlertEvent).filter(AlertEvent.id == event_id).first()
    if row is None:
        raise AlertNotFound("告警事件不存在")
    row.status = "acknowledged"
    row.acknowledged_at = datetime.now()
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# SMTP（环境变量配置，密码不进日志）
# ---------------------------------------------------------------------------


def smtp_from_env(env=None) -> dict:
    env = env if env is not None else os.environ
    return {
        "host": (env.get("SMTP_HOST") or "").strip(),
        "port": int((env.get("SMTP_PORT") or "587") or 587),
        "username": (env.get("SMTP_USERNAME") or "").strip(),
        "password": env.get("SMTP_PASSWORD") or "",
        "from_addr": (env.get("SMTP_FROM") or "").strip(),
        "use_tls": (env.get("SMTP_USE_TLS") or "true").strip().lower() in ("1", "true", "yes", "on"),
    }


def send_email(cfg, to_addr, subject, body):
    """发送一封纯文本邮件。SMTP 未配置抛 SMTPNotConfigured(503)。"""
    host = (cfg or {}).get("host")
    from_addr = (cfg or {}).get("from_addr")
    if not host or not from_addr or not to_addr:
        raise SMTPNotConfigured("SMTP 未配置，请设置 SMTP_HOST / SMTP_FROM")
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = from_addr
    msg["To"] = to_addr
    port = int((cfg or {}).get("port") or 587)
    try:
        server = smtplib.SMTP(host, port, timeout=10)
        try:
            if (cfg or {}).get("use_tls"):
                server.starttls()
            username = (cfg or {}).get("username")
            if username:
                server.login(username, (cfg or {}).get("password") or "")
            server.sendmail(from_addr, [to_addr], msg.as_string())
        finally:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                server.close()
    except smtplib.SMTPException as exc:
        # 只记录错误类别与简短消息，绝不输出 SMTP 密码
        logger.warning("SMTP 发送失败（规则邮件）: %s", str(exc)[:200])
        raise SMTPDeliveryError("SMTP 发送失败：{}".format(str(exc)[:200]))
    except OSError as exc:
        logger.warning("SMTP 连接失败: %s", str(exc)[:200])
        raise SMTPDeliveryError("SMTP 连接失败：{}".format(str(exc)[:200]))
    return {"ok": True, "to": to_addr}


# ---------------------------------------------------------------------------
# 规则评估（触发 / 去重 / 冷却 / 恢复）
# ---------------------------------------------------------------------------


def _last_event(db, rule_id):
    return (
        db.query(AlertEvent)
        .filter(AlertEvent.rule_id == rule_id)
        .order_by(AlertEvent.id.desc())
        .first()
    )


def _past_cooldown(created_at, cooldown_seconds, now):
    if created_at is None or not cooldown_seconds:
        return True
    return (now - created_at).total_seconds() >= cooldown_seconds


def _create_event(db, rule, event_type, severity, message, now=None):
    """创建告警事件。

    P1-5 时间语义修正：冷却判定使用 evaluate_rules 注入的 now 时钟，
    事件 created_at / resolved_at 也必须写同一个 now，绝不混用注入时间
    和数据库/系统时间，否则冷却差值会变成负数（已稳定复现的 bug）。
    now 为 None 时（acknowledge 等场景）回退系统时间。
    """
    is_resolve = event_type == "recover"
    if now is None:
        now = datetime.now()
    ev = AlertEvent(
        rule_id=rule.id,
        event_type=event_type,
        severity=severity,
        status="resolved" if is_resolve else "firing",
        message=(message or "")[:1000],
        created_at=now,
        resolved_at=now if is_resolve else None,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def _consecutive_backup_failures(db):
    """从最近一次备份倒推连续未完成的备份次数（failed / running 均算失败）。"""
    n = 0
    for row in db.query(BackupJob).order_by(BackupJob.id.desc()).all():
        if row.status == "completed":
            break
        n += 1
    return n


def _evaluate_mm_rule(rule, ctx, multimodal_probe=None):
    """J.6 多模态运行健康告警：探针可达性 + 熔断 + 指标窗口（probe 可注入，离线可测）。

    探针真实 I/O 超时由 requests.get(timeout=3.0) 保证；窗口指标来自
    multimodal_ops.degraded_summary()（纯内存，无网络）。
    """
    res = monitoring_service.check_multimodal_observability(
        timeout=3.0, probe=multimodal_probe,
    )
    rt = rule.rule_type
    status = res.get("status")
    breaker_state = res.get("breaker_state")
    count = int(res.get("count") or 0)
    error_rate = float(res.get("error_rate") or 0.0)
    timeouts = int(res.get("timeouts") or 0)
    p95 = float(res.get("p95_ms") or 0.0)
    image_bytes = int(res.get("image_bytes") or 0)
    pool_exhausted = int(res.get("pool_exhausted") or 0)
    qe = res.get("query_expansion") or {}
    budget_reached = int(qe.get("budget_reached") or 0)

    if rt == "mm_down":
        fault = status in ("down",) or breaker_state == "open"
        return fault, res.get("detail") or "多模态状态 {}（熔断 {}）".format(status, breaker_state)
    if rt == "mm_error_rate":
        threshold = float(rule.threshold or 50)
        return (
            count > 0 and error_rate * 100 >= threshold,
            "多模态窗口错误率 {:.1f}%（阈值 {}%，样本 {}）".format(error_rate * 100, threshold, count),
        )
    if rt == "mm_p95":
        threshold = float(rule.threshold or 5000)
        return (
            count > 0 and p95 >= threshold,
            "多模态 p95 延迟 {:.0f}ms（阈值 {}ms，样本 {}）".format(p95, threshold, count),
        )
    if rt == "mm_timeout":
        threshold = max(1, int(rule.threshold or 3))
        return timeouts >= threshold, "多模态窗口超时 {} 次（阈值 {}）".format(timeouts, threshold)
    if rt == "mm_image_bytes":
        threshold = float(rule.threshold or 500 * 1024 * 1024)
        return (
            image_bytes >= threshold,
            "多模态窗口图片字节 {:.1f}MB（阈值 {:.1f}MB）".format(
                image_bytes / 1048576.0, threshold / 1048576.0
            ),
        )
    if rt == "mm_pool":
        threshold = max(1, int(rule.threshold or 10))
        return pool_exhausted >= threshold, "多模态并发池耗尽 {} 次（阈值 {}）".format(
            pool_exhausted, threshold
        )
    if rt == "mm_budget":
        threshold = max(1, int(rule.threshold or 10))
        return budget_reached >= threshold, "多模态查询扩展预算耗尽 {} 次（阈值 {}）".format(
            budget_reached, threshold
        )
    return False, "未知多模态规则类型"


def _evaluate_rule(db, rule, ctx, milvus_probe=None, neo4j_probe=None, multimodal_probe=None):
    """返回 (fault, detail)。单个规则评估失败只返回 (True, 错误信息)。"""
    rt = rule.rule_type
    try:
        if rt == "disk_space":
            res = monitoring_service.check_disk(ctx.get("save_dir"))
            threshold = float(rule.threshold or 90)
            used = res.get("used_percent") if res.get("status") == "ok" else 0.0
            return res.get("status") != "ok" or used >= threshold, "磁盘已用 {}%（阈值 {}%）".format(used, threshold)
        if rt == "sqlite_check":
            # SQLite busy_timeout 是真实 I/O 超时，探测不会无限阻塞
            res = monitoring_service.check_sqlite(ctx.get("db_path"), timeout=2.0)
            return res.get("status") != "ok", res.get("detail") or "SQLite 检查失败"
        if rt == "milvus":
            # Milvus 探测的真实 I/O 超时（3s），默认 probe 由 pymilvus 客户端超时保证
            res = monitoring_service.check_milvus(
                ctx.get("milvus_uri"), timeout=3.0, probe=milvus_probe
            )
            return res.get("status") != "ok", res.get("detail") or "Milvus 不可用"
        if rt == "neo4j":
            # Neo4j 探测的真实 I/O 超时（3s），由连接/验证超时保证
            res = monitoring_service.check_neo4j(
                ctx.get("neo4j_uri"), ctx.get("neo4j_username"), ctx.get("neo4j_password"),
                timeout=3.0, probe=neo4j_probe,
            )
            return res.get("status") != "ok", res.get("detail") or "Neo4j 不可用"
        if rt == "gpu_mem":
            # nvidia-smi 子进程真实超时（3s），超时由 subprocess.run(timeout=...) 保证
            res = monitoring_service.check_gpu(timeout=3.0)
            if res.get("status") != "ok":
                return False, res.get("detail") or "GPU 不可用（不告警）"
            threshold = float(rule.threshold or 90)
            used = res.get("vram_used_percent", 0.0)
            return used >= threshold, "GPU 显存使用 {}%（阈值 {}%）".format(used, threshold)
        if rt == "backup_fail":
            threshold = max(1, int(rule.threshold or 1))
            n = _consecutive_backup_failures(db)
            return n >= threshold, "连续 {} 次备份失败/未完成（阈值 {}）".format(n, threshold)
        if rt in MM_RULE_TYPES:
            return _evaluate_mm_rule(rule, ctx, multimodal_probe)
    except (TypeError, ValueError):
        return True, "规则阈值非法：{}".format(rule.threshold)
    except Exception as exc:  # noqa: BLE001
        logger.warning("规则 %s 评估异常: %s", rule.id, exc)
        return True, "规则评估异常：{}".format(exc)[:200]
    return False, "未知规则类型"


def evaluate_rules(db: Session, ctx, now=None, milvus_probe=None, neo4j_probe=None, multimodal_probe=None, notify=None):
    """执行一次全部启用规则的检查：触发/去重/冷却/恢复并写事件。

    notify: 可选回调 notify(rule, subject, body, is_resolve)，由路由层注入
            SMTP 发送实现；测试注入记录型 stub。
    multimodal_probe: J.6 多模态探针（可注入），缺省走默认 /health 探针。
    """
    now = now or datetime.now()
    rules = db.query(AlertRule).filter(AlertRule.enabled == 1).all()
    fired, resolved = [], []
    for rule in rules:
        fault, detail = _evaluate_rule(
            db, rule, ctx, milvus_probe, neo4j_probe, multimodal_probe
        )
        last = _last_event(db, rule.id)
        if fault:
            # 冷却期内已有 firing/acknowledged 事件则去重，避免邮件风暴
            if last is not None and last.status in ("firing", "acknowledged") and not _past_cooldown(
                last.created_at, rule.cooldown_seconds, now
            ):
                continue
            ev = _create_event(db, rule, "trigger", "warning", detail, now=now)
            fired.append(ev.id)
            if notify:
                notify(rule, "告警触发：{}".format(rule.name), detail, is_resolve=False)
        else:
            if last is not None and last.status in ("firing", "acknowledged"):
                ev = _create_event(db, rule, "recover", "info", "已恢复正常", now=now)
                resolved.append(ev.id)
                if notify:
                    notify(rule, "告警恢复：{}".format(rule.name), "已恢复正常", is_resolve=True)
    return {"checked": len(rules), "fired": fired, "resolved": resolved}


# ---------------------------------------------------------------------------
# 后台检查循环（lifespan 使用，可取消）
# ---------------------------------------------------------------------------


def _coerce_interval(value) -> float:
    """把任意输入兜底为合法正间隔（非法/非正一律回退 60）。"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 60.0
    return value if value > 0 else 60.0


def alert_interval_seconds(env=None) -> float:
    """读取 ALERT_CHECK_INTERVAL_SECONDS，必须为正数；非法/非正回退默认 60。

    负间隔会让 asyncio.sleep 立即返回形成忙循环，因此必须在此统一兜底。
    """
    env = env if env is not None else os.environ
    return _coerce_interval(env.get("ALERT_CHECK_INTERVAL_SECONDS") or 60)


def alert_round_timeout_seconds(env=None) -> float:
    """读取 ALERT_EVALUATE_TIMEOUT_SECONDS，必须为正数；非法/非正回退默认 90。"""
    env = env if env is not None else os.environ
    try:
        value = float(env.get("ALERT_EVALUATE_TIMEOUT_SECONDS") or 90)
    except (TypeError, ValueError):
        value = 90.0
    return value if value > 0 else 90.0


class AlertLoopState:
    """alert_loop 运行状态（运维/测试可观察：是否正在跑、是否超时卡住、跳过/完成次数）。

    - running:    当前是否有评估线程在后台执行；
    - timed_out:  上一轮超过 round_timeout 仍未完成（线程仍可能在后台执行）；
    - completed:  已启动并结束的轮次数；
    - skipped:    因上一轮未结束而被跳过的轮次数；
    - stopped:    循环已退出（stop 置位或被取消）。
    """

    def __init__(self):
        self.running = False
        self.timed_out = False
        self.completed = 0
        self.skipped = 0
        self.stopped = False
        self._future = None

    def as_dict(self) -> dict:
        return {
            "running": self.running,
            "timed_out": self.timed_out,
            "completed": self.completed,
            "skipped": self.skipped,
            "stopped": self.stopped,
        }


def _run_round(evaluate, state):
    """在后台线程执行一轮 evaluate()；异常只记录日志，不污染 state/future。"""
    try:
        evaluate()
    except Exception:  # noqa: BLE001
        logger.exception("alert evaluation round failed")
    finally:
        state.running = False
        state.completed += 1


async def alert_loop(evaluate, interval=60.0, stop=None, round_timeout=None, state=None):
    """循环执行 evaluate()，间隔 interval 秒。stop.set() 后停止调度新轮次并退出。

    时间语义（P1-5 修正，与旧实现的关键差异）：
    - 每轮通过 asyncio.to_thread 在后台线程执行同步探测，绝不阻塞事件循环；
    - round_timeout 只是“观察上限”：超时只记录 timed_out 状态并跳过后续轮次，
      绝不声称能终止线程——线程的真实退出由各探测项自己的 I/O 超时
      （Milvus 3s / Neo4j 3s / SMTP 10s / nvidia-smi 子进程 3s）保证；
    - 显式跟踪当前运行 future：上一轮未结束时本轮自动跳过（不重复提交线程），
      卡住状态经 state.timed_out 暴露，且只在状态切换时记录一次日志，
      避免 0.01s 间隔循环刷几十条警告；
    - 关闭：stop.set() 或取消后立即停止调度，已提交的线程不被强行杀死
      （线程无法安全 kill），但绝不再提交新线程，因此不会遗留“无限堆积的
      告警执行线程”，也不会永久占住任何锁。
    """
    interval = _coerce_interval(interval)
    if state is None:
        state = AlertLoopState()
    ran_any_round = False
    try:
        while True:
            # stop 置位后（且已至少执行过一轮）立即停止调度新轮次；
            # 兼容“stop 提前置位但要求先跑一轮再退出”的历史语义。
            if stop is not None and stop.is_set() and ran_any_round:
                break
            fut = state._future
            if fut is not None and not fut.done():
                # 上一轮线程仍在后台执行（如真实 I/O 阻塞）：本轮跳过，不重复提交
                state.skipped += 1
                if not state.timed_out:
                    state.timed_out = True
                    logger.warning("上一轮告警评估仍在进行，跳过本轮")
            else:
                if state.timed_out:
                    logger.info("上一轮告警评估已结束，恢复常规轮次")
                state.timed_out = False
                state.running = True
                ran_any_round = True
                state._future = asyncio.ensure_future(
                    asyncio.to_thread(_run_round, evaluate, state)
                )
                fut = state._future
                try:
                    if round_timeout is None or round_timeout <= 0:
                        await asyncio.shield(fut)
                    else:
                        await asyncio.wait_for(asyncio.shield(fut), timeout=round_timeout)
                except asyncio.TimeoutError:
                    state.timed_out = True
                    logger.warning(
                        "告警评估本轮超过 %.1fs 未完成（线程仍在后台执行，后续轮次自动跳过）",
                        round_timeout,
                    )
                except asyncio.CancelledError:
                    # 关闭路径：不强行杀死后台线程，但立即停止调度新轮次
                    break
                except Exception:  # noqa: BLE001
                    logger.exception("alert evaluation failed")
            if stop is not None and stop.is_set():
                break
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
    finally:
        state.stopped = True
        state.running = state._future is not None and not state._future.done()
    return state
