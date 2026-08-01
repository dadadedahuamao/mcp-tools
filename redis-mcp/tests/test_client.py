from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from redis_mcp.client import inspect_key, scan_keys


class FakeRedis:
    def __init__(self) -> None:
        self.scan_calls: list[tuple] = []

    def scan(self, cursor: int, match: str | None, count: int):
        self.scan_calls.append((cursor, match, count))
        return 9, [b"one", b"two"]

    def type(self, key: str): return b"string"
    def ttl(self, key: str): return 60
    def memory_usage(self, key: str): return 3000
    def get(self, key: str): return b"x" * 3000


def test_scan_keys_uses_scan_and_caps_count() -> None:
    client = FakeRedis()
    result = scan_keys(client, cursor=0, match="order:*", count=9999)
    assert client.scan_calls == [(0, "order:*", 200)]
    assert result == {"next_cursor": 9, "keys": ["one", "two"]}


def test_inspect_string_truncates_value() -> None:
    result = inspect_key(FakeRedis(), "demo")
    assert result["value"]["truncated"] is True
    assert len(result["value"]["data"]) == 2048
