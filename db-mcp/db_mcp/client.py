"""SQLAlchemy 驱动的受限数据库只读访问。"""

from __future__ import annotations

import base64
import binascii
from datetime import date, datetime, time
import json
import hashlib
import logging
import re
import time as clock
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import URL, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from db_mcp.config import DatabaseSource
from db_mcp.db_errors import classify_database_error
from db_mcp.sql_safety import QueryValidationError, validate_readonly_query


class DatabaseQueryError(ValueError):
    """可安全展示给 MCP 调用者的数据库查询错误。"""

    def __init__(self, message: str, *, code: str = "query_error") -> None:
        super().__init__(message)
        self.code = code


EngineFactory = Callable[[DatabaseSource], Engine]
logger = logging.getLogger(__name__)
_ORACLE_SQL_ID = re.compile(r"^[0-9a-z]{13}$", re.IGNORECASE)
_MAX_QUERY_PAGE_OFFSET = 100_000
_SENSITIVE_COLUMN = re.compile(
    r"(?:password|passwd|pwd|secret|token|authorization|api[_-]?key|access[_-]?key|"
    r"mobile|phone|tel(?:ephone)?|id[_-]?(?:card|number)|identity(?:_number)?|"
    r"credit[_-]?card|bank[_-]?card)",
    re.IGNORECASE,
)


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
        sql = "SELECT 1 AS connected FROM DUAL" if self.source.dialect == "oracle" else "SELECT 1 AS connected"
        result = self._run(sql, {})
        return {"alias": self.source.alias, "connected": bool(result["rows"])}

    def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        limit: int | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, object]:
        try:
            validated = validate_readonly_query(
                sql,
                dialect=self.source.dialect,
                default_schema=self.source.default_schema,
                allowed_schemas=self.source.allowed_schemas,
                parameters=parameters or {},
            )
        except QueryValidationError as error:
            raise DatabaseQueryError(str(error), code=error.code) from None
        if parameters is not None and not isinstance(parameters, dict):
            raise DatabaseQueryError("parameters 必须是对象")
        if page_size is not None or page_token is not None:
            if limit is not None:
                raise DatabaseQueryError("分页查询不能同时传入 limit", code="parameter_error")
            if not validated.has_order_by:
                raise DatabaseQueryError("分页查询必须包含稳定的 ORDER BY", code="parameter_error")
            actual_page_size = self._page_size(page_size)
            fingerprint = self._query_fingerprint(validated.sql, dict(validated.parameters))
            offset = self._decode_query_page_token(page_token, fingerprint)
            if offset > _MAX_QUERY_PAGE_OFFSET:
                raise DatabaseQueryError("page_token 超出允许的分页范围", code="parameter_error")
            paged_parameters = dict(validated.parameters)
            if {"__db_mcp_page_size", "__db_mcp_offset"} & set(paged_parameters):
                raise DatabaseQueryError("parameters 不允许使用保留分页参数名", code="parameter_error")
            paged_parameters.update({"__db_mcp_page_size": actual_page_size, "__db_mcp_offset": offset})
            result = self._run(self._paginate_sql(validated.sql), paged_parameters, actual_page_size)
            result.update(
                {
                    "page_size": actual_page_size,
                    "next_page_token": self._encode_query_page_token(offset + int(result["row_count"]), fingerprint)
                    if result["truncated"] and result["row_count"]
                    else None,
                }
            )
            return result
        actual_limit = self._limit(limit)
        return self._run(validated.sql, dict(validated.parameters), actual_limit)

    def list_schemas(self) -> list[str]:
        return sorted(self.source.allowed_schemas)

    def list_tables(
        self,
        schema: str | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, object]:
        """列出表，并使用表名游标避免调用方无感遗漏超过上限的对象。"""

        schema = self._schema(schema)
        actual_page_size = self._page_size(page_size)
        after_name = self._decode_table_page_token(page_token, schema)
        try:
            engine = self._engine_factory(self.source)
            try:
                inspector = inspect(engine)
                names = sorted(inspector.get_table_names(schema=schema))
                if after_name is not None:
                    names = [name for name in names if name > after_name]
                selected = names[:actual_page_size]
                truncated = len(names) > len(selected)
                return {
                    "items": [{"schema": schema, "name": name, "type": "table"} for name in selected],
                    "returned_count": len(selected),
                    "truncated": truncated,
                    "next_page_token": self._encode_table_page_token(schema, selected[-1]) if truncated and selected else None,
                }
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
                table_name = table.strip()
                comment = self._inspector_optional(inspector, "get_table_comment", table_name, schema=schema) or {}
                return {
                    "schema": schema,
                    "table": table_name,
                    "table_comment": comment.get("text") if isinstance(comment, dict) else None,
                    "columns": [_json_value(column) for column in inspector.get_columns(table_name, schema=schema)],
                    "constraints": {
                        "primary_key": _json_value(self._inspector_optional(inspector, "get_pk_constraint", table_name, schema=schema) or {}),
                        "foreign_keys": _json_value(self._inspector_optional(inspector, "get_foreign_keys", table_name, schema=schema) or []),
                        "unique": _json_value(self._inspector_optional(inspector, "get_unique_constraints", table_name, schema=schema) or []),
                        "check": _json_value(self._inspector_optional(inspector, "get_check_constraints", table_name, schema=schema) or []),
                    },
                }
            finally:
                engine.dispose()
        except SQLAlchemyError as error:
            raise _safe_error(error) from None

    def list_indexes(
        self,
        schema: str | None = None,
        table: str | None = None,
        limit: int | None = None,
    ) -> dict[str, object]:
        """列出业务 schema 中的索引和索引列。"""

        selected_schema = self._schema(schema)
        if table is not None and (not isinstance(table, str) or not table.strip()):
            raise DatabaseQueryError("table 必须是非空字符串")
        if self.source.dialect == "postgresql":
            return self._run(
                """
                SELECT ns.nspname AS schema_name, tbl.relname AS table_name,
                       idx.relname AS index_name, am.amname AS index_method,
                       ix.indisunique AS is_unique, ix.indisprimary AS is_primary,
                       ix.indisvalid AS is_valid,
                       pg_get_indexdef(idx.oid) AS definition,
                       pg_get_expr(ix.indpred, ix.indrelid) AS predicate,
                       ord.ordinality AS column_position,
                       pg_get_indexdef(idx.oid, ord.ordinality, true) AS column_definition
                FROM pg_index ix
                JOIN pg_class idx ON idx.oid = ix.indexrelid
                JOIN pg_class tbl ON tbl.oid = ix.indrelid
                JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
                JOIN pg_am am ON am.oid = idx.relam
                LEFT JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS ord(attnum, ordinality) ON true
                WHERE ns.nspname = :schema
                  AND (:table_name IS NULL OR tbl.relname = :table_name)
                ORDER BY tbl.relname, idx.relname, ord.ordinality
                """,
                {"schema": selected_schema, "table_name": table.strip() if isinstance(table, str) else None},
                self._limit(limit),
            )
        if self.source.dialect == "mysql":
            return self._run(
                """
                SELECT table_schema AS schema_name, table_name, index_name,
                       (non_unique = 0) AS is_unique, index_type,
                       seq_in_index AS column_position, collation AS sort_direction,
                       sub_part AS prefix_length, column_name, expression,
                       is_visible, cardinality, index_comment
                FROM information_schema.statistics
                WHERE table_schema = :schema
                  AND (:table_name IS NULL OR table_name = :table_name)
                ORDER BY table_name, index_name, seq_in_index
                """,
                {"schema": selected_schema, "table_name": table.strip() if isinstance(table, str) else None},
                self._limit(limit),
            )
        self._require_oracle()
        return self._run(
            """
            SELECT ai.owner AS schema_name, ai.table_name, ai.index_name, ai.index_type,
                   ai.uniqueness, ai.status, ai.last_analyzed,
                   DBMS_METADATA.GET_DDL('INDEX', ai.index_name, ai.owner) AS definition,
                   NVL(aic.column_name, aie.column_expression) AS column_definition,
                   aic.column_position, aic.descend
            FROM all_indexes ai
            JOIN all_ind_columns aic
              ON aic.index_owner = ai.owner AND aic.index_name = ai.index_name
            LEFT JOIN all_ind_expressions aie
              ON aie.index_owner = aic.index_owner
             AND aie.index_name = aic.index_name
             AND aie.column_position = aic.column_position
            WHERE ai.owner = :schema
              AND (:table_name IS NULL OR ai.table_name = :table_name)
            ORDER BY ai.table_name, ai.index_name, aic.column_position
            """,
            {"schema": selected_schema, "table_name": table.strip() if isinstance(table, str) else None},
            self._limit(limit),
        )

    def search_sql(self, sql_id: str | None = None, limit: int | None = None) -> dict[str, object]:
        """返回近一天或指定 SQL ID 的性能摘要，不返回 SQL 文本。"""

        self._require_oracle()
        parameters: dict[str, Any] = {}
        predicate = "last_active_time >= SYSDATE - 1"
        if sql_id is not None:
            parameters["sql_id"] = self._sql_id(sql_id)
            predicate = "sql_id = :sql_id"
        return self._run(
            f"""
            SELECT sql_id, MAX(plan_hash_value) AS plan_hash_value,
                   SUM(executions) AS executions,
                   ROUND(SUM(elapsed_time) / 1000000, 2) AS elapsed_seconds,
                   ROUND(SUM(cpu_time) / 1000000, 2) AS cpu_seconds,
                   SUM(buffer_gets) AS buffer_gets,
                   ROUND(SUM(elapsed_time) / NULLIF(SUM(executions), 0) / 1000, 2) AS avg_elapsed_ms,
                   MAX(last_active_time) AS last_active_time,
                   MAX(module) AS module,
                   MAX(parsing_schema_name) AS parsing_schema
            FROM v$sql
            WHERE {predicate}
            GROUP BY sql_id
            ORDER BY MAX(last_active_time) DESC
            """,
            parameters,
            self._limit(limit),
        )

    def get_sql_detail(self, sql_id: str) -> dict[str, object]:
        """按 SQL ID 返回 SQL 全文及各 child cursor 的统计信息。"""

        self._require_oracle()
        return self._run(
            """
            SELECT sql_id, child_number, plan_hash_value, executions, elapsed_time, cpu_time,
                   buffer_gets, last_active_time, module, parsing_schema_name, sql_fulltext
            FROM v$sql
            WHERE sql_id = :sql_id
            ORDER BY child_number
            """,
            {"sql_id": self._sql_id(sql_id)},
        )

    def get_object_health(self, schema: str | None = None, limit: int | None = None) -> dict[str, object]:
        """返回业务 schema 的无效对象及表、索引统计信息。"""

        selected_schema = self._schema(schema)
        actual_limit = self._limit(limit)
        parameters = {"schema": selected_schema}
        if self.source.dialect == "postgresql":
            return {
                "table_statistics": self._run(
                    """SELECT schemaname AS schema_name, relname AS table_name, n_live_tup AS estimated_rows,
                              n_dead_tup AS dead_rows, last_analyze, last_autoanalyze, last_vacuum, last_autovacuum
                       FROM pg_stat_user_tables WHERE schemaname = :schema ORDER BY relname""",
                    parameters,
                    actual_limit,
                ),
                "index_statistics": self._run(
                    """SELECT schemaname AS schema_name, relname AS table_name, indexrelname AS index_name,
                              idx_scan, idx_tup_read, idx_tup_fetch
                       FROM pg_stat_user_indexes WHERE schemaname = :schema ORDER BY relname, indexrelname""",
                    parameters,
                    actual_limit,
                ),
            }
        if self.source.dialect == "mysql":
            return {
                "table_statistics": self._run(
                    """SELECT table_schema AS schema_name, table_name, table_rows AS estimated_rows,
                              data_length, index_length, update_time
                       FROM information_schema.tables WHERE table_schema = :schema ORDER BY table_name""",
                    parameters,
                    actual_limit,
                ),
                "index_statistics": self._run(
                    """SELECT table_schema AS schema_name, table_name, index_name, non_unique,
                              seq_in_index, column_name, cardinality
                       FROM information_schema.statistics WHERE table_schema = :schema
                       ORDER BY table_name, index_name, seq_in_index""",
                    parameters,
                    actual_limit,
                ),
            }
        self._require_oracle()
        return {
            "invalid_objects": self._run(
                """
                SELECT object_name, object_type, status, last_ddl_time
                FROM all_objects
                WHERE owner = :schema AND status <> 'VALID'
                ORDER BY object_type, object_name
                """,
                parameters,
                actual_limit,
            ),
            "table_statistics": self._run(
                """
                SELECT table_name, num_rows, blocks, avg_row_len, stale_stats, last_analyzed
                FROM all_tab_statistics
                WHERE owner = :schema AND partition_name IS NULL AND subpartition_name IS NULL
                ORDER BY table_name
                """,
                parameters,
                actual_limit,
            ),
            "index_statistics": self._run(
                """
                SELECT index_name, num_rows, distinct_keys, leaf_blocks, clustering_factor,
                       stale_stats, last_analyzed
                FROM all_ind_statistics
                WHERE owner = :schema AND partition_name IS NULL AND subpartition_name IS NULL
                ORDER BY index_name
                """,
                parameters,
                actual_limit,
            ),
        }

    def search_slow_queries(self, limit: int | None = None) -> dict[str, object]:
        """返回 SQL 指纹性能摘要，不返回 SQL 全文或绑定参数。"""

        actual_limit = self._limit(limit)
        if self.source.dialect == "oracle":
            return self.search_sql(limit=actual_limit)
        if self.source.dialect == "postgresql":
            return self._run(
                """SELECT queryid::text AS query_id, calls, total_exec_time, mean_exec_time,
                          max_exec_time, rows, shared_blks_hit, shared_blks_read
                   FROM pg_stat_statements
                   ORDER BY total_exec_time DESC""",
                {},
                actual_limit,
            )
        return self._run(
            """SELECT digest AS query_id, count_star AS calls,
                      ROUND(sum_timer_wait / 1000000000, 2) AS total_exec_ms,
                      ROUND(avg_timer_wait / 1000000000, 2) AS avg_exec_ms,
                      ROUND(max_timer_wait / 1000000000, 2) AS max_exec_ms,
                      sum_rows_sent, sum_rows_examined
               FROM performance_schema.events_statements_summary_by_digest
               WHERE digest IS NOT NULL
               ORDER BY sum_timer_wait DESC""",
            {},
            actual_limit,
        )

    def explain_query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
        sql_id: str | None = None,
        mode: str = "estimate",
    ) -> dict[str, object]:
        if not isinstance(mode, str) or mode.lower() not in {"estimate", "cursor", "analyze"}:
            raise DatabaseQueryError("mode 仅支持 estimate、cursor、analyze", code="parameter_error")
        normalized_mode = mode.lower()
        if normalized_mode == "analyze":
            raise DatabaseQueryError("生产诊断默认不允许 analyze 模式", code="parameter_error")
        if self.source.dialect == "oracle":
            if normalized_mode == "cursor":
                if not isinstance(sql_id, str) or not sql_id.strip():
                    raise DatabaseQueryError("Oracle cursor 模式需要提供可访问游标的 sql_id", code="parameter_error")
                try:
                    return self._run("SELECT plan_table_output FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(:sql_id, NULL, 'TYPICAL'))", {"sql_id": sql_id.strip()})
                except DatabaseQueryError:
                    raise DatabaseQueryError("当前账号无执行计划查询权限或 sql_id 不可访问") from None
            try:
                validated = validate_readonly_query(sql, dialect=self.source.dialect, default_schema=self.source.default_schema, allowed_schemas=self.source.allowed_schemas, parameters=parameters or {})
            except QueryValidationError as error:
                raise DatabaseQueryError(str(error), code=error.code) from None
            return self._run_oracle_estimate(validated.sql, dict(validated.parameters))
        if normalized_mode != "estimate":
            raise DatabaseQueryError("当前数据库仅支持 estimate 模式", code="parameter_error")
        try:
            validated = validate_readonly_query(sql, dialect=self.source.dialect, default_schema=self.source.default_schema, allowed_schemas=self.source.allowed_schemas)
        except QueryValidationError as error:
            raise DatabaseQueryError(str(error)) from None
        prefix = "EXPLAIN FORMAT=JSON " if self.source.dialect == "mysql" else "EXPLAIN (FORMAT JSON) "
        return self._run(prefix + validated.sql, parameters or {})

    def get_active_sessions(self, limit: int | None = None) -> dict[str, object]:
        statements = {
            "mysql": """
                SELECT p.id AS session_id, t.thread_id, p.user AS username,
                       p.host AS client_address, p.db AS database_name,
                       p.command, p.time AS seconds, p.state
                FROM information_schema.processlist p
                LEFT JOIN performance_schema.threads t ON t.processlist_id = p.id
                """,
            "postgresql": "SELECT pid AS session_id, usename AS username, datname AS database_name, application_name, client_addr::text AS client_address, state, wait_event_type, wait_event, query_start, xact_start, md5(query) AS query_fingerprint FROM pg_stat_activity",
            "oracle": "SELECT sid AS session_id, serial# AS serial_number, username, status, machine, event FROM v$session",
        }
        return self._run(statements[self.source.dialect], {}, self._limit(limit))

    def get_lock_summary(self, limit: int | None = None) -> dict[str, object]:
        statements = {
            "mysql": """
                SELECT engine_lock_id AS lock_id, engine_transaction_id AS transaction_id,
                       thread_id, object_schema AS schema_name, object_name,
                       lock_type, lock_mode, lock_status
                FROM performance_schema.data_locks
                """,
            "postgresql": "SELECT locktype, mode, granted, relation::regclass::text AS relation FROM pg_locks WHERE relation IS NOT NULL",
            "oracle": "SELECT sid AS session_id, type AS lock_type, lmode, request, block FROM v$lock",
        }
        return self._run(statements[self.source.dialect], {}, self._limit(limit))

    def get_lock_tree(self, limit: int | None = None) -> dict[str, object]:
        """返回等待会话到阻塞会话的关系；不提供终止会话等写操作。"""

        statements = {
            "postgresql": """
                WITH waiting AS (
                    SELECT a.pid AS waiting_session_id, a.usename AS waiting_username,
                           a.application_name AS waiting_application, a.query_start,
                           a.xact_start, a.wait_event_type, a.wait_event,
                           unnest(pg_blocking_pids(a.pid)) AS blocking_session_id
                    FROM pg_stat_activity a
                    WHERE cardinality(pg_blocking_pids(a.pid)) > 0
                )
                SELECT w.waiting_session_id, w.blocking_session_id,
                       w.waiting_username, blocker.usename AS blocking_username,
                       w.waiting_application, blocker.application_name AS blocking_application,
                       w.query_start, w.xact_start, w.wait_event_type, w.wait_event,
                       lock_info.lock_mode, lock_info.relation
                FROM waiting w
                LEFT JOIN pg_stat_activity blocker ON blocker.pid = w.blocking_session_id
                LEFT JOIN LATERAL (
                    SELECT l.mode AS lock_mode, l.relation::regclass::text AS relation
                    FROM pg_locks l
                    WHERE l.pid = w.waiting_session_id AND NOT l.granted
                    ORDER BY l.relation NULLS LAST
                    LIMIT 1
                ) lock_info ON true
                ORDER BY w.query_start NULLS LAST
            """,
            "mysql": """
                SELECT r.REQUESTING_ENGINE_TRANSACTION_ID AS waiting_transaction_id,
                       r.BLOCKING_ENGINE_TRANSACTION_ID AS blocking_transaction_id,
                       r.REQUESTING_THREAD_ID AS waiting_thread_id,
                       r.BLOCKING_THREAD_ID AS blocking_thread_id,
                       waiting_thread.processlist_id AS waiting_session_id,
                       blocking_thread.processlist_id AS blocking_session_id,
                       w.ENGINE_LOCK_ID AS waiting_lock_id, b.ENGINE_LOCK_ID AS blocking_lock_id,
                       w.OBJECT_SCHEMA AS schema_name, w.OBJECT_NAME AS object_name,
                       w.LOCK_TYPE AS lock_type, w.LOCK_MODE AS waiting_lock_mode,
                       b.LOCK_MODE AS blocking_lock_mode
                FROM performance_schema.data_lock_waits r
                JOIN performance_schema.data_locks w ON w.ENGINE_LOCK_ID = r.REQUESTING_ENGINE_LOCK_ID
                JOIN performance_schema.data_locks b ON b.ENGINE_LOCK_ID = r.BLOCKING_ENGINE_LOCK_ID
                LEFT JOIN performance_schema.threads waiting_thread ON waiting_thread.thread_id = r.REQUESTING_THREAD_ID
                LEFT JOIN performance_schema.threads blocking_thread ON blocking_thread.thread_id = r.BLOCKING_THREAD_ID
            """,
            "oracle": """
                SELECT waiter.sid AS waiting_session_id, blocker.sid AS blocking_session_id,
                       waiting_session.username AS waiting_username,
                       blocking_session.username AS blocking_username,
                       waiting_session.machine AS waiting_machine,
                       blocking_session.machine AS blocking_machine,
                       waiter.type AS lock_type, waiter.request AS waiting_request,
                       blocker.lmode AS blocking_mode,
                       locked_object.object_owner AS schema_name,
                       object_info.object_name, object_info.object_type
                FROM v$lock waiter
                JOIN v$lock blocker ON blocker.id1 = waiter.id1 AND blocker.id2 = waiter.id2
                LEFT JOIN v$session waiting_session ON waiting_session.sid = waiter.sid
                LEFT JOIN v$session blocking_session ON blocking_session.sid = blocker.sid
                LEFT JOIN v$locked_object locked_object ON locked_object.session_id = waiter.sid
                LEFT JOIN all_objects object_info ON object_info.object_id = locked_object.object_id
                WHERE waiter.request > 0 AND blocker.block = 1
            """,
        }
        return self._run(statements[self.source.dialect], {}, self._limit(limit))

    def get_capabilities(self) -> dict[str, object]:
        """返回由当前数据库方言和服务实现共同决定的诊断能力。"""

        return {
            "data_source": self.source.alias,
            "dialect": self.source.dialect,
            "metadata": True,
            "indexes": self.source.dialect in {"oracle", "postgresql", "mysql"},
            "constraints": True,
            "object_search": True,
            "explain_estimate": self.source.dialect in {"postgresql", "mysql", "oracle"},
            "explain_cursor": self.source.dialect == "oracle",
            "explain_analyze": False,
            "active_sessions": True,
            "lock_summary": True,
            "lock_tree": True,
            "slow_query_history": True,
            "query_pagination": True,
            "table_pagination": True,
            "notes": [
                "会话、锁和执行计划的实际可见字段受数据源诊断账号权限限制。",
                "PostgreSQL 慢 SQL 依赖 pg_stat_statements；MySQL 的会话、锁、阻塞树和慢 SQL 依赖 performance_schema。",
            ],
        }

    def search_objects(
        self,
        keyword: str,
        object_types: list[str] | None = None,
        schema: str | None = None,
        limit: int | None = None,
    ) -> dict[str, object]:
        """在白名单 schema 内检索表、视图和字段，不要求调用方猜测物理对象名。"""

        if not isinstance(keyword, str) or len(keyword.strip()) < 2:
            raise DatabaseQueryError("keyword 必须至少包含 2 个字符", code="parameter_error")
        selected_schema = self._schema(schema)
        types = self._object_types(object_types)
        if "SYNONYM" in types and self.source.dialect != "oracle":
            if object_types == ["SYNONYM"]:
                raise DatabaseQueryError("同义词检索仅支持 Oracle 数据源", code="unsupported_dialect")
            types = frozenset(types - {"SYNONYM"})
        pattern = f"%{keyword.strip()}%"
        if self.source.dialect == "postgresql":
            parts: list[str] = []
            if "TABLE" in types or "VIEW" in types:
                relkinds: list[str] = []
                if "TABLE" in types:
                    relkinds.extend(["'r'", "'p'"])
                if "VIEW" in types:
                    relkinds.extend(["'v'", "'m'"])
                parts.append(
                    f"""SELECT n.nspname AS schema_name, c.relname AS object_name,
                               CASE WHEN c.relkind IN ('r', 'p') THEN 'TABLE' ELSE 'VIEW' END AS object_type,
                               NULL::text AS column_name, obj_description(c.oid, 'pg_class') AS comment
                        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = :schema AND c.relkind IN ({', '.join(relkinds)})
                          AND c.relname ILIKE :pattern"""
                )
            if "FUNCTION" in types or "PROCEDURE" in types:
                prokinds: list[str] = []
                if "FUNCTION" in types:
                    prokinds.extend(["'f'", "'a'", "'w'"])
                if "PROCEDURE" in types:
                    prokinds.append("'p'")
                parts.append(
                    f"""SELECT n.nspname AS schema_name, p.proname AS object_name,
                               CASE WHEN p.prokind = 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END AS object_type,
                               NULL::text AS column_name, obj_description(p.oid, 'pg_proc') AS comment
                        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                        WHERE n.nspname = :schema AND p.prokind IN ({', '.join(prokinds)})
                          AND p.proname ILIKE :pattern"""
                )
            if "COLUMN" in types:
                parts.append(
                    """SELECT table_schema AS schema_name, table_name AS object_name, 'COLUMN' AS object_type,
                              column_name, NULL::text AS comment
                       FROM information_schema.columns
                       WHERE table_schema = :schema AND (table_name ILIKE :pattern OR column_name ILIKE :pattern)"""
                )
            return self._run(
                "SELECT * FROM (" + " UNION ALL ".join(parts) + ") objects ORDER BY object_type, object_name, column_name",
                {"schema": selected_schema, "pattern": pattern},
                self._limit(limit),
            )
        if self.source.dialect == "oracle":
            parts = []
            if "TABLE" in types or "VIEW" in types or "FUNCTION" in types or "PROCEDURE" in types:
                oracle_types = []
                if "TABLE" in types:
                    oracle_types.append("'TABLE'")
                if "VIEW" in types:
                    oracle_types.append("'VIEW'")
                if "FUNCTION" in types:
                    oracle_types.append("'FUNCTION'")
                if "PROCEDURE" in types:
                    oracle_types.append("'PROCEDURE'")
                parts.append(
                    f"""SELECT owner AS schema_name, object_name, object_type, CAST(NULL AS VARCHAR2(128)) AS column_name,
                               CAST(NULL AS VARCHAR2(4000)) AS comment
                        FROM all_objects
                        WHERE owner = :schema AND object_type IN ({', '.join(oracle_types)})
                          AND object_name LIKE UPPER(:pattern)"""
                )
            if "COLUMN" in types:
                parts.append(
                    """SELECT owner AS schema_name, table_name AS object_name, 'COLUMN' AS object_type, column_name,
                              comments AS comment
                       FROM all_col_comments
                       WHERE owner = :schema AND (table_name LIKE UPPER(:pattern) OR column_name LIKE UPPER(:pattern))"""
                )
            if "SYNONYM" in types:
                parts.append(
                    """SELECT owner AS schema_name, synonym_name AS object_name, 'SYNONYM' AS object_type,
                              table_name AS column_name, table_owner AS comment
                       FROM all_synonyms
                       WHERE owner = :schema AND synonym_name LIKE UPPER(:pattern)"""
                )
            return self._run(
                "SELECT * FROM (" + " UNION ALL ".join(parts) + ") ORDER BY object_type, object_name, column_name",
                {"schema": selected_schema, "pattern": pattern},
                self._limit(limit),
            )
        parts = []
        if "TABLE" in types or "VIEW" in types:
            table_types = []
            if "TABLE" in types:
                table_types.append("'BASE TABLE'")
            if "VIEW" in types:
                table_types.append("'VIEW'")
            parts.append(
                f"""SELECT table_schema AS schema_name, table_name AS object_name,
                           CASE WHEN table_type = 'BASE TABLE' THEN 'TABLE' ELSE 'VIEW' END AS object_type,
                           NULL AS column_name, table_comment AS comment
                    FROM information_schema.tables
                    WHERE table_schema = :schema AND table_type IN ({', '.join(table_types)})
                      AND table_name LIKE :pattern"""
            )
        if "FUNCTION" in types or "PROCEDURE" in types:
            routine_types = []
            if "FUNCTION" in types:
                routine_types.append("'FUNCTION'")
            if "PROCEDURE" in types:
                routine_types.append("'PROCEDURE'")
            parts.append(
                f"""SELECT routine_schema AS schema_name, routine_name AS object_name, routine_type AS object_type,
                           NULL AS column_name, routine_comment AS comment
                    FROM information_schema.routines
                    WHERE routine_schema = :schema AND routine_type IN ({', '.join(routine_types)})
                      AND routine_name LIKE :pattern"""
            )
        if "COLUMN" in types:
            parts.append(
                """SELECT table_schema AS schema_name, table_name AS object_name, 'COLUMN' AS object_type,
                          column_name, column_comment AS comment
                   FROM information_schema.columns
                   WHERE table_schema = :schema AND (table_name LIKE :pattern OR column_name LIKE :pattern)"""
            )
        return self._run(
            "SELECT * FROM (" + " UNION ALL ".join(parts) + ") objects ORDER BY object_type, object_name, column_name",
            {"schema": selected_schema, "pattern": pattern},
            self._limit(limit),
        )

    def get_database_health(self) -> dict[str, object]:
        """返回不含业务数据的数据库身份、版本和基础运行态摘要。"""

        statements = {
            "postgresql": "SELECT version() AS database_version, current_user AS current_user, current_database() AS database_name, (SELECT count(*) FROM pg_stat_activity) AS session_count",
            "mysql": "SELECT VERSION() AS database_version, CURRENT_USER() AS current_user, DATABASE() AS database_name, (SELECT count(*) FROM information_schema.processlist) AS session_count",
            "oracle": "SELECT (SELECT banner FROM v$version WHERE ROWNUM = 1) AS database_version, USER AS current_user, SYS_CONTEXT('USERENV', 'DB_NAME') AS database_name, (SELECT count(*) FROM v$session) AS session_count FROM dual",
        }
        return {
            "data_source": self.source.alias,
            "dialect": self.source.dialect,
            "allowed_schemas": sorted(self.source.allowed_schemas),
            "readonly": True,
            "limits": {
                "max_rows": self.source.query_limits.max_rows,
                "timeout_seconds": self.source.query_limits.timeout_seconds,
                "max_response_bytes": self.source.query_limits.max_response_bytes,
            },
            "runtime": self._run(statements[self.source.dialect], {}),
        }

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
            raise _safe_error(error, self.source) from None
        return self._format_result(columns, fetched, maximum, started, sql)

    def _run_oracle_estimate(self, sql: str, parameters: dict[str, Any]) -> dict[str, object]:
        """在同一 Oracle 会话中生成并读取估算计划，连接关闭时回滚内部 PLAN_TABLE 写入。"""

        maximum = self.source.query_limits.max_rows
        started = clock.perf_counter()
        statement_id = f"DBMCP_{uuid4().hex[:24].upper()}"
        explain_sql = f"EXPLAIN PLAN SET STATEMENT_ID = :__db_mcp_statement_id FOR {sql}"
        try:
            engine = self._engine_factory(self.source)
            try:
                with engine.connect() as connection:
                    self._configure_timeout(connection)
                    connection.execute(text(explain_sql), {**parameters, "__db_mcp_statement_id": statement_id})
                    result = connection.execute(
                        text("SELECT plan_table_output FROM TABLE(DBMS_XPLAN.DISPLAY(NULL, :__db_mcp_statement_id, 'TYPICAL'))"),
                        {"__db_mcp_statement_id": statement_id},
                    )
                    columns = list(result.keys())
                    fetched = result.fetchmany(maximum + 1)
            finally:
                engine.dispose()
        except Exception as error:
            raise _safe_error(error, self.source) from None
        return self._format_result(columns, fetched, maximum, started, explain_sql)

    def _format_result(
        self,
        columns: list[str],
        fetched: list[tuple[Any, ...]],
        maximum: int,
        started: float,
        sql_for_audit: str,
    ) -> dict[str, object]:
        rows: list[dict[str, Any]] = []
        masked_columns: set[str] = set()
        bytes_used = len(json.dumps(columns, ensure_ascii=False).encode("utf-8"))
        truncated = len(fetched) > maximum
        for item in fetched[:maximum]:
            row = {}
            for column, value in zip(columns, item, strict=True):
                if _SENSITIVE_COLUMN.search(column):
                    row[column] = "***" if value is not None else None
                    if value is not None:
                        masked_columns.add(column)
                else:
                    row[column] = _json_value(value)
            encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if bytes_used + len(encoded) > self.source.query_limits.max_response_bytes:
                truncated = True
                break
            rows.append(row)
            bytes_used += len(encoded)
        response = {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "duration_ms": round((clock.perf_counter() - started) * 1000, 2),
            "masked_columns": sorted(masked_columns),
        }
        while rows and _response_bytes(response) > self.source.query_limits.max_response_bytes:
            rows.pop()
            response["row_count"] = len(rows)
            response["truncated"] = True
        if _response_bytes(response) > self.source.query_limits.max_response_bytes:
            response.update({"columns": [], "rows": [], "row_count": 0, "truncated": True})
        logger.info(
            "database readonly query completed data_source=%s dialect=%s sql_fingerprint=%s row_count=%s truncated=%s duration_ms=%s",
            self.source.alias,
            self.source.dialect,
            hashlib.sha256(sql_for_audit.encode("utf-8")).hexdigest(),
            response["row_count"],
            response["truncated"],
            response["duration_ms"],
        )
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

    def _page_size(self, page_size: int | None) -> int:
        if page_size is None:
            return self.source.query_limits.max_rows
        return self._limit(page_size)

    def _paginate_sql(self, sql: str) -> str:
        if self.source.dialect == "oracle":
            return f"SELECT * FROM ({sql}) db_mcp_page OFFSET :__db_mcp_offset ROWS FETCH NEXT :__db_mcp_page_size ROWS ONLY"
        return f"SELECT * FROM ({sql}) AS db_mcp_page LIMIT :__db_mcp_page_size OFFSET :__db_mcp_offset"

    @staticmethod
    def _query_fingerprint(sql: str, parameters: dict[str, Any]) -> str:
        canonical = json.dumps({"sql": sql, "parameters": parameters}, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _encode_query_page_token(offset: int, fingerprint: str) -> str:
        payload = json.dumps({"offset": offset, "fingerprint": fingerprint}, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_query_page_token(page_token: str | None, fingerprint: str) -> int:
        if page_token is None:
            return 0
        if not isinstance(page_token, str) or not page_token.strip():
            raise DatabaseQueryError("page_token 必须是非空字符串", code="parameter_error")
        try:
            padded = page_token.strip() + "=" * (-len(page_token.strip()) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
            raise DatabaseQueryError("page_token 无效", code="parameter_error") from None
        offset = payload.get("offset") if isinstance(payload, dict) else None
        if payload.get("fingerprint") != fingerprint or not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise DatabaseQueryError("page_token 与查询不匹配", code="parameter_error")
        return offset

    @staticmethod
    def _object_types(value: list[str] | None) -> frozenset[str]:
        if value is None:
            return frozenset({"TABLE", "VIEW", "COLUMN", "FUNCTION", "PROCEDURE", "SYNONYM"})
        if not isinstance(value, list) or not value:
            raise DatabaseQueryError("object_types 必须是非空数组", code="parameter_error")
        normalized = frozenset(item.upper() for item in value if isinstance(item, str))
        if len(normalized) != len(value) or not normalized <= {"TABLE", "VIEW", "COLUMN", "FUNCTION", "PROCEDURE", "SYNONYM"}:
            raise DatabaseQueryError("object_types 仅支持 TABLE、VIEW、COLUMN、FUNCTION、PROCEDURE、SYNONYM", code="parameter_error")
        return normalized

    @staticmethod
    def _encode_table_page_token(schema: str, last_name: str) -> str:
        payload = json.dumps({"schema": schema, "last_name": last_name}, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_table_page_token(page_token: str | None, schema: str) -> str | None:
        if page_token is None:
            return None
        if not isinstance(page_token, str) or not page_token.strip():
            raise DatabaseQueryError("page_token 必须是非空字符串", code="parameter_error")
        try:
            padded = page_token.strip() + "=" * (-len(page_token.strip()) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
            raise DatabaseQueryError("page_token 无效", code="parameter_error") from None
        if not isinstance(payload, dict) or payload.get("schema") != schema or not isinstance(payload.get("last_name"), str):
            raise DatabaseQueryError("page_token 与 schema 不匹配", code="parameter_error")
        return payload["last_name"]

    @staticmethod
    def _inspector_optional(inspector: Any, method_name: str, table: str, *, schema: str) -> Any:
        method = getattr(inspector, method_name, None)
        if method is None:
            return None
        try:
            return method(table, schema=schema)
        except (AttributeError, NotImplementedError):
            return None

    def _schema(self, schema: str | None) -> str:
        selected = self.source.default_schema if schema is None else schema
        if not isinstance(selected, str) or selected not in self.source.allowed_schemas:
            raise DatabaseQueryError("schema 不在数据源白名单中")
        return selected

    def _require_oracle(self) -> None:
        if self.source.dialect != "oracle":
            raise DatabaseQueryError("该诊断能力仅支持 Oracle 数据源", code="unsupported_dialect")

    @staticmethod
    def _sql_id(value: str) -> str:
        if not isinstance(value, str) or not _ORACLE_SQL_ID.fullmatch(value.strip()):
            raise DatabaseQueryError("sql_id 必须是 13 位字母数字字符串", code="parameter_error")
        return value.strip().lower()


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


def _safe_error(error: Exception, source: DatabaseSource | None = None) -> DatabaseQueryError:
    failure = classify_database_error(error)
    logger.warning(
        "database query failed data_source=%s dialect=%s code=%s vendor_code=%s exception_type=%s",
        source.alias if source else None,
        source.dialect if source else None,
        failure.code,
        failure.vendor_code,
        failure.exception_type,
    )
    return DatabaseQueryError(failure.message, code=failure.code)


def _response_bytes(value: dict[str, object]) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
