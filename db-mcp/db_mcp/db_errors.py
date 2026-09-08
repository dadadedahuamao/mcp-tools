"""将数据库驱动错误映射为不泄露敏感信息的诊断结果。"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class DatabaseFailure:
    code: str
    message: str
    vendor_code: str | None
    exception_type: str


_MAPPINGS = (
    (re.compile(r"ORA-01745\b", re.IGNORECASE), "parameter_error", "SQL 绑定参数名称无效"),
    (re.compile(r"ORA-00936\b|ORA-00933\b", re.IGNORECASE), "sql_syntax_error", "SQL 语法与目标数据库不兼容"),
    (re.compile(r"ORA-00942\b", re.IGNORECASE), "object_not_found", "查询对象不存在或当前账号不可见"),
    (re.compile(r"ORA-01031\b", re.IGNORECASE), "permission_denied", "当前账号没有查询所需权限"),
    (re.compile(r"(?:ERROR\s+)?(?:1142|1227)\b|SELECT command denied|PROCESS privilege", re.IGNORECASE), "permission_denied", "当前账号没有查询所需权限"),
    (re.compile(r"(?:ERROR\s+)?1146\b|Table .+ doesn't exist", re.IGNORECASE), "object_not_found", "查询对象不存在或当前账号不可见"),
)
_VENDOR_CODE = re.compile(r"\b(?:ORA|PG|SQLSTATE)-?\d{4,5}\b|\bORA-\d{5}\b|\bERROR\s+\d{4}\b", re.IGNORECASE)


def classify_database_error(error: Exception) -> DatabaseFailure:
    """分类时只提取错误编号，绝不向调用方传递驱动原始消息。"""

    detail = str(error)
    vendor_code_match = _VENDOR_CODE.search(detail)
    vendor_code = vendor_code_match.group(0).upper() if vendor_code_match else None
    for pattern, code, message in _MAPPINGS:
        if pattern.search(detail):
            return DatabaseFailure(code, message, vendor_code, type(error).__name__)
    if isinstance(error, TimeoutError) or "timeout" in detail.lower() or "timed out" in detail.lower():
        return DatabaseFailure("timeout_error", "数据库查询超时", vendor_code, type(error).__name__)
    return DatabaseFailure("query_error", "数据库查询失败", vendor_code, type(error).__name__)
