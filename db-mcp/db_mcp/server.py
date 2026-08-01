"""向 Codex 暴露预注册数据源的只读诊断工具。"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from db_mcp.client import DatabaseQueryError, DatabaseReader
from db_mcp.config import DatabaseConfig, DatabaseConfigurationError, load_source


def create_server(
    config: DatabaseConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
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

    def reader(alias: str) -> DatabaseReader:
        try:
            return DatabaseReader(load_source(config, alias))
        except DatabaseConfigurationError as error:
            raise ValueError(str(error)) from None

    def invoke(operation: Any) -> Any:
        try:
            return operation()
        except (DatabaseQueryError, DatabaseConfigurationError) as error:
            raise ValueError(str(error)) from None

    @server.tool(name="list_data_sources", description="列出预注册数据源的非敏感摘要。")
    def list_data_sources() -> list[dict[str, object]]:
        return [config.sources[alias].safe_summary() for alias in sorted(config.sources)]

    @server.tool(name="test_connection", description="使用预注册数据源验证只读连接是否可用。")
    def test_connection(data_source: str) -> dict[str, object]:
        return invoke(lambda: reader(data_source).test_connection())

    @server.tool(name="list_schemas", description="列出该数据源允许访问的 schema 白名单。")
    def list_schemas(data_source: str) -> list[str]:
        return invoke(lambda: reader(data_source).list_schemas())

    @server.tool(name="list_tables", description="列出允许 schema 中的表名；不会读取表数据。")
    def list_tables(data_source: str, schema: str | None = None) -> list[dict[str, object]]:
        return invoke(lambda: reader(data_source).list_tables(schema))

    @server.tool(name="describe_table", description="读取指定白名单 schema 中表的列定义。")
    def describe_table(data_source: str, table: str, schema: str | None = None) -> dict[str, object]:
        return invoke(lambda: reader(data_source).describe_table(table, schema))

    @server.tool(name="query", description="执行单条参数化只读 SELECT/CTE 查询；强制 schema、行数、字节和超时限制。")
    def query(data_source: str, sql: str, parameters: dict[str, Any] | None = None, limit: int | None = None) -> dict[str, object]:
        return invoke(lambda: reader(data_source).query(sql, parameters, limit))

    @server.tool(name="explain_query", description="查询已校验 SQL 的执行计划；Oracle 需传入可访问游标的 sql_id。")
    def explain_query(data_source: str, sql: str = "", parameters: dict[str, Any] | None = None, sql_id: str | None = None) -> dict[str, object]:
        return invoke(lambda: reader(data_source).explain_query(sql, parameters, sql_id))

    @server.tool(name="get_active_sessions", description="限量查看数据库活动会话摘要；实际可见范围由只读账号权限决定。")
    def get_active_sessions(data_source: str, limit: int | None = None) -> dict[str, object]:
        return invoke(lambda: reader(data_source).get_active_sessions(limit))

    @server.tool(name="get_lock_summary", description="限量查看数据库锁摘要；实际可见范围由只读账号权限决定。")
    def get_lock_summary(data_source: str, limit: int | None = None) -> dict[str, object]:
        return invoke(lambda: reader(data_source).get_lock_summary(limit))

    return server
