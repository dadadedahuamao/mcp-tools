from __future__ import annotations

from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from db_mcp.client import DatabaseQueryError, DatabaseReader, _json_value
from db_mcp.db_errors import classify_database_error
from db_mcp.config import DatabaseSource, QueryLimits


class FakeResult:
    keys = lambda self: ["id", "name"]

    def fetchmany(self, size: int):
        return [(1, "one"), (2, "two")]


class FakeConnection:
    def __init__(self):
        self.executed = []

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        return FakeResult()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeEngine:
    def __init__(self):
        self.connection = FakeConnection()

    def connect(self):
        return self.connection

    def dispose(self):
        pass


class FakeInspector:
    def get_table_names(self, schema=None):
        return ["zebra", "alpha", "middle"]

    def get_columns(self, table, schema=None):
        return [{"name": "id", "type": "INTEGER", "nullable": False, "default": None}]

    def get_pk_constraint(self, table, schema=None):
        return {"name": "pk_example", "constrained_columns": ["id"]}

    def get_foreign_keys(self, table, schema=None):
        return [{"name": "fk_owner", "constrained_columns": ["owner_id"], "referred_schema": schema, "referred_table": "owner", "referred_columns": ["id"]}]

    def get_unique_constraints(self, table, schema=None):
        return [{"name": "uq_code", "column_names": ["code"]}]

    def get_check_constraints(self, table, schema=None):
        return [{"name": "ck_state", "sqltext": "state IN ('A', 'I')"}]

    def get_table_comment(self, table, schema=None):
        return {"text": "example table"}


def source(**overrides) -> DatabaseSource:
    values = {
        "alias": "reporting",
        "dialect": "postgresql",
        "host": "db.internal",
        "port": 5432,
        "database": "app",
        "username": "readonly",
        "password_env": "DB_PASSWORD",
        "password": "not-for-output",
        "default_schema": "public",
        "allowed_schemas": frozenset({"public"}),
        "tls": None,
        "query_limits": QueryLimits(max_rows=1, max_response_bytes=10_000),
    }
    values.update(overrides)
    return DatabaseSource(**values)


def test_query_binds_parameters_and_truncates_to_source_limit() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(), engine_factory=lambda _source: engine)

    result = reader.query("SELECT id, name FROM orders WHERE id = :id", {"id": 7})

    assert result["columns"] == ["id", "name"]
    assert result["rows"] == [{"id": 1, "name": "one"}]
    assert result["truncated"] is True
    assert engine.connection.executed[-1][1] == {"id": 7}


def test_query_pagination_requires_order_by_and_returns_bound_cursor() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(query_limits=QueryLimits(max_rows=10)), engine_factory=lambda _source: engine)

    with pytest.raises(DatabaseQueryError, match="ORDER BY"):
        reader.query("SELECT id FROM orders", page_size=2)

    result = reader.query("SELECT id FROM orders ORDER BY id", page_size=1)

    executed_sql, executed_parameters = engine.connection.executed[-1]
    assert "LIMIT" in executed_sql.upper()
    assert executed_parameters["__db_mcp_page_size"] == 1
    assert executed_parameters["__db_mcp_offset"] == 0
    assert result["next_page_token"]


def test_query_pagination_rejects_cursor_from_different_query() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(query_limits=QueryLimits(max_rows=10)), engine_factory=lambda _source: engine)
    first = reader.query("SELECT id FROM orders ORDER BY id", page_size=1)

    with pytest.raises(DatabaseQueryError, match="page_token"):
        reader.query("SELECT id FROM customers ORDER BY id", page_size=1, page_token=first["next_page_token"])


def test_oracle_connection_probe_uses_dual() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(dialect="oracle"), engine_factory=lambda _source: engine)
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    reader.test_connection()

    assert "FROM DUAL" in engine.connection.executed[-1][0]


def test_oracle_query_executes_normalized_sql_and_parameters() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(
        source(
            dialect="oracle",
            default_schema="MFG_MES_SITE_DL",
            allowed_schemas=frozenset({"MFG_MES_SITE_DL"}),
        ),
        engine_factory=lambda _source: engine,
    )
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    reader.query(
        "SELECT po.NUMBER FROM mbm_aps_product_order po WHERE po.NUMBER = :number",
        {"number": "10000889224"},
    )

    executed_sql, executed_parameters = engine.connection.executed[-1]
    assert 'po."NUMBER"' in executed_sql
    assert executed_parameters == {"p_number": "10000889224"}


def test_oracle_list_indexes_binds_schema_and_optional_table() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(
        source(dialect="oracle", default_schema="MES", allowed_schemas=frozenset({"MES"})),
        engine_factory=lambda _source: engine,
    )
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    reader.list_indexes(table="ORDERS")

    executed_sql, executed_parameters = engine.connection.executed[-1]
    assert "ALL_INDEXES" in executed_sql.upper()
    assert "ALL_IND_COLUMNS" in executed_sql.upper()
    assert "ALL_IND_EXPRESSIONS" in executed_sql.upper()
    assert "DBMS_METADATA.GET_DDL" in executed_sql.upper()
    assert executed_parameters == {"schema": "MES", "table_name": "ORDERS"}


def test_oracle_estimate_plan_executes_explain_and_reads_it_in_same_connection() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(
        source(dialect="oracle", default_schema="MES", allowed_schemas=frozenset({"MES"})),
        engine_factory=lambda _source: engine,
    )
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    reader.explain_query("SELECT id FROM orders", mode="estimate")

    executed_sql = " ".join(sql for sql, _params in engine.connection.executed).upper()
    assert "EXPLAIN PLAN SET STATEMENT_ID" in executed_sql
    assert "DBMS_XPLAN.DISPLAY" in executed_sql


def test_oracle_lock_tree_includes_session_and_locked_object_context() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(dialect="oracle"), engine_factory=lambda _source: engine)
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    reader.get_lock_tree(limit=1)

    executed_sql = engine.connection.executed[-1][0].upper()
    assert "V$LOCKED_OBJECT" in executed_sql
    assert "ALL_OBJECTS" in executed_sql


def test_oracle_object_search_supports_synonyms() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(dialect="oracle"), engine_factory=lambda _source: engine)
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    reader.search_objects("order", object_types=["SYNONYM"], limit=1)

    assert "ALL_SYNONYMS" in engine.connection.executed[-1][0].upper()


def test_postgresql_synonym_only_search_is_reported_as_unsupported() -> None:
    reader = DatabaseReader(source(), engine_factory=lambda _source: FakeEngine())

    with pytest.raises(DatabaseQueryError, match="同义词") as error:
        reader.search_objects("order", object_types=["SYNONYM"])

    assert error.value.code == "unsupported_dialect"


def test_postgresql_list_indexes_uses_catalog_views_and_keeps_schema_bound() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(), engine_factory=lambda _source: engine)
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    reader.list_indexes(schema="public", table="orders")

    executed_sql, executed_parameters = engine.connection.executed[-1]
    assert "PG_INDEX" in executed_sql.upper()
    assert "PG_GET_INDEXDEF" in executed_sql.upper()
    assert executed_parameters == {"schema": "public", "table_name": "orders"}


def test_mysql_list_indexes_uses_statistics_and_exposes_mysql_8_fields() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(
        source(dialect="mysql", default_schema="inventory", allowed_schemas=frozenset({"inventory"})),
        engine_factory=lambda _source: engine,
    )
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    reader.list_indexes(schema="inventory", table="orders")

    executed_sql, executed_parameters = engine.connection.executed[-1]
    upper_sql = executed_sql.upper()
    assert "INFORMATION_SCHEMA.STATISTICS" in upper_sql
    assert all(field in upper_sql for field in ("COLLATION", "SUB_PART", "EXPRESSION", "IS_VISIBLE", "INDEX_COMMENT"))
    assert executed_parameters == {"schema": "inventory", "table_name": "orders"}


def test_mysql_runtime_diagnostics_do_not_select_sql_text() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(dialect="mysql"), engine_factory=lambda _source: engine)
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    reader.get_active_sessions()
    reader.get_lock_summary()
    reader.get_lock_tree()
    reader.search_slow_queries()

    executed_sql = " ".join(sql for sql, _params in engine.connection.executed).upper()
    assert "PROCESSLIST_INFO" not in executed_sql
    assert "DIGEST_TEXT" not in executed_sql
    assert "DATA_LOCK_WAITS" in executed_sql
    assert "PERFORMANCE_SCHEMA.THREADS" in executed_sql


def test_mysql_capabilities_describe_indexes_and_performance_schema_requirement() -> None:
    capabilities = DatabaseReader(source(dialect="mysql")).get_capabilities()

    assert capabilities["indexes"] is True
    assert capabilities["lock_tree"] is True
    assert any("performance_schema" in note for note in capabilities["notes"])


def test_list_tables_returns_stable_cursor_pagination(monkeypatch) -> None:
    engine = FakeEngine()
    monkeypatch.setattr("db_mcp.client.inspect", lambda _engine: FakeInspector())
    reader = DatabaseReader(source(query_limits=QueryLimits(max_rows=10)), engine_factory=lambda _source: engine)

    first = reader.list_tables(page_size=2)
    second = reader.list_tables(page_size=2, page_token=first["next_page_token"])

    assert [item["name"] for item in first["items"]] == ["alpha", "middle"]
    assert first["truncated"] is True
    assert [item["name"] for item in second["items"]] == ["zebra"]
    assert second["truncated"] is False


def test_describe_table_includes_constraints_and_table_comment(monkeypatch) -> None:
    engine = FakeEngine()
    monkeypatch.setattr("db_mcp.client.inspect", lambda _engine: FakeInspector())
    reader = DatabaseReader(source(), engine_factory=lambda _source: engine)

    result = reader.describe_table("example")

    assert result["table_comment"] == "example table"
    assert result["constraints"]["primary_key"]["name"] == "pk_example"
    assert result["constraints"]["foreign_keys"][0]["name"] == "fk_owner"
    assert result["constraints"]["unique"][0]["name"] == "uq_code"
    assert result["constraints"]["check"][0]["name"] == "ck_state"


def test_get_lock_tree_uses_postgresql_blocking_function() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(), engine_factory=lambda _source: engine)
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    reader.get_lock_tree()

    assert "PG_BLOCKING_PIDS" in engine.connection.executed[-1][0].upper()


def test_capabilities_are_dialect_specific() -> None:
    assert DatabaseReader(source()).get_capabilities()["indexes"] is True
    assert DatabaseReader(source()).get_capabilities()["explain_estimate"] is True
    assert DatabaseReader(source(dialect="oracle")).get_capabilities()["explain_estimate"] is True


def test_explain_query_rejects_analyze_mode_without_executing_business_sql() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(), engine_factory=lambda _source: engine)

    with pytest.raises(DatabaseQueryError, match="analyze") as error:
        reader.explain_query("SELECT id FROM orders", mode="analyze")

    assert error.value.code == "parameter_error"
    assert engine.connection.executed == []


def test_postgresql_search_objects_limits_to_whitelisted_schema() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(query_limits=QueryLimits(max_rows=5)), engine_factory=lambda _source: engine)
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    reader.search_objects("order", object_types=["TABLE", "COLUMN"], limit=3)

    executed_sql, executed_parameters = engine.connection.executed[-1]
    assert "INFORMATION_SCHEMA.COLUMNS" in executed_sql.upper()
    assert executed_parameters == {"schema": "public", "pattern": "%order%"}


def test_postgresql_search_objects_includes_function_catalog_when_requested() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(query_limits=QueryLimits(max_rows=5)), engine_factory=lambda _source: engine)
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    reader.search_objects("order", object_types=["FUNCTION"], limit=1)

    assert "PG_PROC" in engine.connection.executed[-1][0].upper()


def test_database_health_returns_safe_static_identity_and_runtime_summary() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(), engine_factory=lambda _source: engine)
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    result = reader.get_database_health()

    assert result["data_source"] == "reporting"
    assert result["dialect"] == "postgresql"
    assert result["allowed_schemas"] == ["public"]
    assert "version" in engine.connection.executed[-1][0].lower()


def test_postgresql_slow_query_search_uses_statement_statistics_without_sql_text() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(), engine_factory=lambda _source: engine)
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    reader.search_slow_queries(limit=1)

    executed_sql = engine.connection.executed[-1][0].upper()
    assert "PG_STAT_STATEMENTS" in executed_sql
    assert "QUERY " not in executed_sql


def test_postgresql_object_health_reads_statistics_views() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(), engine_factory=lambda _source: engine)
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    result = reader.get_object_health()

    assert set(result) == {"table_statistics", "index_statistics"}
    executed_sql = " ".join(sql for sql, _params in engine.connection.executed).upper()
    assert "PG_STAT_USER_TABLES" in executed_sql
    assert "PG_STAT_USER_INDEXES" in executed_sql


def test_oracle_search_sql_hides_sql_text_and_normalizes_id() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(dialect="oracle"), engine_factory=lambda _source: engine)
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    reader.search_sql(sql_id="0ABCDEF123456")

    executed_sql, executed_parameters = engine.connection.executed[-1]
    assert "V$SQL" in executed_sql.upper()
    assert "SQL_FULLTEXT" not in executed_sql.upper()
    assert "SQL_TEXT" not in executed_sql.upper()
    assert executed_parameters == {"sql_id": "0abcdef123456"}


def test_oracle_sql_detail_requires_valid_sql_id_and_reads_full_text() -> None:
    reader = DatabaseReader(source(dialect="oracle"), engine_factory=lambda _source: FakeEngine())

    with pytest.raises(DatabaseQueryError, match="sql_id") as error:
        reader.get_sql_detail("not-a-sql-id")

    assert error.value.code == "parameter_error"

    engine = FakeEngine()
    reader = DatabaseReader(source(dialect="oracle"), engine_factory=lambda _source: engine)
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]
    reader.get_sql_detail("0abcdef123456")

    assert "SQL_FULLTEXT" in engine.connection.executed[-1][0].upper()


def test_oracle_object_health_queries_only_fixed_dictionary_views() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(
        source(dialect="oracle", default_schema="MES", allowed_schemas=frozenset({"MES"})),
        engine_factory=lambda _source: engine,
    )
    reader._configure_timeout = lambda _connection: None  # type: ignore[method-assign]

    result = reader.get_object_health()

    assert set(result) == {"invalid_objects", "table_statistics", "index_statistics"}
    executed_sql = " ".join(sql for sql, _params in engine.connection.executed)
    assert "ALL_OBJECTS" in executed_sql.upper()
    assert "ALL_TAB_STATISTICS" in executed_sql.upper()
    assert "ALL_IND_STATISTICS" in executed_sql.upper()


@pytest.mark.parametrize(
    ("method_name", "args", "dialect"),
    [
        ("search_sql", (), "postgresql"),
        ("get_sql_detail", ("0abcdef123456",), "postgresql"),
    ],
)
def test_oracle_diagnostics_reject_unsupported_dialect(method_name: str, args: tuple[object, ...], dialect: str) -> None:
    reader = DatabaseReader(source(dialect=dialect), engine_factory=lambda _source: FakeEngine())

    with pytest.raises(DatabaseQueryError, match="Oracle") as error:
        getattr(reader, method_name)(*args)

    assert error.value.code == "unsupported_dialect"


def test_query_rejects_limit_larger_than_source_cap() -> None:
    reader = DatabaseReader(source(), engine_factory=lambda _source: FakeEngine())

    with pytest.raises(DatabaseQueryError, match="limit"):
        reader.query("SELECT id FROM orders", {}, limit=2)


def test_query_error_does_not_echo_parameters_or_driver_message() -> None:
    class BrokenConnection(FakeConnection):
        def execute(self, statement, params=None):
            raise RuntimeError("password=super-secret")

    class BrokenEngine(FakeEngine):
        def __init__(self):
            self.connection = BrokenConnection()

    reader = DatabaseReader(source(), engine_factory=lambda _source: BrokenEngine())

    with pytest.raises(DatabaseQueryError) as error:
        reader.query("SELECT id FROM orders", {"password": "super-secret"})

    assert "super-secret" not in str(error.value)


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (RuntimeError("ORA-00936: missing expression"), "sql_syntax_error"),
        (RuntimeError("ORA-00942: table or view does not exist"), "object_not_found"),
        (RuntimeError("ORA-01031: insufficient privileges"), "permission_denied"),
        (RuntimeError("ERROR 1142 (42000): SELECT command denied to user"), "permission_denied"),
        (RuntimeError("ERROR 1227 (42000): Access denied; you need (at least one of) the PROCESS privilege(s)"), "permission_denied"),
        (RuntimeError("ERROR 1146 (42S02): Table 'performance_schema.data_locks' doesn't exist"), "object_not_found"),
        (RuntimeError("ORA-01745: invalid host/bind variable name"), "parameter_error"),
        (TimeoutError("timed out"), "timeout_error"),
    ],
)
def test_classifies_database_error(error: Exception, expected_code: str) -> None:
    assert classify_database_error(error).code == expected_code


def test_binary_value_is_labelled_and_encoded_as_base64() -> None:
    assert _json_value(b"abc") == {"encoding": "base64", "value": "YWJj"}


def test_query_masks_default_sensitive_columns_and_emits_audit_log(caplog) -> None:
    import logging

    caplog.set_level(logging.INFO)
    class SensitiveResult(FakeResult):
        keys = lambda self: ["id", "mobile_phone", "access_token"]

        def fetchmany(self, size: int):
            return [(1, "13800138000", "secret-token")]

    class SensitiveConnection(FakeConnection):
        def execute(self, statement, params=None):
            self.executed.append((str(statement), params))
            return SensitiveResult()

    class SensitiveEngine(FakeEngine):
        def __init__(self):
            self.connection = SensitiveConnection()

    reader = DatabaseReader(source(), engine_factory=lambda _source: SensitiveEngine())

    result = reader.query("SELECT id, mobile_phone, access_token FROM orders")

    assert result["rows"] == [{"id": 1, "mobile_phone": "***", "access_token": "***"}]
    assert result["masked_columns"] == ["access_token", "mobile_phone"]
    assert "sql_fingerprint" in caplog.text
    assert "secret-token" not in caplog.text


def test_postgresql_timeout_uses_parameterized_set_config() -> None:
    engine = FakeEngine()
    reader = DatabaseReader(source(), engine_factory=lambda _source: engine)

    reader.query("SELECT id FROM orders")

    assert "set_config" in engine.connection.executed[0][0]


def test_query_drops_oversized_result_envelope() -> None:
    class LargeResult(FakeResult):
        keys = lambda self: ["x" * 2_000]

        def fetchmany(self, size: int):
            return [(1,)]

    class LargeConnection(FakeConnection):
        def execute(self, statement, params=None):
            self.executed.append((str(statement), params))
            return LargeResult()

    class LargeEngine(FakeEngine):
        def __init__(self):
            self.connection = LargeConnection()

    reader = DatabaseReader(source(query_limits=QueryLimits(max_rows=1, max_response_bytes=1024)), engine_factory=lambda _source: LargeEngine())

    result = reader.query("SELECT 1 AS value")

    assert result["truncated"] is True
    assert result["columns"] == []
    assert result["rows"] == []
