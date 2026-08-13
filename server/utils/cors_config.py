"""CORS 配置解析（9.3.1）。

将 CORS_ALLOWED_ORIGINS 环境变量解析为 (origins, allow_credentials)：
- 未设置/空值：回退到本机开发/生产同源来源，不使用 `*`；
- 显式来源：逗号分隔；包含 `*` 时不允许携带 credentials
  （浏览器 CORS 规范会拒绝 `allow_origins=["*"]` + credentials 的组合）；
- 纯空白分隔后无有效来源时，同样回退到本机开发来源。

本模块无任何外部依赖，便于单元测试直接导入。
"""

from typing import Tuple


def resolve_cors_config(cors_env: str) -> Tuple[list[str], bool]:
    """把 CORS_ALLOWED_ORIGINS 解析为 (origins, allow_credentials)。"""
    if cors_env and cors_env.strip():
        origins = [o.strip() for o in cors_env.split(",") if o.strip()]
        allow_credentials = "*" not in origins
        return origins or ["http://localhost:5173"], allow_credentials
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost",
        "http://127.0.0.1",
    ], True
