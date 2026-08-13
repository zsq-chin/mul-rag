"""问答测试集业务逻辑（会话注入，纯服务层，便于单元测试）。

覆盖：测试集/用例 CRUD、分页搜索、JSON/CSV 导入导出、执行（execute_suite）。

安全约束：
- 仅 superadmin 访问（由 router 鉴权）。
- 导入先全量校验再单事务写入；任一行失败全部回滚并返回行号。
- 单文件最大 5MB、单次最多 5000 条。
- CSV 单元格公式注入防护：`=` `+` `-` `@` `'` 开头字符串导出时加 `'` 转义，
  导入时剥掉一层前导 `'`，保证 JSON/CSV 往返一致。
- 本模块不得 import `src` / `server.db_manager`。
"""

import csv
import io
import json
import logging

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from server.models.evaluation_model import EvaluationCase, EvaluationSuite

logger = logging.getLogger("sage.evaluation")

MAX_IMPORT_BYTES = 5 * 1024 * 1024  # 5MB
MAX_IMPORT_ROWS = 5000
VALID_DIFFICULTY = frozenset({"easy", "medium", "hard"})
CSV_COLUMNS = [
    "question",
    "answer",
    "key_points",
    "kb_id",
    "category",
    "difficulty",
    "enabled",
    "note",
]
_FORMULA_PREFIXES = ("=", "+", "-", "@", "'")

_FALSE_VALUES = {"0", "no", "false", "off", "否"}


class EvaluationError(Exception):
    status_code = 400


class SuiteNotFound(EvaluationError):
    status_code = 404


class CaseNotFound(EvaluationError):
    status_code = 404


# --- 序列化 ---


def _serialize_suite(row, case_count=None):
    if case_count is None:
        case_count = (
            len(row.cases) if getattr(row, "cases", None) is not None else 0
        )
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "category": row.category,
        "created_by": row.created_by,
        "case_count": case_count,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _parse_key_points(raw) -> list[str]:
    """把库中存储的 key_points JSON 字符串解析为字符串列表；空/非法返回 []。"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except (ValueError, TypeError):
        pass
    return []


def _serialize_case(row):
    key_points = _parse_key_points(row.key_points)
    return {
        "id": row.id,
        "suite_id": row.suite_id,
        "question": row.question,
        "answer": row.answer,
        "key_points": key_points,
        "kb_id": row.kb_id,
        "category": row.category,
        "difficulty": row.difficulty,
        "enabled": bool(row.enabled),
        "note": row.note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _suite_counts(session, suite_ids):
    if not suite_ids:
        return {}
    rows = (
        session.query(EvaluationCase.suite_id, func.count(EvaluationCase.id))
        .filter(EvaluationCase.suite_id.in_(suite_ids))
        .group_by(EvaluationCase.suite_id)
        .all()
    )
    return {sid: cnt for sid, cnt in rows}


def _get_suite(session, suite_id: int) -> EvaluationSuite:
    row = session.query(EvaluationSuite).filter(EvaluationSuite.id == suite_id).first()
    if row is None:
        raise SuiteNotFound("测试集不存在")
    return row


# --- 测试集 ---


def create_suite(session: Session, name: str, description=None, category=None, creator=""):
    if not name or not str(name).strip():
        raise EvaluationError("测试集名称不能为空")
    row = EvaluationSuite(
        name=str(name).strip()[:255],
        description=description,
        category=(category or "").strip()[:50] or None,
        created_by=(creator or "")[:100],
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _serialize_suite(row, 0)


def list_suites(session: Session, keyword="", category="", page=1, page_size=20) -> dict:
    q = session.query(EvaluationSuite)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(or_(EvaluationSuite.name.ilike(like), EvaluationSuite.description.ilike(like)))
    if category:
        q = q.filter(EvaluationSuite.category == category)
    total = q.count()
    rows = (
        q.order_by(EvaluationSuite.created_at.desc(), EvaluationSuite.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    counts = _suite_counts(session, [r.id for r in rows])
    items = [_serialize_suite(r, counts.get(r.id, 0)) for r in rows]
    return {"items": items, "page": page, "page_size": page_size, "total": total}


def get_suite(session: Session, suite_id: int) -> dict:
    row = _get_suite(session, suite_id)
    count = (
        session.query(func.count(EvaluationCase.id))
        .filter(EvaluationCase.suite_id == suite_id)
        .scalar()
        or 0
    )
    return _serialize_suite(row, count)


def update_suite(session: Session, suite_id: int, payload: dict) -> dict:
    row = _get_suite(session, suite_id)
    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            raise EvaluationError("测试集名称不能为空")
        row.name = name[:255]
    if "description" in payload:
        row.description = payload.get("description")
    if "category" in payload:
        row.category = (payload.get("category") or "").strip()[:50] or None
    session.commit()
    session.refresh(row)
    count = (
        session.query(func.count(EvaluationCase.id))
        .filter(EvaluationCase.suite_id == suite_id)
        .scalar()
        or 0
    )
    return _serialize_suite(row, count)


def delete_suite(session: Session, suite_id: int) -> bool:
    row = _get_suite(session, suite_id)
    session.delete(row)  # cascade delete-orphan 删除用例
    session.commit()
    return True


# --- 用例 ---


def _normalize_key_points(value):
    """把 key_points 规整为字符串列表；空值返回 None，非法返回 'INVALID'。"""
    if value is None:
        return None
    if isinstance(value, list):
        return [str(k)[:500] for k in value][:50]
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
        except (ValueError, TypeError):
            return "INVALID"
        if isinstance(parsed, list):
            return [str(k)[:500] for k in parsed][:50]
        return "INVALID"
    return "INVALID"


def _validate_case(
    question, answer, key_points, kb_id, category, difficulty, enabled, note
) -> str | None:
    if not question or not str(question).strip():
        return "question 不能为空"
    if difficulty:
        d = str(difficulty).strip().lower()
        if d not in VALID_DIFFICULTY:
            return "difficulty 只能是 easy/medium/hard"
    if key_points is not None and _normalize_key_points(key_points) == "INVALID":
        return "key_points 必须是字符串数组或 JSON 数组字符串"
    return None


def _coerce_case_fields(values: dict) -> dict:
    """把导入/请求字段规整为模型字段。"""
    result = {}
    question = str(values.get("question") or "").strip()
    result["question"] = question
    answer = values.get("answer")
    result["answer"] = str(answer).strip() if isinstance(answer, str) else (answer or None)
    kp = _normalize_key_points(values.get("key_points"))
    if kp == "INVALID":
        result["key_points"] = None
    elif kp:
        result["key_points"] = json.dumps(kp, ensure_ascii=False)
    else:
        result["key_points"] = None
    kb = values.get("kb_id")
    result["kb_id"] = str(kb).strip()[:100] if kb else None
    cat = values.get("category")
    result["category"] = str(cat).strip()[:50] or None if cat else None
    diff = values.get("difficulty")
    result["difficulty"] = str(diff).strip().lower()[:20] or None if diff else None
    enabled = values.get("enabled", 1)
    result["enabled"] = 0 if _is_false(enabled) else 1
    note = values.get("note")
    result["note"] = str(note).strip() or None if isinstance(note, str) else (note or None)
    return result


def _is_false(value) -> bool:
    if isinstance(value, bool):
        return not value
    s = str(value or "").strip().lower()
    return s in _FALSE_VALUES


def list_cases(session: Session, suite_id: int, keyword="", page=1, page_size=20) -> dict:
    _get_suite(session, suite_id)
    q = session.query(EvaluationCase).filter(EvaluationCase.suite_id == suite_id)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(or_(EvaluationCase.question.ilike(like), EvaluationCase.answer.ilike(like)))
    total = q.count()
    rows = (
        q.order_by(EvaluationCase.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [_serialize_case(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def _get_case(session, suite_id: int, case_id: int) -> EvaluationCase:
    row = (
        session.query(EvaluationCase)
        .filter(EvaluationCase.suite_id == suite_id, EvaluationCase.id == case_id)
        .first()
    )
    if row is None:
        raise CaseNotFound("用例不存在")
    return row


def create_case(session: Session, suite_id: int, payload: dict) -> dict:
    _get_suite(session, suite_id)
    err = _validate_case(
        payload.get("question"),
        payload.get("answer"),
        payload.get("key_points"),
        payload.get("kb_id"),
        payload.get("category"),
        payload.get("difficulty"),
        payload.get("enabled"),
        payload.get("note"),
    )
    if err:
        raise EvaluationError(err)
    fields = _coerce_case_fields(payload)
    row = EvaluationCase(suite_id=suite_id, **fields)
    session.add(row)
    session.commit()
    session.refresh(row)
    return _serialize_case(row)


def update_case(session: Session, suite_id: int, case_id: int, payload: dict) -> dict:
    row = _get_case(session, suite_id, case_id)
    merged = _serialize_case(row)
    # 只校验合并后的最终值，保证 partial update 不产生非法状态
    merged["key_points"] = [str(k) for k in merged["key_points"]]
    for key, value in payload.items():
        merged[key] = value
    err = _validate_case(
        merged.get("question"),
        merged.get("answer"),
        merged.get("key_points"),
        merged.get("kb_id"),
        merged.get("category"),
        merged.get("difficulty"),
        merged.get("enabled"),
        merged.get("note"),
    )
    if err:
        raise EvaluationError(err)
    fields = _coerce_case_fields(payload)
    for key in fields:
        if key in payload:
            setattr(row, key, fields[key])
    session.commit()
    session.refresh(row)
    return _serialize_case(row)


def delete_case(session: Session, suite_id: int, case_id: int) -> bool:
    row = _get_case(session, suite_id, case_id)
    session.delete(row)
    session.commit()
    return True


# --- 导入 ---


def _parse_json_rows(content_text: str) -> list[dict]:
    try:
        data = json.loads(content_text)
    except ValueError as e:
        raise EvaluationError(f"JSON 解析失败: {e}")
    rows = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise EvaluationError("JSON 格式错误：应为用例数组或 {cases: [...]}")
    parsed = []
    for item in rows:
        if not isinstance(item, dict):
            raise EvaluationError("JSON 每一行必须是对象")
        parsed.append(item)
    return parsed


def _parse_csv_rows(content_text: str) -> list[dict]:
    try:
        rows = list(csv.reader(io.StringIO(content_text)))
    except csv.Error as e:
        raise EvaluationError(f"CSV 解析失败: {e}")
    if not rows:
        return []
    header = None
    start = 0
    if rows and rows[0] and str(rows[0][0]).strip().lower() == "question":
        header = [str(c).strip() for c in rows[0]]
        start = 1
    parsed = []
    for i, raw in enumerate(rows[start:], start=start + 1):
        if not raw or all(not (c or "").strip() for c in raw):
            continue
        if header:
            row = {header[j]: raw[j] if j < len(raw) else "" for j in range(len(header))}
        else:
            row = {CSV_COLUMNS[j]: raw[j] if j < len(raw) else "" for j in range(len(CSV_COLUMNS))}
        # 公式注入转义还原：剥掉前导单引号
        for key in row:
            if isinstance(row[key], str) and row[key].startswith("'"):
                row[key] = row[key][1:]
        parsed.append(row)
    return parsed


def import_cases(session: Session, suite_id: int, content: bytes, format: str) -> dict:
    """导入用例：先全量校验，任一行失败全部回滚并返回行号。"""
    _get_suite(session, suite_id)
    if not content:
        raise EvaluationError("上传文件为空")
    if len(content) > MAX_IMPORT_BYTES:
        raise EvaluationError("文件超过 5MB 上限")
    fmt = (format or "json").strip().lower()
    if fmt not in ("json", "csv"):
        raise EvaluationError("format 只能是 json 或 csv")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise EvaluationError("文件必须是 UTF-8 编码")
    rows = _parse_json_rows(text) if fmt == "json" else _parse_csv_rows(text)
    if not rows:
        return {"imported": 0, "row_errors": [], "total": 0}
    if len(rows) > MAX_IMPORT_ROWS:
        raise EvaluationError(f"单次最多导入 {MAX_IMPORT_ROWS} 条，当前 {len(rows)} 条")

    row_errors = []
    valid = []
    for i, raw in enumerate(rows, start=1):
        err = _validate_case(
            raw.get("question"),
            raw.get("answer"),
            raw.get("key_points"),
            raw.get("kb_id"),
            raw.get("category"),
            raw.get("difficulty"),
            raw.get("enabled"),
            raw.get("note"),
        )
        if err:
            row_errors.append({"row": i, "error": err})
        else:
            valid.append(_coerce_case_fields(raw))

    if row_errors:
        return {"imported": 0, "row_errors": row_errors, "total": len(rows)}

    for fields in valid:
        session.add(EvaluationCase(suite_id=suite_id, **fields))
    session.commit()
    return {"imported": len(valid), "row_errors": [], "total": len(rows)}


# --- 导出 ---


def _sanitize_cell(value):
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _export_rows(session: Session, suite_id: int) -> list[dict]:
    _get_suite(session, suite_id)
    rows = (
        session.query(EvaluationCase)
        .filter(EvaluationCase.suite_id == suite_id)
        .order_by(EvaluationCase.id)
        .all()
    )
    return [_serialize_case(r) for r in rows]


def export_cases_json(session: Session, suite_id: int) -> dict:
    items = _export_rows(session, suite_id)
    return {"items": items, "page": 1, "page_size": len(items) or 20, "total": len(items)}


def export_cases_csv(session: Session, suite_id: int) -> str:
    items = _export_rows(session, suite_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for it in items:
        writer.writerow(
            [
                _sanitize_cell(it["question"]),
                _sanitize_cell(it["answer"] or ""),
                _sanitize_cell(json.dumps(it["key_points"], ensure_ascii=False)),
                _sanitize_cell(it["kb_id"] or ""),
                _sanitize_cell(it["category"] or ""),
                _sanitize_cell(it["difficulty"] or ""),
                "1" if it["enabled"] else "0",
                _sanitize_cell(it["note"] or ""),
            ]
        )
    return buf.getvalue()


# --- 执行 ---


def _judge_by_key_points(key_points: list[str], answer_text: str) -> bool:
    """按要点子串判分：全部要点都出现在答案中才算通过（不区分大小写）。"""
    lowered = answer_text.lower()
    return all(str(kp).strip().lower() in lowered for kp in key_points if str(kp).strip())


def execute_suite(session: Session, suite_id: int, answerer) -> dict:
    """执行测试集：对每个启用的用例调用 answerer 获取答案并自动判分。

    answerer 签名: (question: str, kb_id: str | None) -> (response: str | None, error: str | None)
    约定两者至多一个非 None：error 非 None 表示该用例无法作答（计入 errored，不判分）；
    否则若存在 key_points 则按子串匹配判分（matched/judged），无 key_points 时该用例
    执行成功但未判分（unjudged）。

    纯计算 + 外部调用：不写库、不改状态，结果以报告形式返回，失败信息逐条透出，
    避免"批量失败被 200 掩盖"（8.1.2）。
    """
    suite = _get_suite(session, suite_id)
    cases = (
        session.query(EvaluationCase)
        .filter(EvaluationCase.suite_id == suite_id, EvaluationCase.enabled == 1)
        .order_by(EvaluationCase.id)
        .all()
    )
    if not cases:
        raise EvaluationError("该测试集没有启用的用例，请先添加或启用用例")

    passed = 0
    failed = 0
    errored = 0
    unjudged = 0
    results = []
    for case in cases:
        question = str(case.question or "").strip()
        key_points = _parse_key_points(case.key_points)
        item = {
            "case_id": case.id,
            "question": question,
            "expected": case.answer,
            "key_points": key_points,
            "kb_id": case.kb_id,
            "response": None,
            "error": None,
            "matched": None,
            "judged": False,
        }
        try:
            answer_text, error = answerer(question, case.kb_id)
        except Exception as e:  # 执行器自身异常也按单条失败计，不让整批崩掉
            answer_text, error = None, f"执行器异常：{e}"
        if error:
            item["error"] = str(error)[:500]
            errored += 1
            results.append(item)
            continue
        item["response"] = (answer_text or "")[:2000]
        if key_points:
            item["matched"] = bool(_judge_by_key_points(key_points, answer_text or ""))
            item["judged"] = True
            if item["matched"]:
                passed += 1
            else:
                failed += 1
        else:
            unjudged += 1
        results.append(item)

    return {
        "suite_id": suite_id,
        "suite_name": suite.name,
        "total": len(cases),
        "passed": passed,
        "failed": failed,
        "errored": errored,
        "unjudged": unjudged,
        "cases": results,
    }
