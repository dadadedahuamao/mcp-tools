from __future__ import annotations

from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from db_mcp.client import DatabaseQueryError, DatabaseReader, _json_value
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


def test_binary_value_is_labelled_and_encoded_as_base64() -> None:
    assert _json_value(b"abc") == {"encoding": "base64", "value": "YWJj"}


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
