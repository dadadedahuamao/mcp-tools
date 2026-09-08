from __future__ import annotations

import asyncio
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from db_mcp.config import DatabaseConfig, DatabaseSource, QueryLimits
from db_mcp.server import create_server, tool_error_response


def test_server_describes_readonly_database_boundary() -> None:
    server = create_server(DatabaseConfig(sources={}))

    assert server.name == "database-readonly"
    assert "只读" in server.instructions


def test_server_accepts_explicit_http_listener_settings() -> None:
    server = create_server(DatabaseConfig(sources={}), host="0.0.0.0", port=9000)

    assert server.settings.host == "0.0.0.0"
    assert server.settings.port == 9000


def test_server_registers_all_readonly_tools() -> None:
    server = create_server(DatabaseConfig(sources={}))

    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == {
        "list_data_sources",
        "test_connection",
        "list_schemas",
        "list_tables",
        "describe_table",
        "list_indexes",
        "query",
        "explain_query",
        "search_sql",
        "get_sql_detail",
        "get_object_health",
        "get_active_sessions",
        "get_lock_summary",
        "get_lock_tree",
        "get_capabilities",
        "search_objects",
        "get_database_health",
        "submit_query",
        "get_query_status",
        "get_query_result",
        "cancel_query",
        "search_slow_queries",
    }

    index_tool = next(tool for tool in tools if tool.name == "list_indexes")
    assert "MySQL" in index_tool.description


def test_tool_error_response_is_machine_readable_and_does_not_echo_details() -> None:
    response = tool_error_response(ValueError("password=not-for-output"), request_id="request-123", elapsed_ms=12.3)

    assert response == {
        "success": False,
        "error": {
            "category": "INTERNAL_ERROR",
            "code": "DBMCP-50000",
            "database_code": None,
            "message": "数据库 MCP 工具执行失败",
            "retryable": False,
            "suggestion": "请记录 request_id 并联系 MCP 服务维护人员。",
        },
        "request_id": "request-123",
        "elapsed_ms": 12.3,
    }


def test_unknown_data_source_returns_data_source_not_found() -> None:
    server = create_server(DatabaseConfig(sources={}))

    _content, structured = asyncio.run(server.call_tool("get_capabilities", {"data_source": "missing"}))

    assert structured["error"]["category"] == "DATA_SOURCE_NOT_FOUND"
    assert structured["error"]["code"] == "DBMCP-40402"


def test_successful_tool_response_includes_request_tracking_fields() -> None:
    source = DatabaseSource(
        alias="reporting",
        dialect="postgresql",
        host="db.internal",
        port=5432,
        database="app",
        username="readonly",
        password_env=None,
        password="not-for-output",
        default_schema="public",
        allowed_schemas=frozenset({"public"}),
        tls=None,
        query_limits=QueryLimits(),
    )
    server = create_server(DatabaseConfig(sources={"reporting": source}))

    _content, structured = asyncio.run(server.call_tool("get_capabilities", {"data_source": "reporting"}))

    assert isinstance(structured["request_id"], str)
    assert structured["request_elapsed_ms"] >= 0
