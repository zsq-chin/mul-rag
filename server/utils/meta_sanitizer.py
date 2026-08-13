"""聊天 meta 脱敏（6.2.4#7）：日志与异常采集不得出现明文 API Key。

meta 由客户端提交，可能携带密钥字段（api_key / api_base / token 等）。
打日志前统一脱敏为 `***`，覆盖常见拼写；脱敏只影响值，不打乱结构。
"""

_META_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "api_key2",
        "api_base",
        "api-base",
        "apibase",
        "encrypted_api_key",
        "token",
        "authorization",
        "password",
    }
)


def redact_meta_for_log(meta: dict | None) -> dict:
    """把 meta 中敏感字段的值替换为 `***`，其余字段原样返回。

    对 None / 非字典输入返回空字典，避免日志代码侧抛错。
    """
    if not isinstance(meta, dict):
        return {}
    return {
        key: ("***" if str(key).lower() in _META_SENSITIVE_KEYS else value)
        for key, value in meta.items()
    }
