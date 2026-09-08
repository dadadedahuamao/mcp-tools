from pathlib import Path
import sys
import tempfile

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from es_mcp.server import create_server


@pytest.fixture
def temp_dir():
    """创建临时目录。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def server(temp_dir: Path) -> None:
    """创建测试用 server 实例。"""
    env_file = temp_dir / "test_env.yaml"
    env_file.write_text("env: []", encoding="utf-8")
    return create_server(env_file)


def test_server_name(server: object) -> None:
    """测试 server 名称。"""
    assert server.name == "elasticsearch"


def test_server_has_instructions(server: object) -> None:
    """测试 server 包含说明。"""
    assert "Elasticsearch" in server.instructions


def test_server_tools_registered(temp_dir: Path) -> None:
    """测试工具注册。"""
    import asyncio

    env_file = temp_dir / "test_env.yaml"
    env_file.write_text("env: []", encoding="utf-8")
    server = create_server(env_file)
    tools = asyncio.run(server.list_tools())
    tool_names = [tool.name for tool in tools]

    expected_tools = [
        "list_es_environments",
        "search_api_logs",
        "get_api_log_detail",
        "get_index_stats",
        "get_api_statistics",
        "get_slow_apis",
        "get_error_apis",
        "analyze_api_trend",
        "get_api_distribution",
        "search_error_logs",
        "analyze_slow_requests",
        "get_shard_capacity",
        "get_cluster_health",
        "get_node_health",
        "get_shard_allocation",
        "get_index_mapping",
        "get_index_settings",
        "get_index_aliases",
        "get_index_retention",
        "get_index_templates",
        "compare_api_periods",
        "get_api_latency_percentiles",
        "get_api_error_samples",
        "trace_api_request",
        "get_api_topology",
    ]

    for tool_name in expected_tools:
        assert tool_name in tool_names, f"工具 {tool_name} 未注册"


def test_get_index_stats_tool_delegates_to_client_implementation(
    temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MCP 工具应调用客户端统计函数，而不是递归调用同名工具函数。"""
    import asyncio
    import es_mcp.server as server_module

    env_file = temp_dir / "test_env.yaml"
    env_file.write_text(
        """env:
  - env_name: UAT环境
    es:
      hosts: [http://es.example:9200]
      index_allowlist: ['*open_api_log*']
""",
        encoding="utf-8",
    )

    class FakeClient:
        def close(self) -> None:
            pass

    monkeypatch.setattr(server_module, "create_client", lambda config: FakeClient())
    monkeypatch.setattr(
        server_module,
        "client_get_index_stats",
        lambda client, config, index_type: {"index_type": index_type, "total_docs": 42},
    )

    server = create_server(env_file)
    result = asyncio.run(
        server.call_tool(
            "get_index_stats", {"environment": "UAT环境", "index_type": "openapi"}
        )
    )

    import json

    payload = json.loads(result[0].text)
    assert payload["index_type"] == "openapi"
    assert payload["total_docs"] == 42
