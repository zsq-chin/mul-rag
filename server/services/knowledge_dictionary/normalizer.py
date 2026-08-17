"""条目标准化、去重与置信度（设计文档 §8.3 / §8.4）。

纯函数模块：不依赖数据库或模型，便于单元测试。
标准化顺序：名称清理 -> 种子/受控单位映射 -> 数据类型枚举 -> 精确去重键。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional

# ---------------------------------------------------------------------------
# 名称标准化
# ---------------------------------------------------------------------------

_FULL_WIDTH_TABLE = str.maketrans(
    {
        c: chr(ord(c) - 0xFEE0)
        for c in "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
    }
)

_END_PUNCT = re.compile(r"[\s:：,，;；.。()（）\[\]【】、/\\\-—_*]+$")
_LEAD_PUNCT = re.compile(r"^[\s:：,，;；.。()（）\[\]【】、/\\\-—_*]+")


def normalize_name(raw: str) -> str:
    """清理名称空白、全半角、常见标点和大小写差异。"""
    if not raw:
        return ""
    text = str(raw).translate(_FULL_WIDTH_TABLE)
    text = unicodedata.normalize("NFKC", text)
    text = _LEAD_PUNCT.sub("", text)
    text = _END_PUNCT.sub("", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


# ---------------------------------------------------------------------------
# 受控单位映射（保留原单位，同时给出统一表示；设计文档 §8.3.3）
# ---------------------------------------------------------------------------

_UNIT_ALIASES: Dict[str, str] = {
    "m³": "m3",
    "方": "m3",
    "立方米": "m3",
    "m3": "m3",
    "mm": "mm",
    "cm": "cm",
    "m": "m",
    "km": "km",
    "t": "t",
    "吨": "t",
    "kg": "kg",
    "t/d": "t/d",
    "吨/天": "t/d",
    "m³/d": "m3/d",
    "m3/d": "m3/d",
    "方/天": "m3/d",
    "mpa": "mpa",
    "MPa": "mpa",
    "gpa": "gpa",
    "GPa": "gpa",
    "md": "md",
    "mD": "md",
    "℃": "degc",
    "°c": "degc",
    "degc": "degc",
    "%": "pct",
    "pct": "pct",
    "min": "min",
    "分钟": "min",
    "天": "d",
    "d": "d",
    "万元": "wan_yuan",
    "wan_yuan": "wan_yuan",
    "10⁴m³": "1e4_m3",
    "1e4_m3": "1e4_m3",
    "段": "duan",
    "簇": "cu",
    "mm²": "mm2",
}


def normalize_unit(raw: Optional[str]) -> str:
    """单位统一表示；空值返回空串；未知单位回退清理后的原值。"""
    if raw is None:
        return ""
    text = str(raw).translate(_FULL_WIDTH_TABLE)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", "", text).strip().strip("()（）[]【】")
    if not text:
        return ""
    key = text.lower()
    return _UNIT_ALIASES.get(key, text.lower())


# ---------------------------------------------------------------------------
# 数据类型受控枚举（设计文档 §6.4：未知类型保留为 string 候选）
# ---------------------------------------------------------------------------

DATA_TYPES = ("string", "number", "integer", "boolean", "date", "datetime", "enum", "range", "text")

_DATA_TYPE_ALIASES = {
    "str": "string",
    "字符串": "string",
    "文本": "text",
    "text": "text",
    "数字": "number",
    "数值": "number",
    "float": "number",
    "double": "number",
    "number": "number",
    "整型": "integer",
    "整数": "integer",
    "int": "integer",
    "integer": "integer",
    "布尔": "boolean",
    "bool": "boolean",
    "boolean": "boolean",
    "日期": "date",
    "date": "date",
    "时间": "datetime",
    "datetime": "datetime",
    "枚举": "enum",
    "enum": "enum",
    "区间": "range",
    "范围": "range",
    "range": "range",
}


def map_data_type(raw: Optional[str]) -> str:
    """数据类型映射到受控枚举；未知值保留 string 候选（由调用方标记待审核）。"""
    if not raw:
        return "string"
    text = str(raw).strip().lower()
    return _DATA_TYPE_ALIASES.get(text, "string")


def normalize_synonyms(raw: Any) -> List[str]:
    """同义词规范化：去重、去空白、去全半角差异，保持顺序。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    out: List[str] = []
    seen = set()
    for item in raw:
        name = normalize_name(str(item))
        if name and name not in seen:
            seen.add(name)
            out.append(str(item).strip())
    return out


def dedupe_key(entry: Dict[str, Any]) -> str:
    """同一版本内确定性精确去重键：标准名称 + 数据类型 + 规范化单位。"""
    return "|".join(
        [
            normalize_name(str(entry.get("standard_name") or "")),
            map_data_type(entry.get("data_type")),
            normalize_unit(entry.get("unit")),
        ]
    )


def content_hash(entry: Dict[str, Any]) -> str:
    """条目内容哈希：向量增量索引与幂等写入的依据（§10.3）。"""
    canonical = {
        "category": str(entry.get("category") or "").strip(),
        "standard_name": str(entry.get("standard_name") or "").strip(),
        "definition": str(entry.get("definition") or "").strip(),
        "unit": normalize_unit(entry.get("unit")),
        "data_type": map_data_type(entry.get("data_type")),
        "synonyms": normalize_synonyms(entry.get("synonyms")),
        "value_rule": str(entry.get("value_rule") or "").strip(),
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 置信度（设计文档 §8.4：可解释信号组合）
# ---------------------------------------------------------------------------

# 权重可解释：定义 0.30 / 单位与类型 0.20 / 种子命中 0.15 / 多源 0.15 / 完整性 0.10 / 冲突 -0.20
_CONFIDENCE_WEIGHTS = {
    "has_definition": 0.30,
    "explicit_unit": 0.10,
    "explicit_type": 0.10,
    "seed_hit": 0.15,
    "multi_source": 0.15,
    "complete": 0.10,
    "conflict": -0.20,
    "inferred_penalty": -0.10,
}


def compute_confidence(signals: Dict[str, Any]) -> float:
    """由可解释信号组合产生 0..1 置信度，夹紧到 [0, 1]。

    信号键：
    - has_definition: 是否存在明确原文定义（证据覆盖 definition 字段且非推断）
    - explicit_unit / explicit_type: 单位/数据类型是否来自原文
    - seed_hit: 是否命中种子字典
    - multi_source: 是否有多个独立来源支持
    - conflict: 是否存在冲突
    - complete: 必填字段完整
    - inferred_penalty: 关键字段为模型推断
    """
    score = 0.0
    for key, weight in _CONFIDENCE_WEIGHTS.items():
        if bool(signals.get(key)):
            score += weight
    return round(min(max(score, 0.0), 1.0), 4)


CONFIDENCE_HIGH = 0.85  # >= 0.85 高置信，仍需人工审核后发布
CONFIDENCE_REVIEW = 0.60  # 0.60-0.84 重点审核；< 0.60 不允许批量直接通过


def confidence_band(confidence: float) -> str:
    """置信度分级：high / review / low。"""
    if confidence >= CONFIDENCE_HIGH:
        return "high"
    if confidence >= CONFIDENCE_REVIEW:
        return "review"
    return "low"


def merge_synonyms(*synonym_lists: Iterable[str]) -> List[str]:
    """合并多个同义词列表（确定性顺序）。"""
    merged: List[str] = []
    seen = set()
    for lst in synonym_lists:
        for item in normalize_synonyms(list(lst)):
            key = normalize_name(item)
            if key not in seen:
                seen.add(key)
                merged.append(item)
    return merged


def are_units_compatible(a: Optional[str], b: Optional[str]) -> bool:
    """单位一致或可换算视为兼容（当前仅支持一致判定 + 空值兼容）。"""
    na, nb = normalize_unit(a), normalize_unit(b)
    if not na or not nb:
        return True
    return na == nb
