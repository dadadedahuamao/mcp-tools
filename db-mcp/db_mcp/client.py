"""SQLAlchemy 驱动的受限数据库只读访问。"""

from __future__ import annotations

import base64
from datetime import date, datetime, time
import json
import time as clock
from typing import Any, Callable

from sqlalchemy import URL, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from db_mcp.config import DatabaseSource
from db_mcp.sql_safety import QueryValidationError, validate_readonly_query


class DatabaseQueryError(ValueError):
    """可安全展示给 MCP 调用者的数据库查询错误。"""


EngineFactory = Callable[[DatabaseSource], Engine]


def create_database_engine(source: DatabaseSource) -> Engine:
    """使用已验证的固定配置创建一次性连接池。"""

    driver = {"mysql": "mysql+pymysql", "postgresql": "postgresql+psycopg", "oracle": "oracle+oracledb"}[source.dialect]
    query: dict[str, str] = {}
    connect_args: dict[str, Any] = {"connect_timeout": source.query_limits.connect_timeout_seconds}
    if source.dialect == "oracle":
        query["service_name"] = source.database
        connect_args = {"tcp_connect_timeout": source.query_limits.connect_timeout_seconds}
    if source.tls is True:
        if source.dialect == "mysql":
            connect_args["ssl"] = {}
        elif source.dialect == "postgresql":
            connect_args["sslmode"] = "require"
        else:
            query["protocol"] = "tcps"
    elif isinstance(source.tls, dict):
        if source.dialect == "mysql":
            connect_args["ssl"] = dict(source.tls)
        elif source.dialect == "postgresql":
            connect_args.update(source.tls)
        else:
            query.update({str(key): str(value) for key, value in source.tls.items()})
    url = URL.create(driver, username=source.username, password=source.password, host=source.host, port=source.port, database=None if source.dialect == "oracle" else source.database, query=query)
    return create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=0, connect_args=connect_args)


class DatabaseReader:
    """对一个预注册数据源执行安全、限量的只读操作。"""

    def __init__(self, source: DatabaseSource, engine_factory: EngineFactory = create_database_engine) -> None:
        self.source = source
        self._engine_factory = engine_factory

    def test_connection(self) -> dict[str, object]:
        result = self._run("SELECT 1 AS connected", {})
        return {"alias": self.source.alias, "connected": bool(result["rows"])}

    def query(self, sql: str, parameters: dict[str, Any] | None = None, limit: int | None = None) -> dict[str, object]:
        try:
            validated = validate_readonly_query(sql, dialect=self.source.dialect, default_schema=self.source.default_schema, allowed_schemas=self.source.allowed_schemas)
        except QueryValidationError as error:
            raise DatabaseQueryError(str(error)) from None
        if parameters is not None and not isinstance(parameters, dict):
            raise DatabaseQueryError("parameters 必须是对象")
        actual_limit = self._limit(limit)
        return self._run(validated.sql, parameters or {}, actual_limit)

    def list_schemas(self) -> list[str]:
        return sorted(self.source.allowed_schemas)

    def list_tables(self, schema: str | None = None) -> list[dict[str, object]]:
        schema = self._schema(schema)
        try:
            engine = self._engine_factory(self.source)
            try:
                inspector = inspect(engine)
                names = sorted(inspector.get_table_names(schema=schema))[: self.source.query_limits.max_rows]
                return [{"schema": schema, "name": name, "type": "table"} for name in names]
            finally:
                engine.dispose()
        except SQLAlchemyError as error:
            raise _safe_error(error) from None

    def describe_table(self, table: str, schema: str | None = None) -> dict[str, object]:
        if not isinstance(table, str) or not table.strip():
            raise DatabaseQueryError("table 必须是非空字符串")
        schema = self._schema(schema)
        try:
            engine = self._engine_factory(self.source)
            try:
                inspector = inspect(engine)
                return {"schema": schema, "table": table.strip(), "columns": [_json_value(column) for column in inspector.get_columns(table.strip(), schema=schema)]}
            finally:
                engine.dispose()
        except SQLAlchemyError as error:
            raise _safe_error(error) from None

    def explain_query(self, sql: str, parameters: dict[str, Any] | None = None, sql_id: str | None = None) -> dict[str, object]:
        if self.source.dialect == "oracle":
            if not isinstance(sql_id, str) or not sql_id.strip():
                raise DatabaseQueryError("Oracle 执行计划需要提供可访问游标的 sql_id")
            try:
                return self._run("SELECT plan_table_output FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(:sql_id, NULL, 'TYPICAL'))", {"sql_id": sql_id.strip()})
            except DatabaseQueryError:
                raise DatabaseQueryError("当前账号无执行计划查询权限或 sql_id 不可访问") from None
        try:
            validated = validate_readonly_query(sql, dialect=self.source.dialect, default_schema=self.source.default_schema, allowed_schemas=self.source.allowed_schemas)
        except QueryValidationError as error:
            raise DatabaseQueryError(str(error)) from None
        prefix = "EXPLAIN FORMAT=JSON " if self.source.dialect == "mysql" else "EXPLAIN (FORMAT JSON) "
        return self._run(prefix + validated.sql, parameters or {})

    def get_active_sessions(self, limit: int | None = None) -> dict[str, object]:
        statements = {
            "mysql": "SELECT ID AS session_id, USER AS username, HOST AS host, DB AS database_name, COMMAND AS command, TIME AS seconds, STATE AS state FROM information_schema.PROCESSLIST",
            "postgresql": "SELECT pid AS session_id, usename AS username, datname AS database_name, state, wait_event_type, query_start FROM pg_stat_activity",
            "oracle": "SELECT sid AS session_id, serial# AS serial_number, username, status, machine, event FROM v$session",
        }
        return self._run(statements[self.source.dialect], {}, self._limit(limit))

    def get_lock_summary(self, limit: int | None = None) -> dict[str, object]:
        statements = {
            "mysql": "SELECT ENGINE_LOCK_ID AS lock_id, OBJECT_SCHEMA AS schema_name, OBJECT_NAME AS object_name, LOCK_TYPE AS lock_type, LOCK_STATUS AS lock_status FROM performance_schema.data_locks",
            "postgresql": "SELECT locktype, mode, granted, relation::regclass::text AS relation FROM pg_locks WHERE relation IS NOT NULL",
            "oracle": "SELECT sid AS session_id, type AS lock_type, lmode, request, block FROM v$lock",
        }
        return self._run(statements[self.source.dialect], {}, self._limit(limit))

    def _run(self, sql: str, parameters: dict[str, Any], limit: int | None = None) -> dict[str, object]:
        maximum = limit or self.source.query_limits.max_rows
        started = clock.perf_counter()
        try:
            engine = self._engine_factory(self.source)
            try:
                with engine.connect() as connection:
                    self._configure_timeout(connection)
                    result = connection.execute(text(sql), parameters)
                    columns = list(result.keys())
                    fetched = result.fetchmany(maximum + 1)
            finally:
                engine.dispose()
        except DatabaseQueryError:
            raise
        except Exception as error:
            raise _safe_error(error) from None
        rows: list[dict[str, Any]] = []
        bytes_used = len(json.dumps(columns, ensure_ascii=False).encode("utf-8"))
        truncated = len(fetched) > maximum
        for item in fetched[:maximum]:
            row = {column: _json_value(value) for column, value in zip(columns, item, strict=True)}
            encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if bytes_used + len(encoded) > self.source.query_limits.max_response_bytes:
                truncated = True
                break
            rows.append(row)
            bytes_used += len(encoded)
        response = {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated, "duration_ms": round((clock.perf_counter() - started) * 1000, 2)}
        while rows and _response_bytes(response) > self.source.query_limits.max_response_bytes:
            rows.pop()
            response["row_count"] = len(rows)
            response["truncated"] = True
        if _response_bytes(response) > self.source.query_limits.max_response_bytes:
            response.update({"columns": [], "rows": [], "row_count": 0, "truncated": True})
        return response

    def _configure_timeout(self, connection: Any) -> None:
        timeout_ms = self.source.query_limits.timeout_seconds * 1000
        if self.source.dialect == "postgresql":
            connection.execute(text("SELECT set_config('statement_timeout', :timeout_ms, true)"), {"timeout_ms": str(timeout_ms)})
        elif self.source.dialect == "mysql":
            connection.execute(text("SET SESSION MAX_EXECUTION_TIME = :timeout_ms"), {"timeout_ms": timeout_ms})
        elif self.source.dialect == "oracle":
            raw = connection.connection.driver_connection
            raw.call_timeout = timeout_ms

    def _limit(self, limit: int | None) -> int:
        if limit is None:
            return self.source.query_limits.max_rows
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= self.source.query_limits.max_rows:
            raise DatabaseQueryError(f"limit 必须是 1 到 {self.source.query_limits.max_rows} 的整数")
        return limit

    def _schema(self, schema: str | None) -> str:
        selected = self.source.default_schema if schema is None else schema
        if not isinstance(selected, str) or selected not in self.source.allowed_schemas:
            raise DatabaseQueryError("schema 不在数据源白名单中")
        return selected


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"encoding": "base64", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _safe_error(_error: Exception) -> DatabaseQueryError:
    return DatabaseQueryError("数据库查询失败，请检查数据源连通性、只读权限和数据库对象权限")


def _response_bytes(value: dict[str, object]) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
