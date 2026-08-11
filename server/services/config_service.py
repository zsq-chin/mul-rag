"""系统配置历史与安全回滚（会话注入，纯服务层，便于单元测试）。

职责：
- 可修改字段白名单：未知键与秘密键一律拒绝，整批不写入。
- dump_config() 统一脱敏：secret/token/password/api_key 等值替换为 "***"，
  custom_models 逐项脱敏 api_key；历史快照额外丢弃内部大结构。
- 每次修改保存修改前/后快照、操作人和说明。
- 回滚只回滚该次变更涉及的非秘密字段，写入新的历史记录，不删除旧记录。

安全约束：本模块不得 import `src` / `server.db_manager`。
"""

import json
import logging
import os
import re
import threading

from sqlalchemy.orm import Session

from server.models.operations_model import ConfigChangeHistory as ConfigChange

logger = logging.getLogger("sage.config")

# 可修改配置字段白名单（与前端 SettingView/模型管理实际发送的键一致）
MUTABLE_CONFIG_KEYS = frozenset({
    # 功能开关
    "enable_reranker",
    "enable_knowledge_base",
    "enable_knowledge_graph",
    "default_agent_id",
    # 模型配置
    "model_provider",
    "model_name",
    "embed_model",
    "reranker",
    "model_local_paths",
    "custom_models",
    "use_rewrite_query",
    "device",
    # 图谱检索
    "graph_similarity_threshold",
    "graph_hops",
    "graph_max_entities",
    "graph_max_relations",
    "graph_context_max_chars",
    # 多轮检索
    "multi_query_count",
    "multi_query_max_rounds",
    "multi_query_recall_min",
})

# 命中即视为秘密键的黑名单提示词（双重保险）
_SECRET_HINT = re.compile(
    r"password|passwd|secret|token|api_?key|apikey|smtp|jwt|private|"
    r"master.?key|milvus.?password|mysql.?password|credential|mms.?key|access.?key",
    re.IGNORECASE,
)

# 历史快照中丢弃的内部大结构（GET /api/config 仍返回给前端）
_DROP_INTERNAL_KEYS = frozenset({
    "_config_items",
    "model_names",
    "embed_model_names",
    "reranker_names",
    "model_provider_status",
    "valuable_model_provider",
    "model_dir",
    "filename",
    "save_dir",
})


# 每个可修改键的类型与取值范围规格（P2-1）。未列出的白名单键不强制类型校验。
_CONFIG_VALUE_SPECS = {
    # 功能开关
    "enable_reranker": ("bool", None),
    "enable_knowledge_base": ("bool", None),
    "enable_knowledge_graph": ("bool", None),
    # 模型配置
    "model_provider": ("str", None),
    "model_name": ("str", None),
    "embed_model": ("str", None),
    "reranker": ("str", None),
    "model_local_paths": ("dict", None),
    "custom_models": ("list", None),
    "use_rewrite_query": ("str", ("off", "on", "hyde")),
    "device": ("str", ("cpu", "cuda")),
    # 图谱检索
    "graph_similarity_threshold": ("float", (0.0, 1.0)),
    "graph_hops": ("int", (1, 10)),
    "graph_max_entities": ("int", (1, 500)),
    "graph_max_relations": ("int", (1, 5000)),
    "graph_context_max_chars": ("int", (50, 50000)),
    # 多轮检索
    "multi_query_count": ("int", (1, 10)),
    "multi_query_max_rounds": ("int", (1, 5)),
    "multi_query_recall_min": ("float", (0.0, 1.0)),
}

# custom_models 每项的严格字段规格（P2-1）：(类型, 最小长度, 最大长度)
_CUSTOM_MODEL_FIELDS = {
    "custom_id": ("str", 1, 128),
    "name": ("str", 1, 128),
    "api_base": ("str", 1, 2048),
    "api_key": ("str", 0, 1024),
}

_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)

# 前端回传的只读元数据，绝不写回配置文件（P2-1）
_CUSTOM_MODEL_READONLY = frozenset({"has_api_key", "key_hint"})


def _coerce_scalar(value, kind):
    """尽力把 str/数值 转成目标标量类型；失败返回 (None, False)。"""
    if kind == "bool":
        if isinstance(value, bool):
            return value, True
        if isinstance(value, int) and value in (0, 1):
            return bool(value), True
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("1", "true", "yes", "on"):
                return True, True
            if low in ("0", "false", "no", "off", ""):
                return False, True
        return None, False
    if kind in ("int", "float"):
        if isinstance(value, bool):
            return None, False
        if isinstance(value, int):
            return value, True
        if isinstance(value, float):
            if kind == "int" and not float(value).is_integer():
                return None, False
            return int(value) if kind == "int" else value, True
        if isinstance(value, str):
            try:
                return int(value) if kind == "int" else float(value), True
            except ValueError:
                return None, False
        return None, False
    return None, False


def _validate_config_value(key, value):
    """按规格校验并归一化单个可修改键的值；非法时抛 ConfigError（400）。"""
    spec = _CONFIG_VALUE_SPECS.get(key)
    if spec is None:
        return value
    kind, constraint = spec
    if kind == "str":
        if isinstance(value, bool):
            raise ConfigError(f"{key} 必须是字符串")
        if constraint is not None and value not in constraint:
            raise ConfigError(f"{key} 取值不合法：{value}")
        return value
    if kind == "dict":
        if not isinstance(value, dict):
            raise ConfigError(f"{key} 必须是对象")
        return value
    if kind == "list":
        if not isinstance(value, list):
            raise ConfigError(f"{key} 必须是数组")
        return value
    coerced, ok = _coerce_scalar(value, kind)
    if not ok:
        raise ConfigError(f"{key} 类型错误，期望 {kind}")
    if constraint is not None:
        lo, hi = constraint
        if coerced < lo or coerced > hi:
            raise ConfigError(f"{key} 超出允许范围 [{lo}, {hi}]")
    return coerced


def _validate_custom_models(models):
    """custom_models 每项严格结构校验，并剥离只读元数据（P2-1）。

    - 每项必须是对象；custom_id/name/api_base 必填、字段长度受限。
    - api_base 必须是 http/https URL。
    - custom_id 不得重复。
    - has_api_key/key_hint 等只读元数据在落盘前剥离，防止写回配置文件。
    - api_key 未填写（含空/占位，占位已在 _merge_custom_models 回填真实密钥）
      时不在配置文件中持久化该字段。
    """
    if not isinstance(models, list):
        raise ConfigError("custom_models 必须是数组")
    seen = set()
    out = []
    for i, item in enumerate(models):
        if not isinstance(item, dict):
            raise ConfigError("custom_models[{}] 必须是对象".format(i))
        normalized = {}
        for field, (_kind, min_len, max_len) in _CUSTOM_MODEL_FIELDS.items():
            raw = item.get(field)
            if raw is None:
                if field == "api_key":
                    continue  # 未填 key 的项不持久化 api_key 字段
                raw = ""
            if not isinstance(raw, str):
                raise ConfigError("custom_models[{}] 的 {} 必须是字符串".format(i, field))
            raw = raw.strip()
            if field == "api_base" and raw and not _URL_SCHEME_RE.match(raw):
                raise ConfigError("custom_models[{}] 的 api_base 必须是 http/https 地址".format(i))
            if field == "api_key" and raw == "***":
                raw = ""  # 防御：占位符不应到达这里
            if len(raw) < min_len or len(raw) > max_len:
                raise ConfigError(
                    "custom_models[{}] 的 {} 长度必须在 [{}, {}] 之间".format(i, field, min_len, max_len)
                )
            normalized[field] = raw
        cid = normalized.get("custom_id", "")
        if cid in seen:
            raise ConfigError("custom_models 存在重复 custom_id：{}".format(cid))
        seen.add(cid)
        for k in _CUSTOM_MODEL_READONLY:
            normalized.pop(k, None)
        out.append(normalized)
    return out


def _sanitize_custom_models(models):
    """custom_models 脱敏：api_key 一律占位化，附带 has_api_key / key_hint。"""
    if not isinstance(models, list):
        return models
    out = []
    for m in models:
        if not isinstance(m, dict):
            out.append(m)
            continue
        item = dict(m)
        key = item.get("api_key")
        if isinstance(key, str) and key and key != "***":
            item["has_api_key"] = True
            item["key_hint"] = key[-4:]
        else:
            item["has_api_key"] = False
            item["key_hint"] = ""
        item["api_key"] = "***" if item.get("has_api_key") else ""
        out.append(item)
    return out


def _merge_custom_models(current, incoming):
    """前端整表替换 custom_models 时保留原真实 api_key。

    - 入参项缺 api_key / 为空 / 为占位 "***" 时，沿用当前配置中同 custom_id 的真实密钥；
    - 否则采用新提交的 api_key。
    """
    current_map = {}
    if isinstance(current, list):
        current_map = {
            m.get("custom_id"): m.get("api_key")
            for m in current
            if isinstance(m, dict) and m.get("custom_id")
        }
    out = []
    for m in incoming:
        if not isinstance(m, dict):
            out.append(m)
            continue
        item = dict(m)
        cid = item.get("custom_id")
        submitted = item.get("api_key")
        if not submitted or submitted == "***":
            if cid in current_map:
                item["api_key"] = current_map[cid]
            else:
                item.pop("api_key", None)
        out.append(item)
    return out


# 进程内配置更新锁（P2-1）：同一进程并发更新配置时串行执行，
# 避免内存更新丢失与 _atomic_write 临时文件竞争。
_update_lock = threading.Lock()


class ConfigError(Exception):
    status_code = 400


class ConfigChangeNotFound(ConfigError):
    status_code = 404


# --- 脱敏 ---


def _redact_value(value):
    if isinstance(value, dict):
        return {
            str(k): ("***" if _SECRET_HINT.search(str(k)) else _redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(x) for x in value]
    return value


def sanitize_config_snapshot(raw, drop_internal=False) -> dict:
    """统一脱敏器：秘密值替换为 "***"，custom_models 的 api_key 一律占位化。

    - drop_internal=True：额外丢弃内部大结构（model_names/_config_items 等），
      用于历史快照存储。
    - 任何配置响应（GET / 更新 / 回滚）都不返回真实 API Key；custom_models 附带
      has_api_key / key_hint 供前端提示，绝不泄露密钥本身。
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        k = str(key)
        if drop_internal and k in _DROP_INTERNAL_KEYS:
            continue
        if _SECRET_HINT.search(k):
            out[k] = "***"
        elif k == "custom_models":
            out[k] = _sanitize_custom_models(value)
        else:
            out[k] = _redact_value(value)
    return out


# --- 组件重启提示 ---


def _restore_custom_models(current, old):
    """回滚 custom_models 时保留当前真实 api_key，避免用历史中的 "***" 覆盖真实密钥。

    历史记录中的 api_key 一律脱敏为 "***"，因此回滚结构（custom_id/name/api_base）
    时，从当前配置中取对应 custom_id 的真实 api_key 回填。
    """
    if not isinstance(old, list):
        return old
    current_map = {}
    if isinstance(current, list):
        current_map = {
            m.get("custom_id"): m.get("api_key")
            for m in current
            if isinstance(m, dict) and m.get("custom_id")
        }
    out = []
    for m in old:
        if not isinstance(m, dict):
            out.append(m)
            continue
        item = dict(m)
        cid = item.get("custom_id")
        if item.get("api_key") == "***" and cid in current_map and current_map.get(cid):
            item["api_key"] = current_map[cid]
        # 历史快照携带的只读元数据不得随回滚写回配置文件（P2-1）
        for k in _CUSTOM_MODEL_READONLY:
            item.pop(k, None)
        out.append(item)
    return out


def _restart_components_for(keys) -> list:
    keys = set(keys)
    components = set()
    if keys & {"model_provider", "model_name", "embed_model", "reranker",
               "model_local_paths", "custom_models", "device"}:
        components.add("retriever")
        components.add("knowledge_base")
    if keys & {"enable_knowledge_base"}:
        components.add("knowledge_base")
    if keys & {"enable_knowledge_graph", "graph_similarity_threshold", "graph_hops",
               "graph_max_entities", "graph_max_relations", "graph_context_max_chars"}:
        components.add("graph_base")
    if keys & {"enable_reranker", "use_rewrite_query", "multi_query_count",
               "multi_query_max_rounds", "multi_query_recall_min"}:
        components.add("retriever")
    if keys & {"default_agent_id"}:
        components.add("chat")
    return sorted(components)


# --- 序列化 ---


def _serialize_change(row) -> dict:
    return {
        "id": row.id,
        "operator": row.operator,
        "description": row.description,
        "changes": json.loads(row.changes) if row.changes else [],
        "before_snapshot": json.loads(row.before_snapshot) if row.before_snapshot else {},
        "after_snapshot": json.loads(row.after_snapshot) if row.after_snapshot else {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# --- 更新与历史 ---


def _capture_config_state(config_obj):
    """记录变更前内存配置与文件字节，供失败时恢复。"""
    dump = {}
    try:
        dump = dict(config_obj.dump_config())
    except Exception:
        dump = {}
    filename = getattr(config_obj, "filename", None)
    raw = None
    if filename and os.path.exists(filename):
        try:
            with open(filename, "rb") as f:
                raw = f.read()
        except OSError:
            raw = None
    return dump, raw, filename


def _restore_config(config_obj, captured):
    """把内存配置与配置文件恢复到变更前状态（历史写入失败时调用）。"""
    dump, raw, filename = captured
    config_obj.clear()
    if dump:
        config_obj.update(dump)
    if filename:
        try:
            if raw is None:
                if os.path.exists(filename):
                    os.remove(filename)
            else:
                tmp = f"{filename}.restore.{os.getpid()}"
                with open(tmp, "wb") as f:
                    f.write(raw)
                os.replace(tmp, filename)
        except OSError:
            logger.warning("恢复配置文件失败: %s", filename)


def apply_update(db: Session, config_obj, items: dict, operator="", description=None) -> dict:
    """校验白名单与类型/范围，整批写入并记录变更历史；任何一步失败都恢复原配置。

    - 未知键 / 秘密键 / 类型或范围错误 → 整批拒绝（ConfigError 400），不写任何东西。
    - custom_models 未填写 api_key 的项沿用当前真实密钥（_merge_custom_models）。
    - 配置文件写入后，历史数据库提交失败时恢复内存与文件，避免“配置已改但无历史”。
    """
    if not items:
        raise ConfigError("没有要修改的配置项")
    items = {str(k): v for k, v in items.items()}
    invalid = [k for k in items if k not in MUTABLE_CONFIG_KEYS or _SECRET_HINT.search(k)]
    if invalid:
        raise ConfigError(
            "以下配置项不在可修改白名单内：{}".format("、".join(sorted(invalid)))
        )

    # 整批预校验 + 落盘 + 历史写入，全程持有进程内锁（P2-1）：
    # 同一进程并发更新配置时串行执行，避免内存更新丢失与临时文件竞争。
    with _update_lock:
        normalized = {}
        for key, value in items.items():
            if key == "custom_models":
                merged = _merge_custom_models(config_obj.get("custom_models"), value)
                normalized[key] = _validate_custom_models(merged)
            else:
                normalized[key] = _validate_config_value(key, value)

        captured = _capture_config_state(config_obj)
        before = sanitize_config_snapshot(config_obj.dump_config(), drop_internal=True)
        changes = []
        try:
            for key, value in normalized.items():
                old = config_obj.get(key)
                config_obj[key] = value
                changes.append({"key": key, "old": _redact_value(old), "new": _redact_value(value)})
            config_obj.save()
            after = sanitize_config_snapshot(config_obj.dump_config(), drop_internal=True)

            record = ConfigChange(
                operator=(operator or "")[:100],
                description=(description or "").strip()[:500] or None,
                changes=json.dumps(changes, ensure_ascii=False, default=str),
                before_snapshot=json.dumps(before, ensure_ascii=False, default=str),
                after_snapshot=json.dumps(after, ensure_ascii=False, default=str),
            )
            db.add(record)
            db.commit()
            db.refresh(record)
        except ConfigError:
            _restore_config(config_obj, captured)
            raise
        except Exception:
            _restore_config(config_obj, captured)
            try:
                db.rollback()
            except Exception:
                pass
            logger.error("配置更新失败，已恢复原配置", exc_info=True)
            raise ConfigError("配置更新失败，已回滚")
        return {
            "change_id": record.id,
            "changed_keys": list(normalized.keys()),
            "restart_components": _restart_components_for(normalized.keys()),
        }


def list_history(db: Session, operator="", page=1, page_size=20) -> dict:
    query = db.query(ConfigChange)
    if operator:
        query = query.filter(ConfigChange.operator.ilike(f"%{operator}%"))
    total = query.count()
    rows = (
        query.order_by(ConfigChange.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [_serialize_change(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def get_history(db: Session, change_id: int):
    row = db.query(ConfigChange).filter(ConfigChange.id == change_id).first()
    if row is None:
        return None
    return _serialize_change(row)


def rollback(db: Session, config_obj, change_id: int, operator="", description=None) -> dict:
    """回滚到某次变更：只回滚该次涉及的非秘密字段，并写入新的历史记录。

    历史写入失败时恢复原配置与文件，避免“配置已改但无历史”。
    """
    row = db.query(ConfigChange).filter(ConfigChange.id == change_id).first()
    if row is None:
        raise ConfigChangeNotFound("配置变更记录不存在")
    changes = json.loads(row.changes) if row.changes else []

    with _update_lock:
        captured = _capture_config_state(config_obj)
        before = sanitize_config_snapshot(config_obj.dump_config(), drop_internal=True)
        rolled = []
        revert_changes = []
        for item in changes:
            key = item.get("key")
            old = item.get("old")
            if not key or key not in MUTABLE_CONFIG_KEYS or _SECRET_HINT.search(key):
                continue
            current = config_obj.get(key)
            if key == "custom_models":
                # 历史中 api_key 已被脱敏，回填当前真实密钥
                old = _restore_custom_models(current, old)
            config_obj[key] = old
            rolled.append(key)
            revert_changes.append({"key": key, "old": _redact_value(current), "new": _redact_value(old)})
        try:
            config_obj.save()
            after = sanitize_config_snapshot(config_obj.dump_config(), drop_internal=True)

            record = ConfigChange(
                operator=(operator or "")[:100],
                description=(description or f"回滚到变更 #{change_id}").strip()[:500],
                changes=json.dumps(revert_changes, ensure_ascii=False, default=str),
                before_snapshot=json.dumps(before, ensure_ascii=False, default=str),
                after_snapshot=json.dumps(after, ensure_ascii=False, default=str),
            )
            db.add(record)
            db.commit()
            db.refresh(record)
        except Exception:
            _restore_config(config_obj, captured)
            try:
                db.rollback()
            except Exception:
                pass
            logger.error("配置回滚失败，已恢复原配置", exc_info=True)
            raise ConfigError("配置回滚失败，已回滚")
        return {
            "change_id": record.id,
            "rolled_back_keys": rolled,
            "restart_components": _restart_components_for(rolled),
        }
