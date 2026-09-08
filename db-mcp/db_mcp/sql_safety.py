"""基于 SQLGlot 的数据库只读 SQL 审核。"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from sqlglot import exp, parse
from sqlglot.errors import ParseError


class QueryValidationError(ValueError):
    """调用方提供的 SQL 不符合只读访问边界。"""

    def __init__(self, message: str, *, code: str = "validation_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidatedQuery:
    """审核完成且可安全交由连接层执行的查询。"""

    sql: str
    parameters: Mapping[str, Any]
    referenced_schemas: frozenset[str]
    adaptations: tuple[str, ...]
    has_order_by: bool


_DIALECTS = {"mysql": "mysql", "postgresql": "postgres", "oracle": "oracle"}
_ORACLE_RESERVED_IDENTIFIERS = frozenset(
    {
        "ACCESS",
        "ADD",
        "ALL",
        "ALTER",
        "AND",
        "ANY",
        "AS",
        "ASC",
        "AUDIT",
        "BETWEEN",
        "BY",
        "CHAR",
        "CHECK",
        "CLUSTER",
        "COLUMN",
        "COMMENT",
        "COMPRESS",
        "CONNECT",
        "CREATE",
        "CURRENT",
        "DATE",
        "DECIMAL",
        "DEFAULT",
        "DELETE",
        "DESC",
        "DISTINCT",
        "DROP",
        "ELSE",
        "EXCLUSIVE",
        "EXISTS",
        "FILE",
        "FLOAT",
        "FOR",
        "FROM",
        "GRANT",
        "GROUP",
        "HAVING",
        "IDENTIFIED",
        "IMMEDIATE",
        "IN",
        "INCREMENT",
        "INDEX",
        "INITIAL",
        "INSERT",
        "INTEGER",
        "INTERSECT",
        "INTO",
        "IS",
        "LEVEL",
        "LIKE",
        "LOCK",
        "LONG",
        "MAXEXTENTS",
        "MINUS",
        "MLSLABEL",
        "MODE",
        "MODIFY",
        "NOAUDIT",
        "NOCOMPRESS",
        "NOT",
        "NOWAIT",
        "NULL",
        "NUMBER",
        "OF",
        "OFFLINE",
        "ON",
        "ONLINE",
        "OPTION",
        "OR",
        "ORDER",
        "PCTFREE",
        "PRIOR",
        "PRIVILEGES",
        "PUBLIC",
        "RAW",
        "RENAME",
        "RESOURCE",
        "REVOKE",
        "ROW",
        "ROWID",
        "ROWLABEL",
        "ROWNUM",
        "ROWS",
        "SELECT",
        "SESSION",
        "SET",
        "SHARE",
        "SIZE",
        "SMALLINT",
        "START",
        "SUCCESSFUL",
        "SYNONYM",
        "SYSDATE",
        "TABLE",
        "THEN",
        "TO",
        "TRIGGER",
        "UID",
        "UNION",
        "UNIQUE",
        "UPDATE",
        "USER",
        "VALIDATE",
        "VALUES",
        "VARCHAR",
        "VARCHAR2",
        "VIEW",
        "WHENEVER",
        "WHERE",
        "WITH",
    }
)
_ORACLE_BIND_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*$")
_DIALECT_MISMATCH_MARKERS = {
    "oracle": (re.compile(r"\binformation_schema\b", re.IGNORECASE), re.compile(r"\bdatabase\s*\(", re.IGNORECASE)),
    "mysql": (re.compile(r"\bpg_catalog\b", re.IGNORECASE), re.compile(r"\bcurrent_schema\s*\(", re.IGNORECASE)),
    "postgresql": (re.compile(r"\bdatabase\s*\(", re.IGNORECASE),),
}
_DIALECT_RESERVED_IDENTIFIERS = {
    "postgresql": frozenset({"ALL", "ANALYSE", "AND", "ANY", "AS", "ASC", "BETWEEN", "CASE", "CHECK", "CREATE", "CURRENT_DATE", "CURRENT_USER", "DEFAULT", "DELETE", "DESC", "DISTINCT", "ELSE", "END", "EXISTS", "FALSE", "FETCH", "FOR", "FOREIGN", "FROM", "GRANT", "GROUP", "HAVING", "IN", "INNER", "INSERT", "INTERSECT", "INTO", "JOIN", "LEADING", "LEFT", "LIMIT", "LOCALTIME", "LOCALTIMESTAMP", "NOT", "NULL", "OFFSET", "ON", "ONLY", "OR", "ORDER", "OUTER", "PRIMARY", "REFERENCES", "RETURNING", "RIGHT", "SELECT", "SESSION_USER", "SOME", "SYMMETRIC", "TABLE", "THEN", "TO", "TRAILING", "TRUE", "UNION", "UNIQUE", "USER", "USING", "VARIADIC", "VERBOSE", "WHEN", "WHERE", "WINDOW", "WITH"}),
    "mysql": frozenset({"ACCESSIBLE", "ADD", "ALL", "ALTER", "ANALYZE", "AND", "AS", "ASC", "ASENSITIVE", "BEFORE", "BETWEEN", "BIGINT", "BINARY", "BLOB", "BOTH", "BY", "CALL", "CASCADE", "CASE", "CHANGE", "CHAR", "CHECK", "COLLATE", "COLUMN", "CONDITION", "CONNECTION", "CONSTRAINT", "CONTINUE", "CONVERT", "CREATE", "CROSS", "CURRENT_DATE", "CURRENT_TIME", "CURRENT_TIMESTAMP", "CURRENT_USER", "CURSOR", "DATABASE", "DATABASES", "DAY_HOUR", "DAY_MICROSECOND", "DAY_MINUTE", "DAY_SECOND", "DEC", "DECIMAL", "DECLARE", "DEFAULT", "DELAYED", "DELETE", "DESC", "DESCRIBE", "DETERMINISTIC", "DISTINCT", "DISTINCTROW", "DIV", "DOUBLE", "DROP", "DUAL", "EACH", "ELSE", "ELSEIF", "ENCLOSED", "ESCAPED", "EXISTS", "EXIT", "EXPLAIN", "FALSE", "FETCH", "FLOAT", "FOR", "FORCE", "FOREIGN", "FROM", "FULLTEXT", "GRANT", "GROUP", "HAVING", "HIGH_PRIORITY", "HOUR_MICROSECOND", "HOUR_MINUTE", "HOUR_SECOND", "IF", "IGNORE", "IN", "INDEX", "INFILE", "INNER", "INOUT", "INSENSITIVE", "INSERT", "INT", "INTEGER", "INTERVAL", "INTO", "IS", "ITERATE", "JOIN", "KEY", "KEYS", "KILL", "LEADING", "LEAVE", "LEFT", "LIKE", "LIMIT", "LINEAR", "LINES", "LOAD", "LOCALTIME", "LOCALTIMESTAMP", "LOCK", "LONG", "LOOP", "LOW_PRIORITY", "MASTER_SSL_VERIFY_SERVER_CERT", "MATCH", "MEDIUMBLOB", "MEDIUMINT", "MEDIUMTEXT", "MIDDLEINT", "MINUTE_MICROSECOND", "MINUTE_SECOND", "MOD", "MODIFIES", "NATURAL", "NOT", "NO_WRITE_TO_BINLOG", "NULL", "NUMERIC", "ON", "OPTIMIZE", "OPTION", "OPTIONALLY", "OR", "ORDER", "OUT", "OUTER", "OUTFILE", "PRECISION", "PRIMARY", "PROCEDURE", "PURGE", "RANGE", "READ", "READS", "READ_WRITE", "REAL", "REFERENCES", "REGEXP", "RELEASE", "RENAME", "REPEAT", "REPLACE", "REQUIRE", "RESTRICT", "RETURN", "REVOKE", "RIGHT", "RLIKE", "SCHEMA", "SCHEMAS", "SECOND_MICROSECOND", "SELECT", "SENSITIVE", "SEPARATOR", "SET", "SHOW", "SMALLINT", "SONAME", "SPATIAL", "SPECIFIC", "SQL", "SQLEXCEPTION", "SQLSTATE", "SQLWARNING", "SQL_BIG_RESULT", "SQL_CALC_FOUND_ROWS", "SQL_SMALL_RESULT", "SSL", "STARTING", "STRAIGHT_JOIN", "TABLE", "TERMINATED", "THEN", "TINYBLOB", "TINYINT", "TINYTEXT", "TO", "TRAILING", "TRIGGER", "TRUE", "UNDO", "UNION", "UNIQUE", "UNLOCK", "UNSIGNED", "UPDATE", "USAGE", "USE", "USING", "UTC_DATE", "UTC_TIME", "UTC_TIMESTAMP", "VALUES", "VARBINARY", "VARCHAR", "VARCHARACTER", "VARYING", "WHEN", "WHERE", "WHILE", "WITH", "WRITE", "XOR", "YEAR_MONTH", "ZEROFILL"}),
}


def _reserved_identifiers(dialect: str) -> frozenset[str]:
    if dialect == "oracle":
        return _ORACLE_RESERVED_IDENTIFIERS
    return _DIALECT_RESERVED_IDENTIFIERS[dialect]
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
    parameters: Mapping[str, Any] | None = None,
) -> ValidatedQuery:
    """只接受单条 SELECT/CTE，并把所有对象限制到 schema 白名单。"""

    if not isinstance(sql, str) or not sql.strip():
        raise QueryValidationError("SQL 必须是非空字符串")
    read_dialect = _DIALECTS.get(dialect)
    if read_dialect is None:
        raise QueryValidationError("不支持的 SQL 方言")
    if parameters is not None and not isinstance(parameters, Mapping):
        raise QueryValidationError("parameters 必须是对象", code="parameter_error")
    if any(marker.search(sql) for marker in _DIALECT_MISMATCH_MARKERS[dialect]):
        raise QueryValidationError(
            "SQL 方言与目标数据库类型不匹配；元数据请使用 list_tables 或 describe_table",
            code="dialect_mismatch",
        )
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
        # Oracle 的 DUAL 经 PUBLIC 同义词解析。它不是业务 schema 下的表，
        # 因此不能套用未限定表名的默认 schema 补全规则。
        if dialect == "oracle" and not table.db and table.name.upper() == "DUAL":
            continue
        schema = table.db
        effective_schema = schema if schema else default_schema
        if effective_schema not in allowed_schemas:
            raise QueryValidationError("SQL 引用了 schema 白名单外的对象")
        schemas.add(effective_schema)
        if not schema:
            table.set("db", exp.to_identifier(default_schema))
    actual_parameters = dict(parameters or {})
    adaptations: list[str] = []
    if dialect == "oracle":
        if not statement.args.get("from"):
            statement.set("from", exp.From(this=exp.Table(this=exp.to_identifier("DUAL"))))
            adaptations.append("oracle_dual")
    if _quote_reserved_identifiers(statement, _reserved_identifiers(dialect)):
        adaptations.append(f"{dialect}_reserved_identifier")
    if dialect == "oracle":
        actual_parameters, renamed = _normalize_oracle_binds(statement, actual_parameters)
        if renamed:
            adaptations.append("oracle_bind_parameter")
    return ValidatedQuery(
        sql=_render_sql(statement, dialect),
        parameters=actual_parameters,
        referenced_schemas=frozenset(schemas),
        adaptations=tuple(adaptations),
        has_order_by=statement.args.get("order") is not None,
    )


def _render_sql(statement: exp.Select, dialect: str) -> str:
    """按目标方言输出标识符，并保持 SQLAlchemy 的 :name 参数格式。"""

    rendered = statement.sql(dialect=_DIALECTS[dialect])
    return re.sub(r"%\(([A-Za-z][A-Za-z0-9_]*)\)s", r":\1", rendered)


def _quote_reserved_identifiers(statement: exp.Select, reserved_identifiers: frozenset[str]) -> bool:
    changed = False
    for column in statement.find_all(exp.Column):
        for identifier in (column.this, column.args.get("table"), column.args.get("db")):
            changed = _quote_identifier(identifier, reserved_identifiers) or changed
    for table in statement.find_all(exp.Table):
        changed = _quote_identifier(table.this, reserved_identifiers) or changed
        changed = _quote_identifier(table.args.get("db"), reserved_identifiers) or changed
    for alias in statement.find_all(exp.TableAlias):
        changed = _quote_identifier(alias.this, reserved_identifiers) or changed
    for alias in statement.find_all(exp.Alias):
        changed = _quote_identifier(alias.args.get("alias"), reserved_identifiers) or changed
    return changed


def _quote_identifier(identifier: object, reserved_identifiers: frozenset[str]) -> bool:
    if not isinstance(identifier, exp.Identifier):
        return False
    if identifier.args.get("quoted") or identifier.name.upper() not in reserved_identifiers:
        return False
    identifier.set("quoted", True)
    return True


def _normalize_oracle_binds(
    statement: exp.Select,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """使 Oracle 绑定变量名合法，并同步迁移调用方参数。"""

    placeholders = list(statement.find_all(exp.Placeholder))
    names = [placeholder.name for placeholder in placeholders]
    missing = sorted({name for name in names if name not in parameters})
    if missing:
        raise QueryValidationError("SQL 绑定参数缺失", code="parameter_error")

    result = dict(parameters)
    used_names = set(names)
    renamed = False
    for placeholder in placeholders:
        name = placeholder.name
        if not _is_unsafe_oracle_bind(name):
            continue
        replacement = _unique_oracle_bind_name(name, used_names)
        used_names.add(replacement)
        placeholder.set("this", replacement)
        result[replacement] = result.pop(name)
        renamed = True
    return result, renamed


def _is_unsafe_oracle_bind(name: str) -> bool:
    return not _ORACLE_BIND_NAME.fullmatch(name) or name.upper() in _ORACLE_RESERVED_IDENTIFIERS


def _unique_oracle_bind_name(name: str, used_names: set[str]) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_$#]", "_", name).lower().lstrip("0123456789") or "value"
    candidate = f"p_{cleaned}"
    suffix = 2
    while candidate in used_names:
        candidate = f"p_{cleaned}_{suffix}"
        suffix += 1
    return candidate
