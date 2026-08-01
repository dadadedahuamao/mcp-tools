from __future__ import annotations

import asyncio
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from db_mcp.config import DatabaseConfig
from db_mcp.server import create_server


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
        "query",
        "explain_query",
        "get_active_sessions",
        "get_lock_summary",
    }
