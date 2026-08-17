"""知识字典功能包（设计文档 docs/superpowers/specs/2026-08-16-knowledge-dictionary-generation-design.md）。

模块职责：
- errors            统一业务错误（§14.1）
- permissions       角色权限（§3：admin/superadmin 等价，user 只读）
- repository        关系数据读写与事务边界（§6）
- service           版本/审核/发布/撤回业务不变量（§11）
- source_adapters   三种来源统一为节点流 + 快照（§7）
- extractor         候选抽取与证据校验（§8.1/§8.2）
- normalizer        标准化/去重/置信度纯函数（§8.3/§8.4）
- jobs              持久化任务与生成流水线（§12）
- vector_indexer    Milvus 独立集合/增量索引/检索（§10）
- seed_import       XinJiang 种子幂等迁移（§9）
- export_service    XLSX/CSV/JSON 导出（§13.5）
"""

from . import errors, permissions  # noqa: F401
from .errors import DictionaryError  # noqa: F401

__all__ = ["errors", "permissions", "DictionaryError"]
