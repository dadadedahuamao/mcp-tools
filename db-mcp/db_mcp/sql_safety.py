"""基于 SQLGlot 的数据库只读 SQL 审核。"""

from __future__ import annotations

from dataclasses import dataclass
import re

from sqlglot import exp, parse
from sqlglot.errors import ParseError


class QueryValidationError(ValueError):
    """调用方提供的 SQL 不符合只读访问边界。"""


@dataclass(frozen=True)
class ValidatedQuery:
    """审核完成且可安全交由连接层执行的查询。"""

    sql: str
    referenced_schemas: frozenset[str]


_DIALECTS = {"mysql": "mysql", "postgresql": "postgres", "oracle": "oracle"}
_FORBIDDEN_TOKENS = re.compile(
    r"\b(?:call|exec(?:ute)?|begin|commit|rollback|savepoint|set\s+transaction|"
    r"lock\s+tables|load\s+data|copy|grant|revoke|use|alter\s+session)\b|"
    r"\bfor\s+(?:update|share|no\s+key\s+update|key\s+share)\b|"
    r"\binto\s+(?:outfile|dumpfile)\b|\bselect\b[\s\S]*?\binto\b",
    re.IGNORECASE,
)
_FORBIDDEN_FUNCTIONS = re.compile(
    r"\b(?:pg_(?:terminate_backend|cancel_backend|read_file|read_binary_file|ls_dir|sleep|advisory_[a-z_]+)|"
    r"set_config|nextval|setval|dblink_[a-z_]*|lo_(?:export|import)|"
    r"get_lock|release_lock|load_file|sleep|sys_exec|sys_eval|"
    r"dbms_[a-z0-9_]+|utl_[a-z0-9_]+)\s*\(",
    re.IGNORECASE,
)
_FORBIDDEN_EXPRESSIONS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Merge,
    exp.Command,
    exp.Lock,
)


def validate_readonly_query(
    sql: str,
    *,
    dialect: str,
    default_schema: str,
    allowed_schemas: frozenset[str],
) -> ValidatedQuery:
    """只接受单条 SELECT/CTE，并把所有对象限制到 schema 白名单。"""

    if not isinstance(sql, str) or not sql.strip():
        raise QueryValidationError("SQL 必须是非空字符串")
    read_dialect = _DIALECTS.get(dialect)
    if read_dialect is None:
        raise QueryValidationError("不支持的 SQL 方言")
    if _FORBIDDEN_TOKENS.search(sql):
        raise QueryValidationError("SQL 包含不允许的只读边界外操作")
    if _FORBIDDEN_FUNCTIONS.search(sql):
        raise QueryValidationError("SQL 包含不允许的有副作用函数")

    try:
        statements = [statement for statement in parse(sql, read=read_dialect) if statement]
    except ParseError as error:
        raise QueryValidationError("SQL 语法无效") from error
    if len(statements) != 1:
        raise QueryValidationError("仅允许执行单条 SQL 查询")
    statement = statements[0]
    if not isinstance(statement, exp.Select):
        raise QueryValidationError("仅允许 SELECT 或只读 CTE 查询")
    if any(statement.find(expression_type) for expression_type in _FORBIDDEN_EXPRESSIONS):
        raise QueryValidationError("SQL 包含不允许的只读边界外操作")
    if any(isinstance(function, exp.Anonymous) for function in statement.find_all(exp.Func)):
        raise QueryValidationError("SQL 包含未批准的自定义函数")

    cte_names = {
        cte.alias_or_name
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }
    schemas: set[str] = set()
    for table in statement.find_all(exp.Table):
        if "@" in table.sql():
            raise QueryValidationError("SQL 不允许使用数据库链接")
        if not table.db and table.name in cte_names:
            continue
        schema = table.db
        effective_schema = schema if schema else default_schema
        if effective_schema not in allowed_schemas:
            raise QueryValidationError("SQL 引用了 schema 白名单外的对象")
        schemas.add(effective_schema)
        if not schema:
            table.set("db", exp.to_identifier(default_schema))
    # 不指定输出方言，避免 PostgreSQL 输出将 SQLAlchemy 的 :name 改写为 %(name)s。
    return ValidatedQuery(sql=statement.sql(), referenced_schemas=frozenset(schemas))
