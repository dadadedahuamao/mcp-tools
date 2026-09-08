"""向 Codex 暴露预注册数据源的只读诊断工具。"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from db_mcp.client import DatabaseQueryError, DatabaseReader
from db_mcp.config import DatabaseConfig, DatabaseConfigurationError, load_source
from db_mcp.query_tasks import QueryTaskManager


_ERROR_DETAILS: dict[str, tuple[str, str, bool, str]] = {
    "parameter_error": ("INVALID_ARGUMENT", "DBMCP-40001", False, "请检查工具参数和数据源白名单。"),
    "unsupported_dialect": ("UNSUPPORTED_DIALECT", "DBMCP-40002", False, "请使用支持该能力的数据库类型，或调用 get_capabilities。"),
    "sql_syntax_error": ("SQL_REJECTED", "DBMCP-40003", False, "请检查 SQL 是否符合目标数据库方言和只读限制。"),
    "object_not_found": ("OBJECT_NOT_FOUND", "DBMCP-40401", False, "请检查对象名称、schema 白名单和诊断账号可见范围。"),
    "permission_denied": ("PERMISSION_DENIED", "DBMCP-40301", False, "请为诊断账号授予受控的只读系统视图权限。"),
    "timeout_error": ("QUERY_TIMEOUT", "DBMCP-40801", True, "请缩小查询范围，或由管理员调整受控超时配置。"),
    "query_error": ("DATABASE_ERROR", "DBMCP-50001", False, "请记录 request_id 并检查数据库服务端日志。"),
    "data_source_not_found": ("DATA_SOURCE_NOT_FOUND", "DBMCP-40402", False, "请调用 list_data_sources 并使用其中的正式别名。"),
    "data_source_ambiguous": ("INVALID_ARGUMENT", "DBMCP-40004", False, "数据源别名不唯一，请使用 list_data_sources 中的正式别名。"),
}


def tool_error_response(error: Exception, *, request_id: str, elapsed_ms: float) -> dict[str, object]:
    """将服务端异常转为不暴露连接串、参数或驱动细节的统一错误对象。"""

    code = error.code if isinstance(error, DatabaseQueryError) else "internal_error"
    if isinstance(error, DatabaseConfigurationError):
        message = str(error)
        if message.startswith("未找到预注册数据源"):
            code = "data_source_not_found"
        elif "匹配不唯一" in message:
            code = "data_source_ambiguous"
        else:
            code = "parameter_error"
    category, public_code, retryable, suggestion = _ERROR_DETAILS.get(
        code,
        ("INTERNAL_ERROR", "DBMCP-50000", False, "请记录 request_id 并联系 MCP 服务维护人员。"),
    )
    public_message = str(error) if isinstance(error, (DatabaseQueryError, DatabaseConfigurationError)) else "数据库 MCP 工具执行失败"
    return {
        "success": False,
        "error": {
            "category": category,
            "code": public_code,
            "database_code": None,
            "message": public_message,
            "retryable": retryable,
            "suggestion": suggestion,
        },
        "request_id": request_id,
        "elapsed_ms": elapsed_ms,
    }


def create_server(
    config: DatabaseConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 18000,
    allowed_hosts: list[str] | None = None,
) -> FastMCP:
    """创建 MCP；调用者只能使用配置中已有的数据源别名。"""

    server = FastMCP(
        "database-readonly",
        instructions="仅查询预注册数据库的数据和诊断信息。所有操作均为受限只读，不接受运行时连接串或凭据。",
        host=host,
        port=port,
        transport_security=(TransportSecuritySettings(allowed_hosts=allowed_hosts) if allowed_hosts else None),
    )
    query_tasks = QueryTaskManager()

    def reader(alias: str) -> DatabaseReader:
        return DatabaseReader(load_source(config, alias))

    def invoke(operation: Any) -> Any:
        request_id = uuid4().hex
        started = time.perf_counter()
        try:
            result = operation()
            if isinstance(result, dict):
                return {
                    **result,
                    "request_id": request_id,
                    "request_elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            return result
        except DatabaseQueryError as error:
            return tool_error_response(error, request_id=request_id, elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
        except DatabaseConfigurationError as error:
            return tool_error_response(error, request_id=request_id, elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
        except ValueError as error:
            return tool_error_response(
                DatabaseQueryError(str(error), code="parameter_error"),
                request_id=request_id,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
            )

    @server.tool(name="list_data_sources", description="列出预注册数据源的非敏感摘要。")
    def list_data_sources() -> list[dict[str, object]]:
        return [config.sources[alias].safe_summary() for alias in sorted(config.sources)]

    @server.tool(name="test_connection", description="使用预注册数据源验证只读连接是否可用。")
    def test_connection(data_source: str) -> dict[str, object]:
        return invoke(lambda: reader(data_source).test_connection())

    @server.tool(name="list_schemas", description="列出该数据源允许访问的 schema 白名单。")
    def list_schemas(data_source: str) -> list[str]:
        return invoke(lambda: reader(data_source).list_schemas())

    @server.tool(name="list_tables", description="分页列出允许 schema 中的表名；不会读取表数据，并返回截断标记和下一页游标。")
    def list_tables(
        data_source: str,
        schema: str | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, object]:
        return invoke(lambda: reader(data_source).list_tables(schema, page_size, page_token))

    @server.tool(name="describe_table", description="读取指定白名单 schema 中表的字段、注释和约束定义。")
    def describe_table(data_source: str, table: str, schema: str | None = None) -> dict[str, object]:
        return invoke(lambda: reader(data_source).describe_table(table, schema))

    @server.tool(name="list_indexes", description="Oracle/PostgreSQL/MySQL：列出白名单 schema 中表的索引、索引列和定义。")
    def list_indexes(
        data_source: str,
        schema: str | None = None,
        table: str | None = None,
        limit: int | None = None,
    ) -> dict[str, object]:
        return invoke(lambda: reader(data_source).list_indexes(schema, table, limit))

    @server.tool(name="query", description="按预注册数据源方言适配并执行单条参数化只读 SELECT/CTE；强制 schema、行数、字节和超时限制。分页时必须提供稳定 ORDER BY。")
    def query(
        data_source: str,
        sql: str,
        parameters: dict[str, Any] | None = None,
        limit: int | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, object]:
        return invoke(lambda: reader(data_source).query(sql, parameters, limit, page_size, page_token))

    @server.tool(name="submit_query", description="异步提交单条参数化只读 SELECT/CTE，返回 query_id；任务不保存 SQL 或参数。")
    def submit_query(
        data_source: str,
        sql: str,
        parameters: dict[str, Any] | None = None,
        limit: int | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, object]:
        return invoke(lambda: query_tasks.submit(data_source, lambda: reader(data_source).query(sql, parameters, limit, page_size, page_token)))

    @server.tool(name="get_query_status", description="查询异步只读任务的状态；状态包括 queued、running、completed、failed、cancelled。")
    def get_query_status(query_id: str) -> dict[str, object]:
        return invoke(lambda: query_tasks.get_status(query_id))

    @server.tool(name="get_query_result", description="读取已完成异步只读任务的结果；未完成任务不会返回部分业务数据。")
    def get_query_result(query_id: str) -> dict[str, object]:
        return invoke(lambda: query_tasks.get_result(query_id))

    @server.tool(name="cancel_query", description="取消尚未开始的异步只读任务；已运行任务会明确返回无法安全中断的原因。")
    def cancel_query(query_id: str) -> dict[str, object]:
        return invoke(lambda: query_tasks.cancel(query_id))

    @server.tool(name="explain_query", description="获取受控执行计划：PostgreSQL/MySQL 使用 estimate；Oracle 使用 cursor 并传 sql_id；生产环境不允许 analyze。")
    def explain_query(
        data_source: str,
        sql: str = "",
        parameters: dict[str, Any] | None = None,
        sql_id: str | None = None,
        mode: str = "estimate",
    ) -> dict[str, object]:
        return invoke(lambda: reader(data_source).explain_query(sql, parameters, sql_id, mode))

    @server.tool(name="search_sql", description="Oracle：受限检索近一天或指定 SQL ID 的游标性能摘要；不返回 SQL 文本。")
    def search_sql(data_source: str, sql_id: str | None = None, limit: int | None = None) -> dict[str, object]:
        return invoke(lambda: reader(data_source).search_sql(sql_id, limit))

    @server.tool(name="get_sql_detail", description="Oracle：按 SQL ID 查询 SQL 文本和 child cursor 统计。")
    def get_sql_detail(data_source: str, sql_id: str) -> dict[str, object]:
        return invoke(lambda: reader(data_source).get_sql_detail(sql_id))

    @server.tool(name="get_object_health", description="查看白名单 schema 的表、索引统计；Oracle 额外返回无效对象。")
    def get_object_health(data_source: str, schema: str | None = None, limit: int | None = None) -> dict[str, object]:
        return invoke(lambda: reader(data_source).get_object_health(schema, limit))

    @server.tool(name="search_slow_queries", description="查询慢 SQL 指纹性能摘要，不返回 SQL 全文或绑定参数。")
    def search_slow_queries(data_source: str, limit: int | None = None) -> dict[str, object]:
        return invoke(lambda: reader(data_source).search_slow_queries(limit))

    @server.tool(name="get_active_sessions", description="限量查看数据库活动会话摘要；实际可见范围由只读账号权限决定。")
    def get_active_sessions(data_source: str, limit: int | None = None) -> dict[str, object]:
        return invoke(lambda: reader(data_source).get_active_sessions(limit))

    @server.tool(name="get_lock_summary", description="限量查看数据库锁摘要；实际可见范围由只读账号权限决定。")
    def get_lock_summary(data_source: str, limit: int | None = None) -> dict[str, object]:
        return invoke(lambda: reader(data_source).get_lock_summary(limit))

    @server.tool(name="get_lock_tree", description="限量返回等待会话与阻塞会话的锁关系；不提供终止会话能力。")
    def get_lock_tree(data_source: str, limit: int | None = None) -> dict[str, object]:
        return invoke(lambda: reader(data_source).get_lock_tree(limit))

    @server.tool(name="get_capabilities", description="返回数据源按数据库方言和当前 MCP 实现可支持的诊断能力。")
    def get_capabilities(data_source: str) -> dict[str, object]:
        return invoke(lambda: reader(data_source).get_capabilities())

    @server.tool(name="search_objects", description="按关键词检索白名单 schema 中的表、视图、字段、函数、存储过程和同义词；不读取业务表数据。")
    def search_objects(
        data_source: str,
        keyword: str,
        object_types: list[str] | None = None,
        schema: str | None = None,
        limit: int | None = None,
    ) -> dict[str, object]:
        return invoke(lambda: reader(data_source).search_objects(keyword, object_types, schema, limit))

    @server.tool(name="get_database_health", description="查看数据库版本、当前身份、会话数和 MCP 只读限制摘要。")
    def get_database_health(data_source: str) -> dict[str, object]:
        return invoke(lambda: reader(data_source).get_database_health())


    return server
