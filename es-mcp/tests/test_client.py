from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from es_mcp.client import (
    _get_index_pattern,
    _safe_error,
    _validate_index_access,
    get_shard_capacity,
)
from es_mcp.config import ElasticsearchEnvironment


def test_validate_index_access_with_exact_match() -> None:
    """测试精确匹配索引白名单。"""
    allowlist = frozenset({"*open_api_log*", "*third_api_log*"})
    assert _validate_index_access("*open_api_log*", allowlist) is True
    assert _validate_index_access("*third_api_log*", allowlist) is True
    assert _validate_index_access("*other_log*", allowlist) is False


def test_validate_index_access_with_wildcard() -> None:
    """测试通配符匹配索引白名单。"""
    allowlist = frozenset({"mes-api-*", "prod-*"})
    assert _validate_index_access("mes-api-2026.08", allowlist) is True
    assert _validate_index_access("prod-logs", allowlist) is True
    assert _validate_index_access("other-index", allowlist) is False


def test_get_index_pattern_openapi() -> None:
    """测试 OpenApi 索引模式。"""
    assert _get_index_pattern("openapi") == "*open_api_log*"


def test_get_index_pattern_thirdapi() -> None:
    """测试第三方接口索引模式。"""
    assert _get_index_pattern("thirdapi") == "*third_api_log*"


def test_get_index_pattern_invalid() -> None:
    """测试无效索引类型。"""
    try:
        _get_index_pattern("invalid")
        assert False, "应该抛出异常"
    except ValueError as e:
        assert "不支持的索引类型" in str(e)


def test_safe_error_connection() -> None:
    """测试连接错误处理。"""
    error = Exception("Connection refused")
    result = _safe_error(error)
    assert "连接超时或不可达" in result


def test_safe_error_auth() -> None:
    """测试认证错误处理。"""
    error = Exception("Authentication failed")
    result = _safe_error(error)
    assert "认证失败" in result


def test_safe_error_index_not_found() -> None:
    """测试索引不存在错误处理。"""
    error = Exception("index_not_found_exception")
    result = _safe_error(error)
    assert "索引不存在" in result


def test_safe_error_generic() -> None:
    """测试通用错误处理。"""
    error = Exception("Some unknown error")
    result = _safe_error(error)
    assert "查询失败" in result


def test_get_shard_capacity_returns_allowed_shards_and_node_disk_usage() -> None:
    """分片容量查询只读取白名单索引，并返回最小必要的分片与磁盘信息。"""
    class CatClient:
        def __init__(self) -> None:
            self.shard_index: str | None = None

        def shards(self, **kwargs: object) -> list[dict[str, str]]:
            self.shard_index = str(kwargs["index"])
            return [
                {
                    "index": "mes-open_api_log-2026.09.02",
                    "shard": "0",
                    "prirep": "p",
                    "state": "STARTED",
                    "docs": "42",
                    "store": "1048576",
                    "node": "es-node-1",
                }
            ]

        def allocation(self, **kwargs: object) -> list[dict[str, str]]:
            return [
                {
                    "node": "es-node-1",
                    "shards": "21",
                    "disk.indices": "10485760",
                    "disk.used": "20971520",
                    "disk.avail": "83886080",
                    "disk.total": "104857600",
                    "disk.percent": "20",
                }
            ]

    class Client:
        def __init__(self) -> None:
            self.cat = CatClient()

    config = ElasticsearchEnvironment(
        name="UAT环境",
        hosts=("http://es.example:9200",),
        username=None,
        password=None,
        index_allowlist=frozenset({"*open_api_log*", "*third_api_log*"}),
        timeout_seconds=30,
        max_result_window=10000,
        max_response_bytes=1048576,
    )

    client = Client()
    result = get_shard_capacity(client, config, "openapi")

    assert result["index_pattern"] == "*open_api_log*"
    assert result["summary"] == {
        "total_shards": 1,
        "primary_shards": 1,
        "replica_shards": 0,
        "unassigned_shards": 0,
    }
    assert result["shards"][0]["size_bytes"] == 1048576
    assert result["nodes"][0]["disk_percent"] == 20.0
    assert result["nodes"][0]["disk_total_bytes"] == 104857600
    assert result["nodes"][0]["disk_available_bytes"] == 83886080
    assert client.cat.shard_index == "*open_api_log*"
