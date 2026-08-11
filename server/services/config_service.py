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
import re

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


def sanitize_config_snapshot(raw, drop_internal=False, redact_custom_models=True) -> dict:
    """统一脱敏器：秘密值替换为 "***"，custom_models 的 api_key 一并处理。

    - drop_internal=True：额外丢弃内部大结构（model_names/_config_items 等），
      用于历史快照存储。
    - redact_custom_models=False：保留 custom_models 原样（前端编辑模型时需要
      回显真实 api_key），仅用于 GET /api/config 与修改响应的互动模式。
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
        elif k == "custom_models" and not redact_custom_models:
            out[k] = value
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


def apply_update(db: Session, config_obj, items: dict, operator="", description=None) -> dict:
    """校验白名单并写入变更历史。config_obj 为鸭子类型（config / 测试替身）。"""
    if not items:
        raise ConfigError("没有要修改的配置项")
    items = {str(k): v for k, v in items.items()}
    invalid = [k for k in items if k not in MUTABLE_CONFIG_KEYS or _SECRET_HINT.search(k)]
    if invalid:
        raise ConfigError(
            "以下配置项不在可修改白名单内：{}".format("、".join(sorted(invalid)))
        )

    before = sanitize_config_snapshot(config_obj.dump_config(), drop_internal=True)
    changes = []
    for key, value in items.items():
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
    return {
        "change_id": record.id,
        "changed_keys": list(items.keys()),
        "restart_components": _restart_components_for(items.keys()),
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
    """回滚到某次变更：只回滚该次涉及的非秘密字段，并写入新的历史记录。"""
    row = db.query(ConfigChange).filter(ConfigChange.id == change_id).first()
    if row is None:
        raise ConfigChangeNotFound("配置变更记录不存在")
    changes = json.loads(row.changes) if row.changes else []

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
    return {
        "change_id": record.id,
        "rolled_back_keys": rolled,
        "restart_components": _restart_components_for(rolled),
    }
